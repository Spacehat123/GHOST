"""STORE PLANE — day replay (T1c): reconstruct a day's state from the event
log alone, deterministically.

`derive_day` reads ONLY events (no live tables, no clocks beyond the day
string it is given) and folds them into a state dict with sorted, stable
output — replaying the same events anywhere (legacy or unified store)
yields byte-identical JSON. That is the property the T6 simulator's
"replaying a day gives identical decisions" exit test builds on.
"""
import json
from collections import Counter
from typing import Any, Dict

from ghost.core.audit import AuditLedger

DERIVED_EVENT_TYPES = ("policy.decision", "job.enqueued", "job.done",
                       "outbound.sent", "outbound.blocked", "tokens.spent")


def derive_day(day: str, ledger: AuditLedger, limit: int = 20000) -> Dict[str, Any]:
    rows = ledger.query(day=day, limit=limit)
    rows = sorted(rows, key=lambda r: r["id"])  # id order = append order
    by_type = Counter(r["event_type"] for r in rows)
    decisions = sorted({str(r["subject"]) for r in rows
                        if r["event_type"] == "policy.decision" and r["subject"]})
    enqueued = sorted({str(r["subject"]) for r in rows
                       if r["event_type"] == "job.enqueued" and r["subject"]})
    tokens_by_intent: Dict[str, int] = {}
    for r in rows:
        if r["event_type"] == "tokens.spent" and isinstance(r.get("payload"), dict):
            intent = str(r["payload"].get("intent", "unknown"))
            try:
                tokens_by_intent[intent] = tokens_by_intent.get(intent, 0) + \
                    int(r["payload"].get("tokens", 0))
            except (TypeError, ValueError):
                continue
    derived = {t: by_type[t] for t in DERIVED_EVENT_TYPES if t in by_type}
    return {
        "day": day,
        "total_events": len(rows),
        "events_by_type": dict(sorted(by_type.items())),
        "derived": derived,
        "decisions": decisions,
        # volatile identifiers (uuid job ids) are NOT replay state — a day's
        # decision-relevant shape is the count, not the random handles
        "jobs_enqueued_count": len(enqueued),
        "tokens_by_intent": dict(sorted(tokens_by_intent.items())),
    }
