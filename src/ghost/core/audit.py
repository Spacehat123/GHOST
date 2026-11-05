"""Append-only Audit Ledger — the single queryable "what did Ghost do and why" (Roadmap §3.6).

Every job, decision, and outbound action writes a structured event here.
SQLite, WAL mode, one table, monotonic IDs. The daily digest (§10), the
narrator (§14.2), and any future dashboard all read from this ledger —
one source of truth, replayable forever.
"""
import json
import os
import sqlite3
from contextlib import closing
import time
from typing import Any, Dict, List, Optional

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
AUDIT_DB_PATH = os.environ.get("GHOST_AUDIT_DB_PATH") or os.path.join(REPO_DIR, "data", "audit.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    day TEXT NOT NULL,
    event_type TEXT NOT NULL,
    subject TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_day ON audit_events(day);
CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type);
"""


class AuditLedger:
    _instance: Optional["AuditLedger"] = None

    def __init__(self, db_path: str = AUDIT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with closing(self._conn()) as conn, conn:
            conn.executescript(_SCHEMA)

    @classmethod
    def get_instance(cls) -> "AuditLedger":
        if cls._instance is None:
            cls._instance = AuditLedger()
        return cls._instance

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def record(self, event_type: str, subject: Optional[str] = None, **payload: Any) -> int:
        """Append one event. Cheap, synchronous, never raises into callers' faces."""
        now = time.time()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        try:
            with closing(self._conn()) as conn, conn:
                cur = conn.execute(
                    "INSERT INTO audit_events (ts, day, event_type, subject, payload) VALUES (?, ?, ?, ?, ?)",
                    (now, day, event_type, subject, json.dumps(payload, default=str))
                )
                return cur.lastrowid
        except sqlite3.Error:
            # The ledger must never take the pipeline down with it.
            return -1

    def query(self, day: Optional[str] = None, event_type: Optional[str] = None,
              subject: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        sql = "SELECT id, ts, day, event_type, subject, payload FROM audit_events WHERE 1=1"
        args: list = []
        if day:
            sql += " AND day = ?"
            args.append(day)
        if event_type:
            sql += " AND event_type = ?"
            args.append(event_type)
        if subject:
            sql += " AND subject = ?"
            args.append(subject)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with closing(self._conn()) as conn, conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
            out.append(d)
        return out

    def day_summary(self, day: Optional[str] = None) -> Dict[str, Any]:
        """The one-query answer to 'what did Ghost do today' (daily digest input)."""
        day = day or time.strftime("%Y-%m-%d")
        with closing(self._conn()) as conn, conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT event_type, COUNT(*) as n FROM audit_events WHERE day = ? GROUP BY event_type ORDER BY n DESC",
                (day,)
            ).fetchall()
        return {"day": day, "events": {r["event_type"]: r["n"] for r in rows}}


def audit(event_type: str, subject: Optional[str] = None, **payload: Any) -> int:
    """Module-level convenience used across the codebase."""
    return AuditLedger.get_instance().record(event_type, subject, **payload)
