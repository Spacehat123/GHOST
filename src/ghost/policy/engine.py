"""The Decision Engine — Ghost's executive brain (Roadmap §1).

Pure Python. No LLM, no network, no clock reads inside decide() (the snapshot
carries all time-derived values). The LLM proposes; this engine disposes.

Properties (tested):
- DETERMINISTIC: identical snapshot -> identical decision (replayable).
- FAIL CLOSED: unknown/invalid inputs never grant outbound work.
- CAPS ARE LAW: the engine mathematically cannot grant more contacts than
  the daily cap; nothing downstream can talk it up.
- EVERY DECISION IS EVIDENCE: each one gets a stable decision_id (hash of
  snapshot+config) and is written to the audit ledger with its reasons.

Intent priority (§1.1): HOLD_FIRE > SERVICE_EXISTING > CLOSE_PIPELINE >
GROW > MAINTAIN.
"""
import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from ghost.core.audit import audit

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
POLICY_CONFIG_PATH = os.environ.get("GHOST_POLICY_CONFIG_PATH") or os.path.join(REPO_DIR, "configs", "policy.json")

INTENT_HOLD_FIRE = "HOLD_FIRE"
INTENT_SERVICE_EXISTING = "SERVICE_EXISTING"
INTENT_CLOSE_PIPELINE = "CLOSE_PIPELINE"
INTENT_GROW = "GROW"
INTENT_MAINTAIN = "MAINTAIN"

INTENT_PRIORITY = [INTENT_HOLD_FIRE, INTENT_SERVICE_EXISTING, INTENT_CLOSE_PIPELINE, INTENT_GROW, INTENT_MAINTAIN]


# --------------------------------------------------------------------- config

DEFAULT_CONFIG: Dict[str, Any] = {
    "knobs": {
        "ping_deal_value_inr": 20000,          # (a) deals over this ping the operator
        "max_discount_pct": 10,                # bigger discounts ping
        "idle_hours_before_hunt": 24,          # the 'no projects in 24h' trigger
        "hunt_quota_floor_pct": 90,            # the '90% limit left' trigger
        "hunt_quota_reserve_pct": 60,          # leftover quota is reserve, not fuel (§5)
        "max_new_contacts_per_day": 50,        # anti-spam hard cap (§6)
        "max_build_cost_per_hunt_inr": 2000,   # budget granted per FIND_LEADS
        "pipeline_target_inr": 100000,
        "heartbeat_max_age_s": 120             # watchdog teeth
    }
}


