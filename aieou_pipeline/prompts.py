"""Load the prompt spreadsheet and choose which prompts to run."""
from __future__ import annotations

import json
import os
import random
import sqlite3

import openpyxl

from .db import case_sort_key


def load_xlsx(conn: sqlite3.Connection, cfg: dict) -> int:
    """Copy the spreadsheet into the prompts table (skipped if unchanged)."""
    inp = cfg["input"]
    path = inp["path"]
    stat = os.stat(path)
    signature = f"{path}|{stat.st_size}|{int(stat.st_mtime)}"
    row = conn.execute("SELECT value FROM kv WHERE key='input_signature'").fetchone()
    if row and row["value"] == signature:
        return conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[inp["sheet"]] if inp["sheet"] else wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows)]
    for needed in (inp["id_column"], inp["prompt_column"]):
        if needed not in header:
            raise SystemExit(f"input: column '{needed}' not found. Columns: {header}")
    id_idx = header.index(inp["id_column"])
    prompt_idx = header.index(inp["prompt_column"])

    batch = []
    for values in rows:
        if values is None or all(v is None for v in values):
            continue
        prompt = values[prompt_idx]
        if prompt is None or not str(prompt).strip():
            continue
        meta = {
            header[i]: values[i]
            for i in range(min(len(header), len(values)))
            if i not in (id_idx, prompt_idx) and header[i]
        }
        batch.append((str(values[id_idx]).strip(), str(prompt).strip(),
                      json.dumps(meta, ensure_ascii=False)))
    wb.close()

    with conn:
        conn.execute("DELETE FROM prompts")
        conn.executemany("INSERT OR REPLACE INTO prompts VALUES (?,?,?)", batch)
        conn.execute("INSERT OR REPLACE INTO kv VALUES ('input_signature', ?)", (signature,))
    return len(batch)


def select_cases(conn: sqlite3.Connection, sample: dict) -> list[str]:
    """Return the case ids chosen by the [sample] section of the config."""
    rows = [(r["case_id"], json.loads(r["meta_json"]))
            for r in conn.execute("SELECT case_id, meta_json FROM prompts")]
    rows.sort(key=lambda r: case_sort_key(r[0]))

    filters = {k: {str(v) for v in vals} for k, vals in (sample.get("filter") or {}).items()}

    def passes(meta: dict) -> bool:
        # In a filter, "" stands for a blank cell.
        return all(str(meta.get(col) or "") in allowed for col, allowed in filters.items())

    rows = [r for r in rows if passes(r[1])]
    demo = sample["demographic_columns"]

    def is_baseline(meta: dict) -> bool:
        return all(not meta.get(col) for col in demo)

    mode = sample["mode"]
    if mode == "all":
        chosen = [cid for cid, _ in rows]
    elif mode == "baseline":
        chosen = [cid for cid, meta in rows if is_baseline(meta)]
    elif mode == "stratified":
        rng = random.Random(sample["seed"])
        chosen, groups = [], {}
        for cid, meta in rows:
            if is_baseline(meta):
                chosen.append(cid)
            else:
                key = tuple(meta.get(c) for c in sample["pair_columns"])
                groups.setdefault(key, []).append(cid)
        for key in sorted(groups, key=str):
            members = groups[key]
            chosen.extend(rng.sample(members, min(sample["n_per_pair"], len(members))))
    else:
        raise SystemExit(f"sample.mode must be baseline, stratified or all (got '{mode}')")

    chosen.sort(key=case_sort_key)
    if sample.get("limit"):
        chosen = chosen[: sample["limit"]]
    return chosen
