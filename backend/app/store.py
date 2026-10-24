"""SQLite call log: every panel event and the final incident report. No audio is ever stored."""

import json
import sqlite3
import threading

from .config import settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS calls (
                call_id TEXT PRIMARY KEY, started REAL, ended REAL, outcome TEXT,
                max_level INTEGER DEFAULT 0, virtual INTEGER DEFAULT 0, report TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                call_id TEXT, seq INTEGER, ts REAL, type TEXT, data TEXT
            );
            CREATE INDEX IF NOT EXISTS events_call ON events(call_id, seq);
            CREATE TABLE IF NOT EXISTS scam_memory (call_id TEXT, ts REAL, org TEXT, text TEXT);
            """
        )
    return _conn


def start_call(call_id: str, started: float, virtual: bool) -> None:
    with _lock:
        conn().execute("INSERT OR REPLACE INTO calls(call_id, started, virtual) VALUES (?,?,?)", (call_id, started, int(virtual)))
        conn().commit()


def add_event(call_id: str, seq: int, ts: float, type_: str, data: dict) -> None:
    with _lock:
        conn().execute("INSERT INTO events VALUES (?,?,?,?,?)", (call_id, seq, ts, type_, json.dumps(data, default=str)))
        conn().commit()


def end_call(call_id: str, ended: float, outcome: str, max_level: int, report: dict) -> None:
    with _lock:
        conn().execute(
            "UPDATE calls SET ended=?, outcome=?, max_level=?, report=? WHERE call_id=?",
            (ended, outcome, max_level, json.dumps(report, default=str), call_id),
        )
        conn().commit()


def remember_scam(call_id: str, ts: float, org: str | None, turns: list[str]) -> None:
    with _lock:
        conn().executemany("INSERT INTO scam_memory VALUES (?,?,?,?)", [(call_id, ts, org, t) for t in turns])
        conn().commit()


def scam_memory() -> list[dict]:
    with _lock:
        rows = conn().execute("SELECT call_id, ts, org, text FROM scam_memory ORDER BY ts DESC LIMIT 500").fetchall()
    return [dict(r) for r in rows]


def forget_scams() -> int:
    with _lock:
        n = conn().execute("DELETE FROM scam_memory").rowcount
        conn().commit()
    return n


def list_calls(limit: int = 50) -> list[dict]:
    with _lock:
        rows = conn().execute(
            "SELECT call_id, started, ended, outcome, max_level, virtual FROM calls ORDER BY started DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_report(call_id: str) -> dict | None:
    with _lock:
        row = conn().execute("SELECT report FROM calls WHERE call_id=?", (call_id,)).fetchone()
    return json.loads(row["report"]) if row and row["report"] else None