def load_config(path: str = POLICY_CONFIG_PATH) -> Dict[str, Any]:
    """Config file wins; missing keys fall back to defaults. The operator owns
    the knobs file; the engine never writes it."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            overrides = json.load(f)
        cfg["knobs"].update(overrides.get("knobs", {}))
    return cfg


# -------------------------------------------------------------------- snapshot

@dataclass(frozen=True)
class StateSnapshot:
    """Everything the engine is allowed to know. No clocks, no IO — caller fills it."""
    # system health
    halted: bool = False
    halt_reason: Optional[str] = None
    open_complaints: int = 0
    domain_health: str = "green"                # green | yellow | red
    heartbeat_age_s: float = 5.0                # age of last watchdog heartbeat
    # work state
    incidents_over_slo: int = 0                 # retainer incidents past SLO
    client_replies_waiting: int = 0
    hot_leads_waiting: int = 0                  # leads mid-pipeline
    pipeline_value_inr: float = 0.0
    last_closed_deal_age_h: Optional[float] = None
    # budgets (today)
    quota_remaining_pct: float = 100.0
    contacts_sent_today: int = 0
    # time context (filled by caller, not read from wall clock)
    within_business_hours: bool = True

    def validated(self) -> "StateSnapshot":
        """Coerce/validate so bad telemetry can't unlock grants OR crash the
        engine. Garbage in -> safe defaults out. Fail closed."""
        def _num(v, default, lo, hi):
            try:
                return max(lo, min(hi, float(v)))
            except (TypeError, ValueError):
                return default

        def _int(v, default, lo, hi):
            try:
                return max(lo, min(hi, int(v)))
            except (TypeError, ValueError):
                return default

        def _opt_num(v, lo, hi):
            if v is None:
                return None
            try:
                return max(lo, min(hi, float(v)))
            except (TypeError, ValueError):
                return None  # unknown deal age -> treated as never-closed -> hunts sooner, safely

        return StateSnapshot(
            halted=bool(self.halted or self.heartbeat_age_s is None),
            halt_reason=self.halt_reason,
            open_complaints=_int(self.open_complaints, 0, 0, 10**6),
            domain_health=self.domain_health if self.domain_health in ("green", "yellow", "red") else "red",
            heartbeat_age_s=_num(self.heartbeat_age_s, 1e9, 0, 1e9),
            incidents_over_slo=_int(self.incidents_over_slo, 0, 0, 10**6),
            client_replies_waiting=_int(self.client_replies_waiting, 0, 0, 10**6),
            hot_leads_waiting=_int(self.hot_leads_waiting, 0, 0, 10**6),
            pipeline_value_inr=_num(self.pipeline_value_inr, 0.0, 0, 1e12),
            last_closed_deal_age_h=_opt_num(self.last_closed_deal_age_h, 0, 1e6),
            quota_remaining_pct=_num(self.quota_remaining_pct, 0.0, 0, 100),
            contacts_sent_today=_int(self.contacts_sent_today, 0, 0, 10**6),
            within_business_hours=bool(self.within_business_hours),
        )


@dataclass
class Grant:
    """A budget the engine grants. Downstream workers may spend less, never more."""
    intent: str
    max_new_contacts: int = 0
    max_build_cost_inr: float = 0.0


@dataclass
class Decision:
    decision_id: str
    intent: str
    grants: List[Grant] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    pings: List[str] = field(default_factory=list)   # categories from §10
    snapshot: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["grants"] = [asdict(g) for g in self.grants]
        return d


# ---------------------------------------------------------------------- engine

class PolicyEngine:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or load_config()
        self.k = self.config["knobs"]

    def decide(self, raw_snapshot: StateSnapshot) -> Decision:
        s = raw_snapshot.validated()
        reasons: List[str] = []
        pings: List[str] = []

        # ---- intent selection, strict priority order -----------------------
        intent = INTENT_MAINTAIN

        hold_reasons = self._hold_fire_reasons(s)
        if hold_reasons:
            intent = INTENT_HOLD_FIRE
            reasons.extend(hold_reasons)
            if s.heartbeat_age_s > self.k["heartbeat_max_age_s"]:
                pings.append("unverifiable: watchdog heartbeat stale — agent frozen")

        elif s.incidents_over_slo > 0 or s.client_replies_waiting > 0:
            intent = INTENT_SERVICE_EXISTING
            reasons.append(f"serving existing work first: {s.incidents_over_slo} incidents over SLO, "
                           f"{s.client_replies_waiting} client replies waiting")

        elif s.hot_leads_waiting > 0:
            intent = INTENT_CLOSE_PIPELINE
            reasons.append(f"{s.hot_leads_waiting} hot lead(s) mid-pipeline — cheapest revenue is closing these")

        elif self._should_hunt(s, reasons):
            intent = INTENT_GROW

        if intent == INTENT_MAINTAIN and not reasons:
            reasons.append("no higher-priority work; idle capacity -> improve golden demos, refresh recon cache")

        # ---- grants ----------------------------------------------------------
        grants: List[Grant] = []
        if intent == INTENT_GROW:
            contacts_left = max(0, self.k["max_new_contacts_per_day"] - s.contacts_sent_today)
            # Quota discipline (§5): hunt only with the non-reserve share.
            usable_pct = max(0.0, s.quota_remaining_pct - self.k["hunt_quota_reserve_pct"])
            quota_ok = s.quota_remaining_pct >= self.k["hunt_quota_floor_pct"]
            if contacts_left == 0:
                # Cap hit: no grant, queue for tomorrow. Never exceed.
                reasons.append("daily contact cap exhausted — hunting queued until tomorrow")
            elif not quota_ok and usable_pct <= 0:
                reasons.append("insufficient non-reserve quota — hunting deferred")
            else:
                grants.append(Grant(
                    intent=INTENT_GROW,
                    max_new_contacts=contacts_left,
                    max_build_cost_inr=float(self.k["max_build_cost_per_hunt_inr"]),
                ))
                reasons.append(f"granted {contacts_left} contacts (cap {self.k['max_new_contacts_per_day']}/day, "
                               f"{s.contacts_sent_today} sent today)")

        decision_id = self._decision_id(s)
        decision = Decision(
            decision_id=decision_id,
            intent=intent,
            grants=grants,
            reasons=reasons,
            pings=pings,
            snapshot=asdict(s),
        )
        audit("policy.decision", subject=decision_id, intent=intent,
              reasons=reasons, grants=[asdict(g) for g in grants], pings=pings)
        return decision

    # ------------------------------------------------------------------ rules

    def _hold_fire_reasons(self, s: StateSnapshot) -> List[str]:
        reasons = []
        if s.halted:
            reasons.append(f"halt flag active (reason={s.halt_reason})")
        if s.open_complaints > 0:
            reasons.append(f"{s.open_complaints} open complaint(s) — outbound frozen (§0.4)")
        if s.domain_health == "red":
            reasons.append("domain reputation red — outbound frozen")
        if s.heartbeat_age_s > self.k["heartbeat_max_age_s"]:
            reasons.append(f"watchdog heartbeat stale ({s.heartbeat_age_s:.0f}s) — sick agent stays silent (§3.3)")
        if reasons:
            reasons.insert(0, "HOLD_FIRE: all outbound frozen")
        return reasons

    def _should_hunt(self, s: StateSnapshot, reasons: List[str]) -> bool:
        """The user's exact scenario, encoded (§1.1): no deal in 24h + 90% quota
        left + pipeline under target + healthy system -> go make money."""
        k = self.k
        if not s.within_business_hours:
            reasons.append("outside business hours for target region — no cold outreach")
            return False
        if s.domain_health != "green":
            reasons.append(f"domain health '{s.domain_health}' — no new cold outreach")
            return False

        idle = (s.last_closed_deal_age_h is None) or (s.last_closed_deal_age_h >= k["idle_hours_before_hunt"])
        quota_rich = s.quota_remaining_pct >= k["hunt_quota_floor_pct"]
        pipeline_low = s.pipeline_value_inr < k["pipeline_target_inr"]

        if idle and quota_rich and pipeline_low:
            idle_txt = "never closed a deal" if s.last_closed_deal_age_h is None else f"last deal {s.last_closed_deal_age_h:.0f}h ago"
            reasons.append(f"GROW trigger: {idle_txt}, {s.quota_remaining_pct:.0f}% quota remaining, "
                           f"pipeline ₹{s.pipeline_value_inr:,.0f} < ₹{k['pipeline_target_inr']:,.0f} target")
            return True
        return False

    def _decision_id(self, s: StateSnapshot) -> str:
        """Stable hash of (snapshot, config) — identical inputs, identical id.
        This is what makes decisions replayable and auditable."""
        blob = json.dumps({"snapshot": asdict(s), "knobs": self.k}, sort_keys=True)
        return "dec_" + hashlib.sha256(blob.encode()).hexdigest()[:16]
