"""Write analysis-ready CSV files: one row per answer, one row per result item."""
from __future__ import annotations

import csv
import json
import pathlib
import sqlite3


def export(cfg: dict, conn: sqlite3.Connection) -> None:
    out_dir = pathlib.Path(cfg["run"]["data_dir"]) / "export"
    out_dir.mkdir(parents=True, exist_ok=True)

    prompts = {r["case_id"]: (r["user_prompt"], json.loads(r["meta_json"]))
               for r in conn.execute(
                   "SELECT * FROM prompts WHERE case_id IN (SELECT case_id FROM jobs)")}
    meta_cols = []
    for _, meta in prompts.values():
        for key in meta:
            if key not in meta_cols:
                meta_cols.append(key)

    answers = conn.execute(
        """SELECT j.job_id, j.surface, j.case_id, j.repeat, r.provider, r.engine,
                  r.requested_at, a.answer_text, a.n_organic, a.n_ads, a.n_references,
                  a.flags_json, r.raw_path, r.html_path
           FROM answers a JOIN jobs j USING (job_id) JOIN responses r USING (job_id)
           ORDER BY CAST(j.case_id AS INTEGER), j.case_id, j.surface, j.repeat""").fetchall()
    head = (["job_id", "surface", "case_id", "repeat"] + meta_cols +
            ["user_prompt", "provider", "engine", "requested_at", "answer_text",
             "n_organic", "n_ads", "n_references", "flags", "raw_path", "html_path"])
    # utf-8-sig so Excel opens the file with the right encoding.
    with open(out_dir / "answers.csv", "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(head)
        for a in answers:
            prompt, meta = prompts[a["case_id"]]
            writer.writerow([a["job_id"], a["surface"], a["case_id"], a["repeat"]] +
                            [meta.get(c) for c in meta_cols] +
                            [prompt, a["provider"], a["engine"], a["requested_at"],
                             a["answer_text"], a["n_organic"], a["n_ads"], a["n_references"],
                             a["flags_json"], a["raw_path"], a["html_path"]])

    items = conn.execute(
        """SELECT j.job_id, j.surface, j.case_id, j.repeat, i.kind, i.position, i.title,
                  i.link, i.domain, i.snippet, i.source
           FROM items i JOIN jobs j USING (job_id)
           ORDER BY CAST(j.case_id AS INTEGER), j.case_id, j.surface, j.repeat, i.kind, i.position""").fetchall()
    with open(out_dir / "items.csv", "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["job_id", "surface", "case_id", "repeat"] + meta_cols +
                        ["kind", "position", "title", "link", "domain", "snippet", "source"])
        for i in items:
            meta = prompts[i["case_id"]][1]
            writer.writerow([i["job_id"], i["surface"], i["case_id"], i["repeat"]] +
                            [meta.get(c) for c in meta_cols] +
                            [i["kind"], i["position"], i["title"], i["link"], i["domain"],
                             i["snippet"], i["source"]])

    print(f"Wrote {len(answers)} answers and {len(items)} items to {out_dir}")
