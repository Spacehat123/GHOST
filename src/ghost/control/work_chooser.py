"""CONTROL PLANE — the deterministic work chooser (T6).

Ghost decides what is worth doing by looking at the state of the business.
This is a PRIORITY ORDER, not an expected-value engine (de-scoped honestly in
the roadmap): fixed ordering, each stage its own boolean gate like the
existing `_should_hunt`. The scheduler consumes the output; the policy engine
still holds the caps (the chooser never unlocks anything the engine forbids).

Priority (highest first):
  1. complaints       — an angry client beats everything (§0.4)
  2. retainers at risk — revenue already sold is bleeding
  3. replies due      — pipeline conversations waiting on us
  4. hunt             — new leads IF the policy gates open (_should_hunt)
  5. build demos      — hot leads that lack a demo
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WorkItem:
    kind: str                      # serve_complaints | serve_retainers | follow_up | hunt | build_demo
    reason: str
    gate: str                      # which boolean gate allowed it (evidence)
    payload: Dict[str, Any] = field(default_factory=dict)


def _complaints_gate(snapshot) -> Optional[WorkItem]:
    if (snapshot.open_complaints or 0) > 0:
        return WorkItem("serve_complaints",
                        f"{snapshot.open_complaints} open complaint(s) — nothing else matters first",
                        "open_complaints>0")
    return None


def _retainers_gate(snapshot, retainer_store=None) -> Optional[WorkItem]:
    if (snapshot.incidents_over_slo or 0) > 0:
        return WorkItem("serve_retainers",
                        f"{snapshot.incidents_over_slo} retainer incident(s) over SLO "
                        "— revenue at risk", "incidents_over_slo>0",
                        payload={"incidents": snapshot.incidents_over_slo})
    return None


def _followups_gate(snapshot) -> Optional[WorkItem]:
    if (snapshot.client_replies_waiting or 0) > 0:
        return WorkItem("follow_up",
                        f"{snapshot.client_replies_waiting} client reply/replies waiting",
                        "client_replies_waiting>0",
                        payload={"waiting": snapshot.client_replies_waiting})
    return None


def _hunt_gate(snapshot, knobs: Optional[Dict[str, Any]] = None) -> Optional[WorkItem]:
    """The same boolean gates the policy engine's _should_hunt applies — the
    chooser re-checks them so a work item is never proposed the engine would
    refuse. Where the two disagree, the ENGINE wins (chooser never unlocks).
    SERVICE-FIRST: any open service work suppresses hunting entirely — the
    engine holds GROW while service intents exist, so must the chooser."""
    if any((snapshot.open_complaints or 0, snapshot.incidents_over_slo or 0,
            snapshot.client_replies_waiting or 0)):
        return None
    knobs = knobs or {}
    idle_hours = float(knobs.get("idle_hours_before_hunt", 24))
    quota_floor = float(knobs.get("hunt_quota_floor_pct", 90))
    last_age = snapshot.last_closed_deal_age_h
    idle_ok = last_age is None or last_age >= idle_hours
    quota_ok = (snapshot.quota_remaining_pct or 0.0) >= quota_floor
    within_hours = bool(snapshot.within_business_hours)
    if idle_ok and quota_ok and within_hours and not snapshot.halted:
        return WorkItem("hunt",
                        f"no deal in {last_age if last_age is not None else '∞'}h, "
                        f"{snapshot.quota_remaining_pct:.0f}% quota left, business hours",
                        "_should_hunt gates all open",
                        payload={"max_new_contacts": int(knobs.get("max_new_contacts_per_day", 50))})
    return None


def _build_gate(snapshot, crm=None) -> Optional[WorkItem]:
    if (snapshot.hot_leads_waiting or 0) > 0 and not snapshot.halted:
        return WorkItem("build_demo",
                        f"{snapshot.hot_leads_waiting} hot lead(s) mid-pipeline need demos",
                        "hot_leads_waiting>0",
                        payload={"hot_leads": snapshot.hot_leads_waiting})
    return None


def choose_work(snapshot, crm=None, retainer_store=None,
                knobs: Optional[Dict[str, Any]] = None) -> List[WorkItem]:
    """Returns the ordered work list for this cycle. Deterministic: the same
    snapshot + stores → the same list, same order, same reasons."""
    if snapshot.halted:
        return []   # the throat-cut handle empties the board instantly
    items: List[WorkItem] = []
    for gate in (_complaints_gate, _retainers_gate, _followups_gate,
                 lambda s: _hunt_gate(s, knobs),
                 lambda s: _build_gate(s, crm)):
        item = gate(snapshot)
        if item:
            items.append(item)
    return items
