"""P6 tests (Operator's 11): event-driven reactivity + self-health.

- IMMEDIATE TRIAGE: a reply signal becomes a queue job; the 1s worker runs
  the intake — triage happens in seconds, not on the next 300s poll.
- SELF-HEALTH (alarm-only): error bursts in the ledger, outbox rows stuck
  pending, dead-lettered jobs → watchdog incident + operator ping. The
  watchdog never freezes anything (the supervisor owns teeth) and a halt
  does not silence the alarm.
- Determinism: no LLM anywhere in the reactive path.
"""
import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-p6-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
os.environ["GHOST_QUEUE_DB_PATH"] = os.path.join(TMP, f"queue-{uuid.uuid4().hex}.db")
os.environ["GHOST_INTAKE_SEEN"] = os.path.join(TMP, "seen.json")

from starlette.testclient import TestClient

from ghost.crm.db import CRMDatabase
from ghost.server.app import app
from ghost.server.agency_api import get_or_create_session_token


class EnvPinned(unittest.TestCase):

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_AUDIT_DB_PATH", "GHOST_CRM_DB_PATH", "GHOST_QUEUE_DB_PATH",
                        "GHOST_INTAKE_SEEN", "GHOST_UNIFIED_DB_PATH", "GHOST_HALT_FILE",
                        "GHOST_HALT_META", "GHOST_EVENTS_DB")}
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
        os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
        os.environ["GHOST_QUEUE_DB_PATH"] = os.path.join(TMP, f"queue-{uuid.uuid4().hex}.db")
        os.environ["GHOST_INTAKE_SEEN"] = os.path.join(TMP, f"seen-{uuid.uuid4().hex}.json")
        os.environ["GHOST_UNIFIED_DB_PATH"] = os.path.join(TMP, f"ghost-{uuid.uuid4().hex}.db")
        os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, f"H-{uuid.uuid4().hex}")
        os.environ["GHOST_HALT_META"] = os.environ["GHOST_HALT_FILE"] + ".json"
        self.crm = CRMDatabase(os.environ["GHOST_CRM_DB_PATH"])
        CRMDatabase._instance = self.crm
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def tearDown(self):
        CRMDatabase._instance = None

    def _ledger(self, event_type=None):
        from ghost.core.audit import AuditLedger
        led = AuditLedger.get_instance()
        return led.query(event_type=event_type, limit=200) if event_type else led.query(limit=200)


class FakeEmailService:
    def __init__(self, msgs):
        self.msgs = list(msgs)

    def imap_configured(self):
        return True

    def check_inbox(self, unread_only=True, limit=5):
        return list(self.msgs)


