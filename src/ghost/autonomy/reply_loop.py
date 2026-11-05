"""Reply Loop — inbound reply -> verdict -> action (Roadmap §2 Stage 4, P2+P3).

Thin, deterministic wiring: classify (reply_classifier) -> act on the verdict:
  ANGRY / unsubscribe  -> permanent suppression + freeze + ping (b)
  BOUNCE               -> suppress address, no ping
  needs_call           -> ping operator with a client brief (§2 Stage 4)
  can't verify         -> ping (c)
  QUESTION answerable  -> drafted reply queued at L2 sampling (no auto-send
                          until email_outreach earns L3+ for replies)
Everything lands in the CRM interaction log and the audit ledger.
"""
import logging
import time
from typing import Any, Callable, Dict, Optional

from ghost.core.audit import audit
from ghost.core.reply_classifier import (
    ANGRY, AUTO_REPLY, BOUNCE, classify_reply, ghost_may_reply_alone,
)

logger = logging.getLogger("ghost.autonomy.reply_loop")


def ingest_reply(
    lead_slug: str,
    reply_text: str,
    *,
    suppression,                                  # SuppressionManager
    crm,                                          # CRMDatabase
    ladder,                                       # AutonomyLadder
    exchange_count: int = 1,
    discount_requested_pct: Optional[float] = None,
    ping: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
) -> Dict[str, Any]:
    """Process one inbound reply. Returns the verdict + actions taken.
    `ping(operator_category, payload)` is the §10 notification out."""
    verdict = classify_reply(reply_text, exchange_count=exchange_count,
                             discount_requested_pct=discount_requested_pct)
    actions: list = []

    try:
        crm.log_interaction(client_slug=lead_slug, channel="email",
                            direction="inbound",
                            content=f"[{verdict.category}] {reply_text[:500]}")
    except Exception:
        pass

    if verdict.category == ANGRY or verdict.freeze_sequence and verdict.category == ANGRY:
        # §0.4 one strike: permanent suppression, sequence frozen, ping (b).
        try:
            suppression.opt_out_lead(lead_slug, reason="reply_classifier:angry")
            actions.append("suppressed_permanently")
        except Exception:
            actions.append("suppress_failed")
        actions.append("sequence_frozen")
        audit("reply.complaint", subject=lead_slug, verdict=verdict.category,
              reasons=verdict.reasons, ping="(b) client distress")
        if ping:
            ping("(b) client distress", {"lead": lead_slug, "verdict": verdict.category,
                                         "reply": reply_text[:300]})

    elif verdict.category == BOUNCE:
        try:
            suppression.opt_out_lead(lead_slug, reason="reply_classifier:bounce")
            actions.append("address_suppressed")
        except Exception:
            pass
        audit("reply.bounce", subject=lead_slug, reasons=verdict.reasons)

    elif verdict.category == AUTO_REPLY:
        actions.append("sequence_paused")
        audit("reply.auto", subject=lead_slug)

    else:
        # INTERESTED / QUESTION / OBJECTION / NOT_NOW
        if verdict.needs_call:
            actions.append("call_recommended")
            audit("reply.call_recommended", subject=lead_slug,
                  reasons=verdict.reasons,
                  brief=f"Call {lead_slug}: " + "; ".join(verdict.reasons),
                  ping="(c) unverifiable: call recommended")
            if ping:
                ping("(c) unverifiable", {
                    "kind": "call_recommended", "lead": lead_slug,
                    "reasons": verdict.reasons,
                    "brief": f"{lead_slug} wants to talk. Reasons: {'; '.join(verdict.reasons)}",
                })
        elif verdict.ping_operator:
            audit("reply.unverifiable", subject=lead_slug,
                  reasons=verdict.reasons, ping="(c) unverifiable")
            if ping:
                ping("(c) unverifiable", {"lead": lead_slug,
                                          "reasons": verdict.reasons,
                                          "reply": reply_text[:300]})
            actions.append("pinged_operator")
        elif ghost_may_reply_alone(verdict):
            actions.append("auto_reply_eligible")
            audit("reply.auto_eligible", subject=lead_slug,
                  verdict=verdict.category, confidence=verdict.confidence)
        else:
            actions.append("queued_for_L2_review")
            audit("reply.queued_review", subject=lead_slug,
                  verdict=verdict.category, confidence=verdict.confidence)

    audit("reply.ingested", subject=lead_slug, verdict=verdict.category,
          confidence=verdict.confidence, actions=actions)
    return {"verdict": verdict, "actions": actions}
