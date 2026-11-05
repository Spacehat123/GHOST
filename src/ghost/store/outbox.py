"""STORE PLANE — the outbox (T1c): every effectful step is recorded BEFORE it
runs, with a unique idempotency key, and advances only through legal states.

pending -> sent -> verified        (happy path)
pending/sent -> failed             (terminal, with the error in payload)

The outbox is what makes a crash anywhere recoverable without double-sends:
T2's effect plane executes only against a pending row, marks it sent BEFORE
the provider call, and the provider's own idempotency key (same value) makes
a re-execution after a crash a no-op at the provider. Reconciliation (T5)
compares outbox rows against provider logs.

Lives only in the unified store (no legacy equivalent). stdlib + facade only.
"""
import json
import os
import sqlite3
import time
from contextlib import closing
from typing import Any, Dict, List, Optional

from ghost.store.facade import ghost_db_path
from ghost.store.schema import connect_target, create_target

# legal transitions: from -> set(to)
TRANSITIONS = {
    "pending": {"sent", "failed"},
    "sent": {"verified", "failed"},
    "verified": set(),
    "failed": set(),
}


def _conn(path: Optional[str] = None) -> sqlite3.Connection:
    conn = connect_target(path or ghost_db_path())
    return conn


def record(kind: str, idempotency_key: str, payload: Optional[Dict[str, Any]] = None,
           grant_id: Optional[str] = None, db_path: Optional[str] = None) -> Optional[int]:
    """Record an intended effect BEFORE running it. Duplicate key -> existing
    row (restarts are normal; the key is the dedupe). Returns the outbox id."""
    now = time.time()
    with closing(_conn(db_path)) as conn, conn:
        create_target(conn)
        cur = conn.execute(
            "INSERT OR IGNORE INTO outbox (kind, grant_id, idempotency_key, payload, "
            "state, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (kind, grant_id, idempotency_key,
             json.dumps(payload or {}, default=str), now, now))
        if cur.rowcount:
            row = conn.execute(
                "SELECT id FROM outbox WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            return int(row[0])
        row = conn.execute(
            "SELECT id FROM outbox WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        return int(row[0]) if row else None


def transition(idempotency_key: str, to_state: str,
               db_path: Optional[str] = None) -> bool:
    """Advance one outbox row to to_state — only along a legal transition.
    Returns False (and changes nothing) for unknown keys or illegal jumps."""
    with closing(_conn(db_path)) as conn, conn:
        row = conn.execute(
            "SELECT id, state FROM outbox WHERE idempotency_key=?",
            (idempotency_key,)).fetchone()
        if row is None:
            return False
        _id, state = int(row[0]), row[1]
        if to_state not in TRANSITIONS.get(state, set()):
            return False
        conn.execute(
            "UPDATE outbox SET state=?, updated_at=? WHERE id=?",
            (to_state, time.time(), _id))
        return True


def state_of(idempotency_key: str, db_path: Optional[str] = None) -> Optional[str]:
    with closing(_conn(db_path)) as conn:
        row = conn.execute(
            "SELECT state FROM outbox WHERE idempotency_key=?",
            (idempotency_key,)).fetchone()
        return row[0] if row else None


def pending(kind: Optional[str] = None, limit: int = 100,
            db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Rows still awaiting their effect (reconciliation's worklist)."""
    sql = "SELECT id, kind, grant_id, idempotency_key, payload, state, created_at " \
          "FROM outbox WHERE state IN ('pending','sent')"
    args: list = []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY created_at LIMIT ?"
    args.append(limit)
    with closing(_conn(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
