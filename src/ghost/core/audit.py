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

# Unified-mode spine: the events_log DDL lives in ghost.store.schema; only the
# table name differs here. Hash chain (unified mode) maintained on every append.


class AuditLedger:
    _instance: Optional["AuditLedger"] = None

    def __init__(self, db_path: Optional[str] = None):
        # Resolution order: explicit arg > env redirect > cutover facade
        # (GHOST_STORE_MODE=unified -> ghost.db events_log).
        self.db_path = db_path or os.environ.get("GHOST_AUDIT_DB_PATH")
        if not self.db_path:
            from ghost.store import facade
            self.db_path = facade.audit_path()
        self.unified = "events_log" in self.db_path or (
            self.db_path.endswith("ghost.db"))
        self.table = "events_log" if self.unified else "audit_events"
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with closing(self._conn()) as conn, conn:
            if self.unified:
                from ghost.store.schema import create_target
                create_target(conn)
            else:
                conn.executescript(_SCHEMA)

    @classmethod
    def get_instance(cls) -> "AuditLedger":
        # Re-resolve the path from env on every call so long-lived processes
        # (and test harnesses that scope paths) pick up redirections instead
        # of a stale import-time snapshot.
        db_path = os.environ.get("GHOST_AUDIT_DB_PATH")
        if not db_path:
            from ghost.store import facade
            db_path = facade.audit_path()
        if cls._instance is None or cls._instance.db_path != db_path:
            cls._instance = AuditLedger(db_path)
        return cls._instance

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def record(self, event_type: str, subject: Optional[str] = None, **payload: Any) -> int:
        """Append one event. Cheap, synchronous, never raises into callers' faces.
        Unified mode keeps the events_log hash chain live on every append."""
        now = time.time()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        try:
            with closing(self._conn()) as conn, conn:
                cur = conn.execute(
                    f"INSERT INTO {self.table} (ts, day, event_type, subject, payload) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (now, day, event_type, subject, json.dumps(payload, default=str))
                )
                rid = cur.lastrowid
                if self.unified:
                    from ghost.store.chain import last_row_hash, row_hash_for
                    # the row just inserted still has a NULL hash, so the last
                    # non-null hash IS the previous link
                    prev = last_row_hash(conn, "events_log")
                    rh = row_hash_for(rid, now, day, event_type, subject,
                                      json.dumps(payload, default=str), prev)
                    conn.execute(
                        "UPDATE events_log SET prev_hash=?, row_hash=?, origin='main' "
                        "WHERE id=?", (prev, rh, rid))
                return rid
        except sqlite3.Error:
            # The ledger must never take the pipeline down with it.
            return -1

    def query(self, day: Optional[str] = None, event_type: Optional[str] = None,
              subject: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        sql = (f"SELECT id, ts, day, event_type, subject, payload "
               f"FROM {self.table} WHERE 1=1")
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
                f"SELECT event_type, COUNT(*) as n FROM {self.table} "
                "WHERE day = ? GROUP BY event_type ORDER BY n DESC",
                (day,)
            ).fetchall()
        return {"day": day, "events": {r["event_type"]: r["n"] for r in rows}}


def audit(event_type: str, subject: Optional[str] = None, **payload: Any) -> int:
    """Module-level convenience used across the codebase."""
    return AuditLedger.get_instance().record(event_type, subject, **payload)
