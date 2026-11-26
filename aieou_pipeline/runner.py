"""Work through the pending jobs: fetch, save the raw response, record the outcome.

Safe to stop and restart at any time: finished jobs are never re-sent.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import pathlib
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .providers import get_provider


class RateLimiter:
    """Spaces request starts evenly so the hourly cap is never exceeded."""

    def __init__(self, per_hour: float):
        self.interval = 3600.0 / per_hour if per_hour else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        time.sleep(max(0.0, start - now))


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _raw_path(root: pathlib.Path, job: dict, suffix: str) -> pathlib.Path:
    case = job["case_id"]
    bucket = f"{int(case) // 1000:04d}" if case.isdigit() else "other"
    folder = root / "raw" / job["surface"] / bucket
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{case}_r{job['repeat']}{suffix}"


def _work(job, surface, provider, limiter, run_cfg, root, abort) -> dict:
    result, attempts, requested_at, interrupted = None, 0, None, False
    while attempts < run_cfg["max_attempts"]:
        if abort.is_set():
            break
        limiter.wait()
        attempts += 1
        requested_at = _now()
        result = provider.fetch(surface, job["user_prompt"], run_cfg["timeout_s"])
        if result.outcome in ("ok", "fail"):
            break
        if result.outcome == "abort":
            abort.set()
            break
        pause = result.retry_after_s or min(300, 5 * 2 ** (attempts - 1))
        if abort.wait(pause + random.uniform(0, 2)):
            interrupted = True
            break

    done = {"job": job, "attempts": attempts, "result": result,
            "requested_at": requested_at, "raw_path": None, "html_path": None,
            "interrupted": interrupted}
    if result is not None and result.outcome == "ok":
        raw = _raw_path(root, job, ".json.gz")
        text = provider.redact(json.dumps(result.payload, ensure_ascii=False))
        with gzip.open(raw, "wt", encoding="utf-8") as fh:
            fh.write(text)
        done["raw_path"] = str(raw.relative_to(root))
        if run_cfg["save_html"] and result.html_url:
            html = provider.fetch_html(result.html_url, run_cfg["timeout_s"])
            if html:
                page = _raw_path(root, job, ".html.gz")
                with gzip.open(page, "wt", encoding="utf-8") as fh:
                    fh.write(provider.redact(html))
                done["html_path"] = str(page.relative_to(root))
    return done


def _record(conn: sqlite3.Connection, surface: dict, done: dict) -> str:
    job, result = done["job"], done["result"]
    if result is None or done["interrupted"]:   # run stopped before this job finished
        return "skipped"
    if result.outcome == "ok":
        status = "done"
    elif result.outcome == "abort":
        status = "pending"                   # nothing wrong with the job itself
    else:
        status = "failed"
    with conn:
        conn.execute(
            "UPDATE jobs SET status=?, attempts=attempts+?, last_error=?, updated_at=? WHERE job_id=?",
            (status, done["attempts"], result.error, _now(), job["job_id"]))
        if status == "done":
            conn.execute(
                "INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job["job_id"], surface["provider"], surface["engine"], done["requested_at"],
                 result.latency_s, result.http_status, result.provider_search_id,
                 json.dumps(result.request_params, ensure_ascii=False),
                 done["raw_path"], done["html_path"]))
    return status if result.outcome != "abort" else "aborted"


def run(cfg: dict, conn: sqlite3.Connection, max_searches: int = 0,
        only_surfaces: list[str] | None = None, retry_failed: bool = False) -> None:
    run_cfg, root = cfg["run"], pathlib.Path(cfg["run"]["data_dir"])
    if retry_failed:
        with conn:
            conn.execute("UPDATE jobs SET status='pending' WHERE status='failed'")

    # Ordered so that one prompt's surfaces are queried close together in time.
    jobs = [dict(r) for r in conn.execute(
        """SELECT j.job_id, j.case_id, j.surface, j.repeat, p.user_prompt
           FROM jobs j JOIN prompts p USING (case_id)
           WHERE j.status='pending'
           ORDER BY j.repeat, CAST(j.case_id AS INTEGER), j.case_id, j.surface""")]
    jobs = [j for j in jobs if j["surface"] in cfg["surfaces"]
            and (not only_surfaces or j["surface"] in only_surfaces)]
    if max_searches:
        jobs = jobs[:max_searches]
    if not jobs:
        print("Nothing to do: no pending jobs. Run `plan` first, or check `status`.")
        return

    providers, limiters = {}, {}
    for name in {cfg["surfaces"][j["surface"]]["provider"] for j in jobs}:
        providers[name] = get_provider(name, cfg)
        providers[name].check_ready()
        per_hour = 0 if cfg["dry_run"] else cfg["providers"].get(name, {}).get("searches_per_hour", 0)
        limiters[name] = RateLimiter(per_hour)

    label = "DRY RUN (mock data, no API calls)" if cfg["dry_run"] else "LIVE"
    print(f"{label}: {len(jobs)} searches, {run_cfg['workers']} workers")
    abort, counts, started = threading.Event(), {}, time.monotonic()
    last_abort_error = None

    with ThreadPoolExecutor(max_workers=run_cfg["workers"]) as pool:
        futures = []
        for job in jobs:
            surface = cfg["surfaces"][job["surface"]]
            futures.append(pool.submit(_work, job, surface, providers[surface["provider"]],
                                       limiters[surface["provider"]], run_cfg, root, abort))
        for n, future in enumerate(as_completed(futures), start=1):
            done = future.result()
            outcome = _record(conn, cfg["surfaces"][done["job"]["surface"]], done)
            counts[outcome] = counts.get(outcome, 0) + 1
            if outcome == "aborted":
                last_abort_error = done["result"].error
            if n % 25 == 0 or n == len(jobs):
                elapsed = time.monotonic() - started
                print(f"  {n}/{len(jobs)}  {counts}  {elapsed:.0f}s")

    if abort.is_set():
        print(f"STOPPED EARLY: {last_abort_error}")
        print("Unfinished jobs are still pending. Fix the problem and run again.")
    if counts.get("failed"):
        print("Some jobs failed. See `status`; retry them with `run --retry-failed`.")
