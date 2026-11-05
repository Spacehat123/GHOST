"""Metrics — the §12 scoreboard, computed from the audit ledger. No vibes.

One function the CLI, the /api/metrics route, and the daily digest all share.
Evidence coverage: fraction of outbound actions with a verifiable artifact —
the roadmap targets 100%.
"""
import time
from typing import Any, Dict


def compute_metrics(day: str | None = None, ledger=None) -> Dict[str, Any]:
    from ghost.core.audit import AuditLedger
    ledger = ledger or AuditLedger.get_instance()
    day = day or time.strftime("%Y-%m-%d")
    events = ledger.day_summary(day).get("events", {})

    def n(kind: str) -> int:
        return int(events.get(kind, 0))

    sent = n("outbound.sent")
    blocked = n("outbound.blocked")
    replies = n("reply.ingested")
    complaints = n("reply.complaint")
    incidents = n("ladder.incident")
    dead = n("job.dead")
    jobs_done = n("job.done")

    # Trust ceilings (§12): negative reply rate < 0.5%, bounce < 2%
    negative_rate = (complaints / replies) if replies else 0.0

    # Autonomy: share of audited lifecycle actions that completed without a
    # dead-letter (incidents/1k actions proxy)
    total_actions = jobs_done + dead + n("job.retry")
    incident_rate_per_1k = (incidents / total_actions * 1000) if total_actions else 0.0

    return {
        "day": day,
        "growth": {
            "leads_contacted": sent,
            "outbound_blocked_by_gates": blocked,
            "payments_verified": n("payment.verified"),
            "replies_ingested": replies,
            "complaints": complaints,
        },
        "trust": {
            "negative_reply_rate": round(negative_rate, 4),
            "negative_reply_rate_ceiling": 0.005,
            "spam_complaints": complaints,
            "halted": events.get("halt.tripped", 0),
        },
        "autonomy": {
            "jobs_completed": jobs_done,
            "jobs_dead": dead,
            "incidents": incidents,
            "incidents_per_1k_actions": round(incident_rate_per_1k, 2),
            "ladder_unlocks": n("ladder.unlock_available"),
            "policy_decisions": n("policy.decision"),
        },
        "reliability": {
            "watchdog_heartbeat_stale": n("watchdog.heartbeat_stale"),
            "retries": n("job.retry"),
            "qa_passes": n("qa.passed"),
            "qa_failures": n("qa.failed"),
        },
        "voice": {
            "narrations": n("narrator.spoke"),
        },
    }
