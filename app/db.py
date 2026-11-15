"""SQLite persistence layer.

The platform is a single-process MVP: one shared connection guarded by a
re-entrant lock is enough and keeps the async engine free of thread pools.
WAL mode allows readers while the engine writes results.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "eval.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT 'v1',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    "input" TEXT NOT NULL,
    expected TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_dataset ON cases(dataset_id, active);

CREATE TABLE IF NOT EXISTS targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    config TEXT NOT NULL DEFAULT '{}',
    metrics TEXT NOT NULL DEFAULT '{}',
    total INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT,
    ended_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_dataset ON runs(dataset_id);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    case_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    latency_ms REAL NOT NULL DEFAULT 0,
    steps INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 1,
    output TEXT NOT NULL DEFAULT '',
    error TEXT,
    error_type TEXT,
    judge TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_results_run ON results(run_id, status);
CREATE INDEX IF NOT EXISTS idx_results_case ON results(run_id, case_id);
"""

_conn = None
_conn_path = None
_lock = threading.RLock()


def resolve_db_path():
    """Return the database path, honouring the EVAL_DB_PATH override."""
    override = os.environ.get("EVAL_DB_PATH")
    if override:
        return Path(override).expanduser()
    return DEFAULT_DB_PATH


def now_iso():
    """UTC timestamp with a trailing Z, millisecond precision."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def get_conn():
    global _conn, _conn_path
    path = str(resolve_db_path())
    with _lock:
        if _conn is None or _conn_path != path:
            if _conn is not None:
                _conn.close()
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn_path = path
        return _conn


def init_db():
    with _lock:
        conn = get_conn()
        conn.executescript(SCHEMA)
        conn.commit()


def reset_db():
    """Delete the database file (used by seed.py --reset)."""
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
            _conn_path = None
        path = resolve_db_path()
        for suffix in ("", "-wal", "-shm"):
            target = Path(str(path) + suffix)
            if target.exists():
                target.unlink()
        init_db()


def query(sql, params=()):
    with _lock:
        cur = get_conn().execute(sql, params)
        rows = cur.fetchall()
        return [dict(row) for row in rows]


def query_one(sql, params=()):
    with _lock:
        cur = get_conn().execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def scalar(sql, params=()):
    with _lock:
        cur = get_conn().execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


def execute(sql, params=()):
    with _lock:
        conn = get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def executemany(sql, seq):
    with _lock:
        conn = get_conn()
        cur = conn.executemany(sql, seq)
        conn.commit()
        return cur.rowcount


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


def loads(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default
