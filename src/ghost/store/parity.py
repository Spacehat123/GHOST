"""STORE PLANE — dual-read parity check (T1c).

During the dual-read window the invariant is: **no history is lost**. Every
event in the legacy audit ledger must exist in the unified events_log (the
migrator guarantees it at copy time); the unified store MAY hold more
(post-cutover writes land only there). Any legacy event missing from the
unified store is drift — usually a code path still writing the legacy store
after cutover — and must alarm.

Jobs: state-count parity the same way. Missing legacy stores are reported,
not errors (queue.db never materialized on this machine, for example).
"""
import os
import sqlite3
from collections import Counter
from typing import Any, Dict, List, Optional

from ghost.store.facade import repo_dir, ghost_db_path


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone())


def _day_multisets(conn: sqlite3.Connection, table: str) -> Dict[str, Counter]:
    out: Dict[str, Counter] = {}
    for day, etype, subject, n in conn.execute(
            f"SELECT day, event_type, subject, COUNT(*) FROM \"{table}\" "
            "GROUP BY day, event_type, subject"):
        out.setdefault(day, Counter())[(etype, subject)] += n
    return out


def compare(legacy_audit: Optional[str] = None, legacy_queue: Optional[str] = None,
            unified: Optional[str] = None) -> Dict[str, Any]:
    repo = repo_dir()
    legacy_audit = legacy_audit or os.path.join(repo, "data", "audit.db")
    legacy_queue = legacy_queue or os.path.join(repo, "data", "queue.db")
    unified = unified or ghost_db_path()

    report: Dict[str, Any] = {"ok": True, "events": {}, "jobs": {}}

    if not os.path.exists(unified):
        return {"ok": False, "events": {"error": "unified store missing — run `ghost migrate`"},
                "jobs": {}}
    conu = _ro(unified)

    # ---- events: every legacy event must exist in unified (no loss) --------
    if os.path.exists(legacy_audit):
        conl = _ro(legacy_audit)
        legacy_ms = _day_multisets(conl, "audit_events")
        unified_ms = _day_multisets(conu, "events_log")
        missing = 0
        drift_days: List[str] = []
        for day, lms in sorted(legacy_ms.items()):
            ums = unified_ms.get(day, Counter())
            lost = lms - ums  # entries in legacy absent from unified
            if lost:
                missing += sum(lost.values())
                drift_days.append(day)
        report["events"] = {
            "legacy_events": sum(sum(c.values()) for c in legacy_ms.values()),
            "unified_events": sum(sum(c.values()) for c in unified_ms.values()),
            "missing_in_unified": missing,
            "drift_days": drift_days,
        }
        if missing:
            report["ok"] = False
        conl.close()
    else:
        report["events"] = {"note": "legacy audit.db absent — nothing to compare"}

    # ---- jobs: state counts (legacy ⊆ unified) ------------------------------
    if os.path.exists(legacy_queue) and _table_exists(_ro(legacy_queue), "jobs"):
        conq = _ro(legacy_queue)
        legacy_jobs = dict(conq.execute(
            "SELECT state, COUNT(*) FROM jobs GROUP BY state"))
        conq.close()
        unified_jobs = dict(conu.execute(
            "SELECT state, COUNT(*) FROM jobs GROUP BY state")) \
            if _table_exists(conu, "jobs") else {}
        lost = {s: n for s, n in legacy_jobs.items()
                if n > unified_jobs.get(s, 0)}
        report["jobs"] = {"legacy": legacy_jobs, "unified": unified_jobs,
                          "missing_in_unified": lost}
        if lost:
            report["ok"] = False
    else:
        report["jobs"] = {"note": "legacy queue.db absent — nothing to compare"}

    conu.close()
    return report


def run() -> Dict[str, Any]:
    return compare()
