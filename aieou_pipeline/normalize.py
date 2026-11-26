"""Turn saved raw responses into the provider-neutral answers and items tables.

Reads only files on disk, so it costs nothing and can be re-run whenever the
parsing rules change.
"""
from __future__ import annotations

import gzip
import json
import pathlib
import sqlite3

from .providers import get_provider


def normalize(cfg: dict, conn: sqlite3.Connection, rebuild: bool = False) -> None:
    root = pathlib.Path(cfg["run"]["data_dir"])
    if rebuild:
        with conn:
            conn.execute("DELETE FROM answers")
            conn.execute("DELETE FROM items")

    rows = conn.execute(
        """SELECT j.job_id, j.surface, r.raw_path
           FROM jobs j JOIN responses r USING (job_id)
           WHERE j.status='done' AND j.job_id NOT IN (SELECT job_id FROM answers)""").fetchall()
    providers, written, skipped = {}, 0, 0
    for row in rows:
        surface = cfg["surfaces"].get(row["surface"])
        if surface is None or not row["raw_path"]:
            skipped += 1
            continue
        name = surface["provider"]
        if name not in providers:
            providers[name] = get_provider(name, cfg)
        with gzip.open(root / row["raw_path"], "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
        norm = providers[name].normalize(surface, payload)
        kinds = [i["kind"] for i in norm.items]
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO answers VALUES (?,?,?,?,?,?)",
                (row["job_id"], norm.answer_text, kinds.count("organic"), kinds.count("ad"),
                 kinds.count("reference") + kinds.count("ai_overview_reference"),
                 json.dumps(norm.flags, ensure_ascii=False)))
            conn.execute("DELETE FROM items WHERE job_id=?", (row["job_id"],))
            conn.executemany(
                "INSERT INTO items VALUES (?,?,?,?,?,?,?,?)",
                [(row["job_id"], i["kind"], i["position"], i["title"], i["link"],
                  i["domain"], i["snippet"], i["source"]) for i in norm.items])
        written += 1
    print(f"Normalized {written} responses" + (f", skipped {skipped}" if skipped else ""))
