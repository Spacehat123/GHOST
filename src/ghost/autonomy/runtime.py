"""Autonomy Runtime — THE assembly point. Wires every organ into a running body (Roadmap wiring gap).

Before this module, P0-P5 were tested libraries with no caller. This is the
single place where:
  - the heartbeat beats (§3.3)
  - the policy scheduler decides (§1) with REAL telemetry (CRM counts, token
    quota, retainer incidents, genuine heartbeat age)
  - granted lead-hunt jobs are executed by workers on the durable queue
  - the daily digest is built and written at 09:00 IST (§10)
  - stale heartbeat freezes outbound via halt (§3.3 teeth)

Started by `ghost run` (headless) and by server startup (FastAPI lifespan).
Everything here is halt-aware: tripping the halt stands the runtime down at
the next safe point, in under a poll interval.
"""
import asyncio
import logging
import os
from typing import Any, Dict, Optional

from ghost.core.audit import audit
from ghost.core import heartbeat
from ghost.core.halt import is_halted
from ghost.core.jobqueue import JobQueue
from ghost.autonomy.scheduler import TelemetryProvider, run_scheduler
from ghost.autonomy.digest import build_digest, write_digest_file

logger = logging.getLogger("ghost.runtime")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))


class AutonomyRuntime:
    """Owns the background tasks. One instance per process."""

    def __init__(self, scheduler_interval_s: float = 300.0,
                 queue_poll_s: float = 1.0,
                 with_lead_hunt: bool = True):
        self.scheduler_interval_s = scheduler_interval_s
        self.queue_poll_s = queue_poll_s
        self.with_lead_hunt = with_lead_hunt
        self.queue: Optional[JobQueue] = None
        self._tasks: list = []
        self.stopped = False

    # ------------------------------------------------------------------ handlers

    def _build_handlers(self) -> Dict[str, Any]:
        handlers: Dict[str, Any] = {}

        if self.with_lead_hunt:
            async def find_leads(payload: Dict[str, Any]) -> Any:
                """Execute a granted hunt: policy said go, so call the real
                hunter with the grant bounds. Evidence lands in the ledger."""
                from ghost.core.halt import assert_not_halted
                assert_not_halted("lead_hunt.find_leads")
                from ghost.workers.lead_hunter import LeadHunter
                from ghost.crm.db import CRMDatabase
                hunter = LeadHunter(crm=CRMDatabase.get_instance())
                max_contacts = int(payload.get("max_new_contacts", 0) or 0)
                if max_contacts <= 0:
                    audit("lead_hunt.skipped", reason="zero grant")
                    return {"hunted": 0}
                city = os.environ.get("GHOST_HUNT_CITY", "Bengaluru")
                result = await hunter.harvest(
                    city=city, max_leads=max_contacts)
                audit("lead_hunt.completed", city=city,
                      grant=max_contacts, result=str(result)[:300])
                return result
            handlers["lead_hunt.find_leads"] = find_leads

        # Close-chain handlers from P2 (contract/welcome) ride the same queue.
        try:
            from ghost.workers.close_chain import make_handlers
            handlers.update(make_handlers())
        except Exception as e:
            logger.warning("close_chain handlers unavailable: %s", e)

        return handlers

    # ------------------------------------------------------------------- tasks

    async def _heartbeat_loop(self) -> None:
        """Beat every HEARTBEAT_INTERVAL_S; enforce staleness teeth (§3.3)."""
        while not self.stopped:
            heartbeat.beat("runtime")
            # Enforce AFTER beating: only trips if BEATING itself went stale
            # (e.g. this loop froze) — checked by the separate enforcer below.
            await asyncio.sleep(heartbeat.HEARTBEAT_INTERVAL_S)

    async def _staleness_enforcer_loop(self) -> None:
        """Independent loop: if the heartbeat is stale (runtime frozen) and we
        somehow still run, freeze outbound. Belt and suspenders, per §3.3."""
        while not self.stopped:
            heartbeat.enforce_stale()
            await asyncio.sleep(heartbeat.STALE_AFTER_S / 2)

    async def _scheduler_loop(self) -> None:
        telemetry = TelemetryProvider()
        engine = _build_engine()
        ping = _operator_ping()
        digest_hook = self._make_digest_hook()

        def enqueue_with_audit(kind: str, payload: Dict[str, Any],
                               idempotency_key: Optional[str] = None, **kw) -> Any:
            if self.queue is None:
                audit("scheduler.simulate_enqueue", kind=kind, key=idempotency_key)
                return None
            return self.queue.enqueue(kind, payload, idempotency_key=idempotency_key)

        await run_scheduler(
            telemetry=telemetry, engine=engine,
            enqueue=enqueue_with_audit, ping=ping,
            interval_s=self.scheduler_interval_s,
            digest_hook=digest_hook,
        )

    def _make_digest_hook(self):
        def hook() -> None:
            # Daily dual-read parity (T1c): in unified mode, verify no legacy
            # history went missing before the digest reports the day.
            try:
                from ghost.store import facade, parity
                if facade.unified():
                    result = parity.run()
                    audit("parity.daily", ok=result.get("ok"),
                          drift_days=result.get("events", {}).get("drift_days", [])[:5])
            except Exception as e:
                audit("parity.daily_failed", error=str(e)[:200])
            # T5: daily reconciliation — records vs reality (fetchers resolve
            # to honest not_configured when the operator's keys are absent)
            try:
                from ghost.verify import reconciliation as reco
                reco.run_all(payment_fetcher=reco.razorpay_payment_fetcher())
            except Exception as e:
                audit("reconcile.failed", error=str(e)[:200])
            content = build_digest()
            path = write_digest_file(content, repo_dir=REPO_DIR)
            audit("digest.written", path=path)
            try:
                from ghost.core import rhythm
                rhythm.mark("digest")
            except Exception:
                pass
            try:
                from ghost.watchdog.service import AutonomousWatchdog
                wd = AutonomousWatchdog.get_instance()
                wd._sync_notify("Ghost Daily Digest", f"Written to {path}")
            except Exception:
                pass
        return hook

    # ------------------------------------------------------------------ public

    async def start(self) -> "AutonomyRuntime":
        """Start every background organ. Idempotent."""
        if self._tasks:
            return self
        self.queue = JobQueue()
        heartbeat.beat("runtime-start")
        self._tasks = [
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
            asyncio.create_task(self._staleness_enforcer_loop(), name="staleness"),
            asyncio.create_task(self._scheduler_loop(), name="scheduler"),
            asyncio.create_task(
                self.queue.run_worker(self._build_handlers(),
                                      poll_interval=self.queue_poll_s),
                name="queue-worker"),
        ]
        audit("runtime.started", tasks=[t.get_name() for t in self._tasks])
        logger.info("AutonomyRuntime started: %s", [t.get_name() for t in self._tasks])
        return self

    async def stop(self) -> None:
        self.stopped = True
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        audit("runtime.stopped")

    @property
    def status(self) -> Dict[str, Any]:
        from ghost.core import halt as halt_mod
        return {
            "running": bool(self._tasks) and not self.stopped,
            "tasks": [t.get_name() for t in self._tasks],
            "halted": halt_mod.is_halted(),
            "halt_state": halt_mod.halt_state(),
            "heartbeat_age_s": heartbeat.age_s(),
            "queue": self.queue.stats() if self.queue else {},
        }


