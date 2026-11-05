"""STORE PLANE — events_log hash chain (extracted so the live audit ledger
and the migrator share ONE implementation; T1c).

The chain makes silent history edits structurally detectable: every row's
row_hash covers its content plus the previous row's hash, in id order.
Tamper with any payload and verify_hash_chain() fails at that row.
"""
import hashlib
import json
import sqlite3
from typing import Tuple


def _row_material(rid, ts, day, etype, subject, payload, prev: str) -> str:
    return json.dumps({"id": rid, "ts": ts, "day": day, "event_type": etype,
                       "subject": subject, "payload": payload,
                       "prev": prev}, sort_keys=True, default=str)


def row_hash_for(rid, ts, day, etype, subject, payload, prev: str) -> str:
    return hashlib.sha256(
        _row_material(rid, ts, day, etype, subject, payload, prev).encode("utf-8")
    ).hexdigest()


def build_hash_chain(conn: sqlite3.Connection, table: str = "events_log") -> int:
    """Recompute prev_hash/row_hash over every row in id order. Idempotent.
    Returns rows chained."""
    rows = conn.execute(
        f"SELECT id, ts, day, event_type, subject, payload FROM \"{table}\" ORDER BY id"
    ).fetchall()
    prev = ""
    for rid, ts, day, etype, subject, payload in rows:
        rh = row_hash_for(rid, ts, day, etype, subject, payload, prev)
        conn.execute(f'UPDATE "{table}" SET prev_hash=?, row_hash=? WHERE id=?',
                     (prev, rh, rid))
        prev = rh
    conn.commit()
    return len(rows)


def verify_hash_chain(conn: sqlite3.Connection, table: str = "events_log") -> Tuple[bool, str]:
    rows = conn.execute(
        f'SELECT id, ts, day, event_type, subject, payload, prev_hash, row_hash '
        f'FROM "{table}" ORDER BY id').fetchall()
    prev = ""
    for rid, ts, day, etype, subject, payload, stored_prev, stored_hash in rows:
        if (stored_prev or "") != prev:
            return False, f"row {rid}: prev_hash mismatch"
        expected = row_hash_for(rid, ts, day, etype, subject, payload, prev)
        if expected != (stored_hash or ""):
            return False, f"row {rid}: row_hash mismatch"
        prev = stored_hash or ""
    return True, f"{len(rows)} rows chained"


def last_row_hash(conn: sqlite3.Connection, table: str = "events_log") -> str:
    row = conn.execute(
        f'SELECT row_hash FROM "{table}" WHERE row_hash IS NOT NULL '
        f'ORDER BY id DESC LIMIT 1').fetchone()
    return row[0] if row else ""
