"""AUTONOMY — the ladder evidence mapper (Operator's 11 P10, goals 1+11).

The ladder's mutators existed since P3 with NO live caller — trust could
never grow. This module is the missing wire: it reads the audit ledger (the
same events the whole system already writes) and feeds the ladder:

  SUCCESS (evidence-backed):            capability
    lead_hunt.completed                 lead_hunting
    demo.hosted                         demo_build_deploy
    outbound.delivered                  email_outreach
    payment.verified                    payments
    sidecar.merged                      retainer_autofix
    contract.sent                       contracts

  VERIFIED INCIDENT (the gates caught reality disagreeing):
    reply.complaint                     email_outreach   (a real client said stop)
    reconcile.mismatch                  payments         (records vs provider)
    sidecar.discarded [verifier-failed] retainer_autofix (the fix did not verify)

Deliberately NOT incidents: grant_required, snapshot_required, halt discards,
underpayment refusals — those are the safety machinery WORKING, not the
capability failing. Only verified reality-mismatches demote.

Cursor: the last processed ledger row id, persisted — replaying a sync never
double-counts. Promotion stays OPERATOR-ONLY; the ladder merely unlocks.
"""
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from ghost.core.audit import audit, AuditLedger
from ghost.paths import data_dir

logger = logging.getLogger("ghost.autonomy.ladder_evidence")

SUCCESS_MAP: Dict[str, str] = {
    "lead_hunt.completed": "lead_hunting",
    "demo.hosted": "demo_build_deploy",
    "outbound.delivered": "email_outreach",
    "payment.verified": "payments",
    "sidecar.merged": "retainer_autofix",
    "contract.sent": "contracts",
}

INCIDENT_MAP: Dict[str, str] = {
    "reply.complaint": "email_outreach",
    "reconcile.mismatch": "payments",
}

QUERY_TYPES = sorted(set(SUCCESS_MAP) | set(INCIDENT_MAP) | {"sidecar.discarded"})
BATCH = 500


def _cursor_path() -> str:
    return os.environ.get("GHOST_LADDER_CURSOR") or os.path.join(
        data_dir(), "ladder_cursor.json")


def _load_cursor() -> int:
    try:
        with open(_cursor_path(), "r", encoding="utf-8") as f:
            return int(json.load(f).get("last_id", 0))
    except (OSError, ValueError):
        return 0


def _save_cursor(last_id: int) -> None:
    tmp = _cursor_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"last_id": last_id, "at": time.time()}, f)
    os.replace(tmp, _cursor_path())


def _verifier_failed_discard(payload: Any) -> bool:
    """A sidecar discard is only an INCIDENT when the INDEPENDENT verifier
    rejected the patch. Halt/budget/out-of-scope discards are the safety
    machinery working correctly."""
    reason = ""
    if isinstance(payload, dict):
        reason = str(payload.get("reason", ""))
    elif isinstance(payload, str):
        reason = payload
    return "verifier failed" in reason or "verifier timeout" in reason


def sync_ladder_from_ledger(ledger: Optional[AuditLedger] = None,
                            ladder=None) -> Dict[str, Any]:
    """One sync pass: read mapped events after the cursor, feed the ladder in
    id order, persist the cursor. Idempotent; safe to call on a schedule."""
    led = ledger or AuditLedger.get_instance()
    if ladder is None:
        from ghost.autonomy.ladder import AutonomyLadder
        ladder = AutonomyLadder()

    cursor = _load_cursor()
    events: List[Dict[str, Any]] = []
    for et in QUERY_TYPES:
        for row in led.query(event_type=et, limit=BATCH):
            if row.get("id", 0) > cursor:
                events.append(row)
    events.sort(key=lambda r: r["id"])

    successes = incidents = 0
    last_id = cursor
    for row in events:
        et = row.get("event_type", "")
        cap = SUCCESS_MAP.get(et)
        if cap:
            ladder.record_success(cap, evidence=f"ledger#{row['id']} {et}")
            successes += 1
        elif et == "sidecar.discarded":
            if _verifier_failed_discard(row.get("payload")):
                ladder.record_incident("retainer_autofix",
                                       description=f"verifier rejected a fix (ledger#{row['id']})",
                                       verified=True)
                incidents += 1
        else:
            cap = INCIDENT_MAP.get(et)
            if cap:
                ladder.record_incident(cap,
                                       description=f"{et} (ledger#{row['id']})",
                                       verified=True)
                incidents += 1
        last_id = max(last_id, row["id"])

    if events:
        _save_cursor(last_id)
        audit("ladder.synced", successes=successes, incidents=incidents,
              cursor=last_id, note="trust follows evidence — promotion stays operator-only")
    return {"successes": successes, "incidents": incidents,
            "processed": len(events), "cursor": last_id}