def _build_engine():
    from ghost.policy.engine import PolicyEngine, load_config
    return PolicyEngine(load_config())


def _operator_ping():
    """The (a)/(b)/(c) channel: desktop notification + watchdog broadcast + ledger."""
    def ping(category: str, payload: Dict[str, Any]) -> None:
        audit("operator.ping", category=category, payload=payload)
        text = f"[{category}] {payload.get('kind', payload.get('ping', 'attention needed'))}"
        try:
            from ghost.watchdog.service import AutonomousWatchdog
            AutonomousWatchdog.get_instance()._sync_notify("Ghost", text[:180])
        except Exception:
            pass
        try:
            import subprocess
            subprocess.run(["notify-send", "-a", "Ghost", "Ghost needs you", text[:180]],
                           timeout=5)
        except Exception:
            pass
    return ping


_runtime: Optional[AutonomyRuntime] = None


async def get_or_start_runtime() -> AutonomyRuntime:
    """Server startup + CLI both land here. One runtime per process."""
    global _runtime
    if _runtime is None:
        _runtime = AutonomyRuntime()
    if not _runtime._tasks:
        await _runtime.start()
    return _runtime


def get_runtime() -> Optional[AutonomyRuntime]:
    """The process's runtime, if started. Server shutdown + /api/runtime/status use this."""
    return _runtime
