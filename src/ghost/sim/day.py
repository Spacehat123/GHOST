"""SIM — the simulated day (T6b): 50 fixture leads, zero operator input,
deterministic end to end.

Assembles the REAL machinery — PolicyEngine, TelemetryProvider, scheduler
loop, durable JobQueue, reply classifier — on a FakeClock with fixture leads
and fake effects (hunt contacts fixtures, build marks demos). The walls are
fake; the organs are real. Exit property: the same seed produces the SAME
sequence of decision_ids and the same final state, so `ghost replay --day`
derives identical day-state from either run's events.
"""
import asyncio
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from ghost.core.audit import AuditLedger
from ghost.core.jobqueue import JobQueue
from ghost.policy.engine import PolicyEngine, load_config
from ghost.sim.clock import FakeClock, ist_hour
from ghost.sim.fixtures import fixture_leads, fixture_replies


class FakeCRM:
    """Fixture-backed CRM exposing exactly the telemetry surface the
    scheduler reads, plus the mutation surface the sim handlers use."""

    def __init__(self, leads: List[Dict[str, Any]]):
        self.leads: Dict[str, Dict[str, Any]] = {l["slug"]: dict(l) for l in leads}
        self.order: List[str] = [l["slug"] for l in leads]
        self.contact_count = 0
        self.complaints = 0
        self.replies_waiting = 0

    # telemetry surface -----------------------------------------------------
    def pipeline_counts(self) -> Dict[str, Any]:
        hot = sum(1 for l in self.leads.values() if l["status"] in ("demo_built", "pitch_sent"))
        return {"open_complaints": self.complaints, "hot_leads": hot,
                "replies_waiting": self.replies_waiting,
                "pipeline_value_inr": float(hot * 25000),
                "last_closed_deal_age_h": None}

    def quota_remaining_pct(self) -> float:
        return max(0.0, 100.0 - self.contact_count * 2.0)

    # mutation surface ------------------------------------------------------
    def contact(self, slug: str) -> None:
        self.contact_count += 1
        self.leads[slug]["status"] = "pitch_sent"

    def mark_built(self, slug: str) -> None:
        self.leads[slug]["status"] = "demo_built"

    def state_summary(self) -> Dict[str, Any]:
        statuses: Dict[str, int] = {}
        for l in self.leads.values():
            statuses[l["status"]] = statuses.get(l["status"], 0) + 1
        return {"statuses": dict(sorted(statuses.items())),
                "contacted": self.contact_count,
                "pipeline_value_inr": self.pipeline_counts()["pipeline_value_inr"]}


def run_simulated_day(seed: int = 20261106, n_leads: int = 50, cycles: int = 5,
                      work_dir: Optional[str] = None) -> Dict[str, Any]:
    """One simulated day. Deterministic: same seed -> same decision_ids,
    same final state. Returns the decision_id sequence + summary."""
    work_dir = work_dir or tempfile.mkdtemp(prefix="ghost-sim-day-")
    os.environ.setdefault("GHOST_HEARTBEAT_PATH", os.path.join(work_dir, "hb.json"))
    os.environ.setdefault("GHOST_RHYTHM_DIR", os.path.join(work_dir, "rhythm"))
    from ghost.core import heartbeat
    heartbeat.beat("sim")   # T0: a missing marker fail-closes the snapshot

    clock = FakeClock()
    crm = FakeCRM(fixture_leads(n_leads, seed))
    ledger = AuditLedger(os.path.join(work_dir, f"audit-{seed}.db"))
    # the sim's organs audit through the env-resolved ledger — point the env
    # at the sim's own ledger so NOTHING leaks into the operator's store
    os.environ["GHOST_AUDIT_DB_PATH"] = ledger.db_path
    queue = JobQueue(os.path.join(work_dir, f"queue-{seed}.db"))
    engine = PolicyEngine(load_config())
    decision_ids: List[str] = []
    pings: List[str] = []

    async def hunt(payload: Dict[str, Any]) -> Any:
        max_new = int(payload.get("max_new_contacts", 50))
        candidates = [s for s in crm.order
                      if crm.leads[s]["status"] == "scouted"][:max_new]
        for slug in candidates:
            queue.enqueue("demo.build", {"slug": slug},
                          idempotency_key=f"build:{slug}")
            crm.contact(slug)
        ledger.record("outbound.sent", subject="sim-batch",
                      count=len(candidates), kind="sim")
        return {"contacted": len(candidates)}

    async def build(payload: Dict[str, Any]) -> Any:
        slug = payload.get("slug")
        crm.mark_built(slug)
        ledger.record("demo.built", subject=slug, kind="sim")
        return {"built": slug}

    handlers = {"lead_hunt.find_leads": hunt, "demo.build": build}

    async def day() -> None:
        for _ in range(cycles):
            hour = ist_hour(clock)
            from ghost.autonomy.scheduler import TelemetryProvider, run_scheduler
            telemetry = TelemetryProvider(crm=crm, queue=queue,
                                          business_hours_override=(10 <= hour < 19),
                                          heartbeat_age_override=5.0)   # sim time, not wall time
            await run_scheduler(
                telemetry=telemetry, engine=engine,
                enqueue=lambda k, p, idempotency_key=None, **kw:
                    queue.enqueue(k, p, idempotency_key=idempotency_key),
                ping=lambda c, p: pings.append(c),
                interval_s=0, max_cycles=1)
            # record the decision this cycle made (the engine is deterministic
            # given the snapshot — pull it back off the ledger for the receipt)
            rows = ledger.query(event_type="scheduler.cycle", limit=1)
            if rows:
                decision_ids.append(str(rows[0].get("subject")))
            await queue.run_worker(handlers, poll_interval=0.001, idle_exit_after=0.01)
            clock.advance(3600)

    # the day's inbox: fixture replies classified deterministically
    from ghost.core.reply_classifier import classify_reply
    reply_categories = sorted(classify_reply(r["text"]).category
                              for r in fixture_replies(seed))

    asyncio.run(day())

    from ghost.store.replay import derive_day
    day_str = time.strftime("%Y-%m-%d", time.gmtime(ledger.query(limit=1)[0]["ts"] \
        if ledger.query(limit=1) else time.time()))
    return {
        "decision_ids": decision_ids,
        "reply_categories": reply_categories,
        "final_state": crm.state_summary(),
        "derived_day": derive_day(day_str, ledger),
        "work_dir": work_dir,
    }
