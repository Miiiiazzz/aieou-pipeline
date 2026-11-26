"""SQLite state: prompts, the job queue, and normalized results."""
from __future__ import annotations

import pathlib
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS prompts (
    case_id     TEXT PRIMARY KEY,
    user_prompt TEXT NOT NULL,
    meta_json   TEXT NOT NULL          -- every other column of the input row
);

-- One job = one prompt sent to one surface, once.
CREATE TABLE IF NOT EXISTS jobs (
    job_id     TEXT PRIMARY KEY,       -- surface|case_id|repeat
    case_id    TEXT NOT NULL,
    surface    TEXT NOT NULL,
    repeat     INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);

-- What was actually sent and what came back (one row per finished job).
CREATE TABLE IF NOT EXISTS responses (
    job_id             TEXT PRIMARY KEY,
    provider           TEXT,
    engine             TEXT,
    requested_at       TEXT,           -- UTC, ISO 8601
    latency_s          REAL,
    http_status        INTEGER,
    provider_search_id TEXT,
    params_json        TEXT,           -- request parameters, no API key
    raw_path           TEXT,           -- verbatim provider JSON (gzip)
    html_path          TEXT            -- raw results page, when saved
);

-- Provider-neutral view, rebuilt from the raw files by `normalize`.
CREATE TABLE IF NOT EXISTS answers (
    job_id      TEXT PRIMARY KEY,
    answer_text TEXT,
    n_organic   INTEGER,
    n_ads       INTEGER,
    n_references INTEGER,
    flags_json  TEXT
);
CREATE TABLE IF NOT EXISTS items (
    job_id   TEXT NOT NULL,
    kind     TEXT NOT NULL,   -- organic | ad | reference | shopping | ...
    position INTEGER,
    title    TEXT,
    link     TEXT,
    domain   TEXT,
    snippet  TEXT,
    source   TEXT
);
CREATE INDEX IF NOT EXISTS items_job ON items(job_id);
"""


def connect(data_dir: str) -> sqlite3.Connection:
    root = pathlib.Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "pipeline.sqlite")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def case_sort_key(case_id: str):
    return (0, int(case_id), "") if case_id.isdigit() else (1, 0, case_id)
