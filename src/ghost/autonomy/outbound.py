"""Outbound Gateway — the ONE door every cold outreach passes through (Roadmap §6).

Not a mailer: a gate stack. Every send attempt is evaluated against, in order:
  1. halt switch (§0.5) — fail closed
  2. ladder level for 'email_outreach' (§1.2) — L0 blocks sending entirely
  3. warmup schedule (§6.2) — 5/day week 1 → 20/day week 2 → 50/day week 4
  4. daily cap (§6.3, from policy knobs) — hard, shared across senders
  5. suppression (§6.3) — lifetime one-first-touch, opt-out honored forever
  6. personalization gate (§6.4) — >=2 lead-specific facts or no send
Each refusal is audited with its reason. The actual sending is delegated to
an injected sender callable, so the L2 sampling rule (review 1-in-N) is a
mode of this gateway, not a hope.

The LLM never sees these checks; it cannot argue with a counter.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ghost.core.audit import audit
from ghost.core.halt import assert_not_halted

logger = logging.getLogger("ghost.autonomy.outbound")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
OUTBOUND_STATE_PATH = os.environ.get("GHOST_OUTBOUND_STATE_PATH") or os.path.join(REPO_DIR, "data", "outbound_state.json")

WARMUP_SCHEDULE = [  # (campaign_day_from, campaign_day_to_inclusive, max_sends)
    (1, 7, 5),
    (8, 14, 20),
    (15, None, 50),
]
MIN_PERSONALIZATION_FACTS = 2
DAILY_STATE_RESET_UTC_OFFSET_H = -5.5  # ~IST midnight reset for counters


@dataclass
class SendRequest:
    lead_slug: str
    to_email: str
    subject: str
    body: str
    personalization_facts: List[str] = field(default_factory=list)
    is_followup: bool = False
    first_contacted_at: Optional[float] = None   # lifetime one-touch bookkeeping


@dataclass
class GateResult:
    allowed: bool
    reason: Optional[str] = None
    gate: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


class OutboundGateway:
    def __init__(self, knobs: Dict[str, Any], suppression, ladder,
                 state_path: str = OUTBOUND_STATE_PATH,
                 now: Optional[Callable[[], float]] = None):
        """knobs: policy knobs dict (max_new_contacts_per_day lives there).
        suppression: SuppressionManager-like (is_suppressed).
        ladder: AutonomyLadder."""
        self.knobs = knobs
        self.suppression = suppression
        self.ladder = ladder
        self.state_path = state_path
        self._now = now or time.time
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        self._state = self._load()

    # ------------------------------------------------------------ day counters

    def _load(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"day": None, "sends_today": 0, "campaign_started_at": None,
                "first_touches": {}}

    def _save(self) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._state, f)
        os.replace(tmp, self.state_path)

    def _ist_day(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self._now() + 5.5 * 3600))

    def _roll_day(self) -> None:
        today = self._ist_day()
        if self._state.get("day") != today:
            self._state["day"] = today
            self._state["sends_today"] = 0
            self._save()

    def campaign_day(self) -> int:
        """Day number of the outreach campaign (1-based) for warmup pacing."""
        started = self._state.get("campaign_started_at")
        if not started:
            self._state["campaign_started_at"] = self._now()
            self._save()
            return 1
        return int((self._now() - started) / 86400) + 1

    def warmup_limit_today(self) -> int:
        day = self.campaign_day()
        for start, end, limit in WARMUP_SCHEDULE:
            if end is None or start <= day <= end:
                return limit
        return WARMUP_SCHEDULE[-1][2]

    def sends_today(self) -> int:
        self._roll_day()
        return self._state["sends_today"]

    # ------------------------------------------------------------------- gates

    def evaluate(self, req: SendRequest) -> GateResult:
        """Run the full gate stack. Refusals are audited. NEVER sends."""
        # 1. halt
        try:
            assert_not_halted(f"outreach:{req.lead_slug}")
        except Exception as e:
            return GateResult(False, gate="halt", reason=str(e)[:200])

        # 2. autonomy level
        refusal = self.ladder.require_level("email_outreach", 2)
        if refusal:
            audit("outbound.blocked", subject=req.lead_slug, gate="ladder", **refusal)
            return GateResult(False, gate="ladder", reason=refusal["reason"])

        # 3. warmup + 4. daily cap — the binding limit is the stricter one
        self._roll_day()
        cap = int(self.knobs.get("max_new_contacts_per_day", 50))
        warm = self.warmup_limit_today()
        limit = min(cap, warm)
        sent = self._state["sends_today"]
        if sent >= limit:
            reason = (f"daily send limit reached ({sent}/{limit}; "
                      f"cap={cap}, warmup_day={self.campaign_day()}={warm})")
            audit("outbound.blocked", subject=req.lead_slug, gate="cap", reason=reason)
            return GateResult(False, gate="cap", reason=reason,
                              meta={"queued_until": "tomorrow"})

        # 5. suppression — lifetime one-first-touch + opt-out list
        if self.suppression.is_suppressed(req.to_email, lead_slug=req.lead_slug):
            reason = "suppressed (opt-out or prior suppression)"
            audit("outbound.blocked", subject=req.lead_slug, gate="suppression", reason=reason)
            return GateResult(False, gate="suppression", reason=reason)
        if not req.is_followup and req.first_contacted_at is not None:
            reason = "lifetime one-first-touch: business already contacted"
            audit("outbound.blocked", subject=req.lead_slug, gate="one_touch", reason=reason)
            return GateResult(False, gate="one_touch", reason=reason)

        # 6. personalization gate — proof this isn't a template blast (§6.4)
        facts = [f for f in (req.personalization_facts or []) if f and str(f).strip()]
        if len(facts) < MIN_PERSONALIZATION_FACTS:
            reason = (f"personalization gate: {len(facts)} lead-specific facts "
                      f"(need {MIN_PERSONALIZATION_FACTS})")
            audit("outbound.blocked", subject=req.lead_slug, gate="personalization",
                  reason=reason, facts=facts)
            return GateResult(False, gate="personalization", reason=reason)

        return GateResult(True, meta={"limit_today": limit, "sent_today": sent,
                                      "warmup_day": self.campaign_day(),
                                      "facts": facts})

    def issue_grant(self, req: SendRequest, ttl_s: float = 300.0) -> Optional[str]:
        """Mint the §11 grant token for a request that PASSED all gates.
        The mail wrapper will refuse to send without it. None = cannot
        authorize (no signing secret) — a deployment-level fail closed."""
        from ghost.core import grants
        result = self.evaluate(req)
        if not result.allowed:
            return None
        token = grants.issue(
            capability="outbound_email",
            subject=req.lead_slug,
            bounds={
                "to": req.to_email,
                "facts": len(result.meta.get("facts", [])),
                "is_followup": req.is_followup,
            },
            ttl_s=ttl_s,
        )
        return token

    def record_sent(self, req: SendRequest, message_id: Optional[str] = None) -> None:
        """Call ONLY after the delegated sender actually accepted the message."""
        self._roll_day()
        self._state["sends_today"] += 1
        self._state.setdefault("first_touches", {})[req.lead_slug] = self._now()
        self._save()
        audit("outbound.sent", subject=req.lead_slug, to=req.to_email,
              message_id=message_id, is_followup=req.is_followup,
              facts=req.personalization_facts[:5],
              sends_today=self._state["sends_today"])

    def is_first_touch_used(self, lead_slug: str) -> bool:
        return lead_slug in self._state.get("first_touches", {})


def build_default_gateway(knobs: Dict[str, Any], suppression, ladder) -> OutboundGateway:
    return OutboundGateway(knobs=knobs, suppression=suppression, ladder=ladder)