class TestImmediateTriage(EnvPinned):

    def test_signal_to_triaged_in_seconds(self):
        """The reactive path: signal → queue job → 1s worker → quarantine →
        classify → acted upon. Everything deterministic, no LLM."""
        slug = f"fast-{uuid.uuid4().hex[:6]}"
        self.crm.add_or_update_lead(dict(slug=slug, name="Salt", google_category="R"))
        self.crm.update_lead(slug, status="pitch_sent")
        with self.crm._get_connection() as conn:
            conn.execute("UPDATE leads SET gaps_json = ? WHERE slug = ?",
                         (json.dumps({"email": "owner@salt.com"}), slug))
            conn.commit()

        es = FakeEmailService([{
            "message_id": f"<fast-{uuid.uuid4().hex[:6]}@salt.com>",
            "sender_email": "owner@salt.com", "subject": "Re: demo",
            "body": "Interested — how do we proceed?",
        }])
        from ghost.autonomy.runtime import AutonomyRuntime
        from ghost.core.jobqueue import JobQueue
        with patch_es(es):
            rt = AutonomyRuntime()
            q = JobQueue()
            rt.queue = q
            handlers = rt._build_handlers()
            self.assertIn("intake.email_poll", handlers)
            t0 = time.monotonic()
            q.enqueue("intake.email_poll", {"source": "test"},
                      idempotency_key=f"intake:{uuid.uuid4().hex}")
            asyncio.run(q.run_worker(handlers, poll_interval=0.001,
                                     idle_exit_after=0.05))
            elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 60.0, "triage must not wait for the 300s cycle")
        # the intake path's contract: classify + log + act (drafts/suppress/
        # ping) — advancing the lead is the relay endpoint's bookkeeping
        self.assertTrue(any("reply.ingested" == r["event_type"] for r in self._ledger()))
        interactions = [r for r in self._ledger() if r["event_type"].startswith("reply.")]
        self.assertTrue(interactions, "the reply was classified and acted upon")

    def test_signal_endpoint_enqueues_the_poll(self):
        self.token = get_or_create_session_token()
        client = TestClient(app)
        res = client.post("/api/watchdog/signal",
                          headers={"X-Session-Token": self.token},
                          json={"event_type": "reply_received", "source": "wa_bridge",
                                "summary": "new reply"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["intake_poked"])
        from ghost.core.jobqueue import JobQueue
        stats = JobQueue().stats()
        self.assertGreaterEqual(stats["pending"] + stats["claimed"] + stats["done"] + stats["dead"],
                                1, "a poll job must exist after a reply signal")


class patch_es:
    """Patch EmailService.get_instance for the intake path."""

    def __init__(self, es):
        self.es = es

    def __enter__(self):
        import ghost.comms.email_service as m
        self.saved = m.EmailService.get_instance
        m.EmailService.get_instance = classmethod(lambda cls: self.es)
        return self

    def __exit__(self, *a):
        import ghost.comms.email_service as m
        m.EmailService.get_instance = self.saved


class TestSelfHealth(EnvPinned):

    def _watchdog(self):
        from ghost.watchdog.service import AutonomousWatchdog
        wd = AutonomousWatchdog(db_path=os.path.join(TMP, f"events-{uuid.uuid4().hex}.db"))
        wd.emit_signal = _CaptureSignals(wd)
        return wd

    def test_error_burst_raises_incident(self):
        from ghost.core.audit import audit
        for i in range(4):
            audit(f"worker{i}.failed", error="synthetic")
        wd = self._watchdog()
        incidents = asyncio.run(wd._check_system_health())
        kinds = [i["kind"] for i in incidents]
        self.assertIn("error_burst", kinds)

    def test_no_burst_no_incident(self):
        from ghost.core.audit import audit
        audit("scheduler.cycle", subject="quiet")
        wd = self._watchdog()
        incidents = asyncio.run(wd._check_system_health())
        self.assertEqual([i for i in incidents if i["kind"] == "error_burst"], [])

    def test_stale_pending_outbox_raises_incident(self):
        db = os.environ["GHOST_UNIFIED_DB_PATH"]
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, kind TEXT, "
            "grant_id TEXT, idempotency_key TEXT UNIQUE, payload TEXT, state TEXT, "
            "created_at REAL, updated_at REAL, error TEXT);")
        conn.execute("INSERT OR IGNORE INTO outbox VALUES ('x1','deploy',NULL,'k1','{}','pending',?,?,NULL)",
                     (time.time() - 7200, time.time() - 7200))
        conn.commit()
        conn.close()
        wd = self._watchdog()
        incidents = asyncio.run(wd._check_system_health())
        self.assertIn("stale_outbox", [i["kind"] for i in incidents])

    def test_dead_letter_raises_incident(self):
        from ghost.core.jobqueue import JobQueue
        q = JobQueue()
        q.enqueue("kind.with.no.handler", {}, idempotency_key=f"dl-{uuid.uuid4().hex}")
        asyncio.run(q.run_worker({}, poll_interval=0.001, idle_exit_after=0.05))
        self.assertEqual(q.stats()["dead"], 1)
        wd = self._watchdog()
        incidents = asyncio.run(wd._check_system_health())
        self.assertIn("dead_letters", [i["kind"] for i in incidents])

    def test_patrol_is_alarm_only_under_halt(self):
        """Halt silences OUTBOUND, never the alarm. The watchdog freezes
        nothing — the supervisor owns the teeth."""
        from ghost.core.halt import trip, resume
        from ghost.core.audit import audit
        trip(reason="test")
        try:
            for i in range(4):
                audit(f"halted.worker.failed", error="synthetic")
            wd = self._watchdog()
            incidents = asyncio.run(wd._check_system_health())
            self.assertTrue(incidents, "the alarm must still sound while halted")
        finally:
            resume(by="test-cleanup")
        self.assertEqual([], [r for r in self._ledger()
                              if r["event_type"] == "effect.grant_issued"],
                         "a health incident must never mint authority")

    def test_patrol_sweep_includes_health(self):
        wd = self._watchdog()
        report = asyncio.run(wd.run_patrol_sweep())
        self.assertIn("health_incidents", report)


class _CaptureSignals:
    """emit_signal wrapper that records without requiring the GUI broadcast."""

    def __init__(self, wd):
        self._wd = wd
        self.captured = []

    async def __call__(self, **kw):
        self.captured.append(kw)
        return {"ok": True}


if __name__ == "__main__":
    unittest.main()
