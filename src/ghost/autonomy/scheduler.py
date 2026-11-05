"""Autonomy Scheduler — the heartbeat that turns policy into work (Roadmap §1).

One async loop, one cycle:
  1. Build a StateSnapshot from live telemetry (providers injected; tests fake them).
  2. PolicyEngine.decide() — deterministic, audited.
  3. Dispatch: GROW grants enqueue lead-hunt jobs (bounded by the grant);
     pings go to the operator; HOLD_FIRE touches nothing.
  4. At ~09:00 IST: build + write the daily digest.

The loop NEVER sends mail, NEVER builds demos itself — it only grants and
enqueues. Workers with halt-gates do the acting.
"""
import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

from ghost.core import heartbeat
from ghost.core.audit import audit
from ghost.core.halt import is_halted
from ghost.policy.engine import PolicyEngine, StateSnapshot, load_config

logger = logging.getLogger("ghost.autonomy.scheduler")


class TelemetryProvider:
    """Source of truth for the snapshot. Subclass/override for real wiring;
    tests inject fakes. Defaults are maximally conservative (fail closed)."""

    def __init__(self, crm=None, queue=None, knobs: Optional[Dict[str, Any]] = None,
                 business_hours_override: Optional[bool] = None):
        self.crm = crm
        self.queue = queue
        self.knobs = knobs or {}
        self.business_hours_override = business_hours_override

    def snapshot(self) -> StateSnapshot:
        halted_state = None
        try:
            from ghost.core import halt as halt_mod
            halted_state = halt_mod.halt_state()
        except Exception:
            halted_state = {"reason": "telemetry_error"}

        # --- CRM-derived counts (fail closed on any error) ------------------
        open_complaints = hot_leads = replies_waiting = 0
        pipeline_value = 0.0
        last_deal_age_h = None
        try:
            if self.crm is not None:
                counts = self.crm.pipeline_counts()  # provided by CRMDatabase
                open_complaints = int(counts.get("open_complaints", 0))
                hot_leads = int(counts.get("hot_leads", 0))
                replies_waiting = int(counts.get("replies_waiting", 0))
                pipeline_value = float(counts.get("pipeline_value_inr", 0))
                last_deal_age_h = counts.get("last_closed_deal_age_h")
        except Exception:
            # Cannot read the CRM -> assume the worst: something needs a human.
            open_complaints = max(open_complaints, 1)

        quota_pct = 0.0
        try:
            if self.crm is not None and hasattr(self.crm, "quota_remaining_pct"):
                quota_pct = float(self.crm.quota_remaining_pct())
        except Exception:
            quota_pct = 0.0

        contacts_sent = 0
        try:
            if self.queue is not None:
                contacts_sent = int(getattr(self.queue, "sends_today", lambda: 0)())
        except Exception:
            contacts_sent = 0

        return StateSnapshot(
            halted=is_halted(),
            halt_reason=(halted_state or {}).get("reason"),
            open_complaints=open_complaints,
            domain_health="green",   # KNOWN PLACEHOLDER — wired to site_monitor in T5
            heartbeat_age_s=heartbeat.age_s(),   # real watchdog age; missing marker = inf (fail closed)
            incidents_over_slo=0,    # KNOWN PLACEHOLDER — wired to RetainerStore in T5
            client_replies_waiting=replies_waiting,
            hot_leads_waiting=hot_leads,
            pipeline_value_inr=pipeline_value,
            last_closed_deal_age_h=last_deal_age_h,
            quota_remaining_pct=quota_pct,
            contacts_sent_today=contacts_sent,
            within_business_hours=self._business_hours(),
        )

    def _business_hours(self) -> bool:
        if self.business_hours_override is not None:
            return bool(self.business_hours_override)
        # IST 10:00–19:00 window: cold outreach never at night (§1.1).
        ist_hour = time.gmtime(time.time() + 5.5 * 3600).tm_hour
        return 10 <= ist_hour < 19


async def run_scheduler(
    telemetry: TelemetryProvider,
    engine: Optional[PolicyEngine] = None,
    enqueue: Optional[Callable[[str, Dict[str, Any], Optional[str]], Any]] = None,
    ping: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    interval_s: float = 300.0,
    max_cycles: Optional[int] = None,
    digest_hook: Optional[Callable[[], Any]] = None,
) -> int:
    """Run scheduler cycles until halted (or max_cycles for tests).
    Returns cycles completed."""
    engine = engine or PolicyEngine(load_config())
    enqueue = enqueue or _default_enqueue
    cycles = 0

    while True:
        if is_halted():
            logger.info("scheduler: halt active — standing down (workers also pause)")
            audit("scheduler.stood_down", reason="halt")
            return cycles

        decision = engine.decide(telemetry.snapshot())
        audit("scheduler.cycle", subject=decision.decision_id,
              intent=decision.intent, grants=[g.intent for g in decision.grants])

        for grant in decision.grants:
            if grant.intent == "GROW" and grant.max_new_contacts > 0:
                # One hunting job per cycle; the hunter enqueues per-lead work
                # bounded by the grant it reads from the payload.
                enqueue("lead_hunt.find_leads", {
                    "max_new_contacts": grant.max_new_contacts,
                    "max_build_cost_inr": grant.max_build_cost_inr,
                    "decision_id": decision.decision_id,
                }, idempotency_key=f"hunt:{decision.decision_id}")

        # Expected-rhythm marker (T4): the loop RAN and decided. The supervisor
        # alarms if no marker appears by the morning deadline (file only).
        try:
            from ghost.core import rhythm
            rhythm.mark("scheduler_cycle")
        except Exception:
            pass

        for p in decision.pings:
            if ping:
                ping("(c) unverifiable", {"decision": decision.decision_id, "ping": p})

        if digest_hook and _digest_time():
            try:
                digest_hook()
            except Exception as e:
                audit("digest.failed", error=str(e)[:200])

        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return cycles
        await asyncio.sleep(interval_s)


def _digest_time() -> bool:
    """True in the 09:00 IST hour, once (cache marker on file)."""
    import os
    marker = os.environ.get("GHOST_DIGEST_MARKER") or "/tmp/ghost_digest_marker"
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 5.5 * 3600))
    ist_hour = time.gmtime(time.time() + 5.5 * 3600).tm_hour
    if ist_hour == 9:
        try:
            with open(marker) as f:
                if f.read().strip() == today:
                    return False
        except FileNotFoundError:
            pass
        with open(marker, "w") as f:
            f.write(today)
        return True
    return False


def _default_enqueue(kind: str, payload: Dict[str, Any], key: Optional[str] = None) -> None:
    """No queue configured -> log-only (L0 simulate). Never lose the decision."""
    logger.info("SIMULATE enqueue %s key=%s payload=%s", kind, key, payload)
    audit("scheduler.simulate_enqueue", kind=kind, key=key)
