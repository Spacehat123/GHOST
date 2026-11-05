"""T0 truth-pass tests (ROADMAP_TOTAL_AUTONOMY.md): the scheduler reads the
real heartbeat age, the email landmine is gone (drafts, never sends), and the
close chain's default welcome path degrades to a draft instead of raising."""
import os
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-t0-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_HEARTBEAT_PATH"] = os.path.join(TMP, f"hb-{uuid.uuid4().hex}.json")

from ghost.core import heartbeat
from ghost.core.audit import AuditLedger
from ghost.autonomy.scheduler import TelemetryProvider


class TestRealHeartbeatInSnapshot(unittest.TestCase):

    def setUp(self):
        self._old = os.environ.get("GHOST_HEARTBEAT_PATH")
        os.environ["GHOST_HEARTBEAT_PATH"] = os.path.join(TMP, f"hb-{uuid.uuid4().hex}.json")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("GHOST_HEARTBEAT_PATH", None)
        else:
            os.environ["GHOST_HEARTBEAT_PATH"] = self._old

    def test_fresh_beat_gives_small_real_age(self):
        heartbeat.beat("test")
        snap = TelemetryProvider().snapshot()
        self.assertLess(snap.heartbeat_age_s, 60.0)
        self.assertAlmostEqual(snap.heartbeat_age_s, heartbeat.age_s(), delta=1.0)

    def test_missing_marker_is_infinite_not_1_point_0(self):
        # No beat written to this scoped path -> fail closed, never the old lie.
        snap = TelemetryProvider().snapshot()
        self.assertEqual(snap.heartbeat_age_s, float("inf"))
        validated = snap.validated()
        self.assertGreaterEqual(validated.heartbeat_age_s, 1e9)


class TestEmailLandmineRemoved(unittest.TestCase):

    def test_send_email_is_gone(self):
        from ghost.comms.email_service import EmailService
        self.assertFalse(hasattr(EmailService, "send_email"),
                         "send_email must not exist — one send path only")

    def test_draft_email_writes_crm_draft_never_sends(self):
        from ghost.crm.db import CRMDatabase
        from ghost.comms.email_service import EmailService
        crm = CRMDatabase(os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db"))
        svc = EmailService()
        out = svc.draft_email(to_email="client@example.com", subject="Welcome",
                              body="Payment confirmed.", crm=crm)
        self.assertTrue(out["drafted"])
        self.assertFalse(out["sent"])
        drafts = crm.get_drafts_for_lead("client@example.com")
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["subject"], "Welcome")
        # The audit trail records the draft, not a fake send.
        rows = AuditLedger.get_instance().query(event_type="email.drafted", limit=10)
        self.assertTrue(any(r.get("subject") == "client@example.com" for r in rows))
        sent = AuditLedger.get_instance().query(event_type="email.sent", limit=10)
        self.assertEqual(sent, [])

    def test_draft_survives_crm_failure_via_file(self):
        from ghost.comms.email_service import EmailService

        class Boom:
            def add_or_update_draft(self, **kw):
                raise RuntimeError("crm down")

        svc = EmailService()
        out = svc.draft_email(to_email="x@y.z", subject="s", body="b", crm=Boom())
        self.assertTrue(out["drafted"])
        self.assertEqual(out["queue"], "file")
        self.assertTrue(os.path.exists(out["path"]))


class TestCloseChainDefaultDrafts(unittest.TestCase):

    def test_default_welcome_delivery_drafts_instead_of_raising(self):
        from ghost.workers.close_chain import _default_welcome_delivery
        out = _default_welcome_delivery({"lead_slug": "test-lead",
                                         "invoice_number": "INV-1"})
        self.assertTrue(out["drafted"])
        self.assertFalse(out["sent"])

    def test_send_welcome_audits_drafted_for_default(self):
        import asyncio
        from ghost.workers.close_chain import make_handlers
        from ghost.core.audit import audit
        handlers = make_handlers()  # no injected sender -> default draft path
        # Payment must be verified on the ledger first (honesty gate).
        AuditLedger.get_instance().record("payment.verified", subject="evt_t0")
        out = asyncio.run(handlers["close_chain.send_welcome"](
            {"lead_slug": "lead-t0", "event_id": "evt_t0", "invoice_number": "INV-2"}))
        self.assertEqual(out["welcomed"], "lead-t0")
        rows = AuditLedger.get_instance().query(event_type="welcome.drafted", limit=10)
        self.assertTrue(any(r.get("subject") == "lead-t0" for r in rows))


if __name__ == "__main__":
    unittest.main()
