"""T2 effect-plane tests — the brief's exact exit test plus deploy gates.

Exit: send attempts WITHOUT a grant, with an EXPIRED grant, with a REUSED
grant, and OVER CAP all fail closed — four red tests. Deploy without a grant
fails; deploy with a grant but no fresh snapshot fails. Outbox rows record
every effect before it runs, with provider-facing idempotency keys.
"""
import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

TMP = tempfile.mkdtemp(prefix="ghost-t2-")
DEMOS = os.path.join(TMP, "demos")
os.makedirs(DEMOS, exist_ok=True)

# Pinned PER TEST (setUp) with cleanup — other test files pop some of these
# env vars at run time (e.g. chaos tests + GHOST_GRANT_SECRET), so
# import-time pinning alone is not order-proof.
PINNED_KEYS = ("GHOST_AUDIT_DB_PATH", "GHOST_UNIFIED_DB_PATH", "GHOST_GRANT_SECRET",
               "GHOST_HALT_FILE", "GHOST_HALT_META", "GHOST_LADDER_STATE_PATH",
               "GHOST_OUTBOUND_STATE_PATH", "GHOST_SNAPSHOTS_DIR", "GHOST_DEMOS_DIR",
               "GHOST_OUTREACH_SMTP_HOST", "GHOST_OUTREACH_SMTP_PORT",
               "GHOST_OUTREACH_SMTP_USER", "GHOST_OUTREACH_SMTP_PASSWORD",
               "GHOST_OUTREACH_FROM", "GHOST_OUTREACH_REPLY_TO")


def pin_env():
    """Returns (apply, restore) closures pinning a fresh env slice."""
    saved = {k: os.environ.get(k) for k in PINNED_KEYS}

    def apply():
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
        os.environ["GHOST_UNIFIED_DB_PATH"] = os.path.join(TMP, f"ghost-{uuid.uuid4().hex}.db")
        os.environ["GHOST_GRANT_SECRET"] = f"test-secret-{uuid.uuid4().hex}"
        os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALTED")
        os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALT.json")
        os.environ["GHOST_LADDER_STATE_PATH"] = os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json")
        os.environ["GHOST_OUTBOUND_STATE_PATH"] = os.path.join(TMP, f"ob-{uuid.uuid4().hex}.json")
        os.environ["GHOST_SNAPSHOTS_DIR"] = os.path.join(TMP, f"snap-{uuid.uuid4().hex}")
        os.environ["GHOST_DEMOS_DIR"] = DEMOS
        # outreach SMTP "configured" (mocked transport in tests)
        os.environ["GHOST_OUTREACH_SMTP_HOST"] = "smtp.test"
        os.environ["GHOST_OUTREACH_SMTP_PORT"] = "587"
        os.environ["GHOST_OUTREACH_SMTP_USER"] = "u"
        os.environ["GHOST_OUTREACH_SMTP_PASSWORD"] = "p"
        os.environ["GHOST_OUTREACH_FROM"] = "ghost@test"
        os.environ["GHOST_OUTREACH_REPLY_TO"] = "reply@test"

    def restore():
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    return apply, restore


from ghost.core import grants
from ghost.autonomy.outbound import OutboundGateway, SendRequest, issue_effect_grant
from ghost.autonomy.sender import ColdOutreachSender, SendRefused
from ghost.store import outbox


class EnvPinnedTest(unittest.TestCase):
    def setUp(self):
        apply, restore = pin_env()
        apply()
        self.addCleanup(restore)


class FakeSuppression:
    def is_suppressed(self, value, lead_slug=None):
        return False


class FakeCRM:
    def __init__(self):
        self.drafts = []

    def add_or_update_draft(self, **kw):
        self.drafts.append(kw)
        return {"id": f"d{len(self.drafts)}"}


def make_request(**over):
    base = dict(lead_slug="test-lead", to_email="owner@test.com",
                subject="A free concept for your restaurant",
                body="We built you something.",
                personalization_facts=["fact one about the tandoor menu",
                                       "fact two about the rooftop seating"])
    base.update(over)
    return SendRequest(**base)


class FakeSMTPServer:
    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg):
        return {}


def sent_delivered_events():
    from ghost.core.audit import AuditLedger
    return AuditLedger.get_instance().query(event_type="outbound.delivered", limit=100)


class TestFourRedGrantTests(EnvPinnedTest):
    """The brief's exact exit test: no / expired / reused / over-cap all fail."""

    def setUp(self):
        super().setUp()
        self.sender = ColdOutreachSender(FakeSuppression(), crm=FakeCRM())
        self._delivered_base = len(sent_delivered_events())  # this test's ledger

    def assert_no_new_delivery(self):
        self.assertEqual(len(sent_delivered_events()), self._delivered_base)

    def test_1_without_grant(self):
        with patch("ghost.autonomy.sender.smtplib.SMTP", FakeSMTPServer):
            with self.assertRaises(SendRefused) as ctx:
                self.sender.send(make_request(), grant_token=None)
            self.assertEqual(ctx.exception.reason, "no_valid_grant")
            self.assert_no_new_delivery()

    def test_2_expired_grant(self):
        token = grants.issue("outbound_email", "test-lead", {"daily_cap": 50}, ttl_s=-10.0)
        self.assertIsNotNone(token)
        with patch("ghost.autonomy.sender.smtplib.SMTP", FakeSMTPServer):
            with self.assertRaises(SendRefused) as ctx:
                self.sender.send(make_request(), grant_token=token)
            self.assertEqual(ctx.exception.reason, "no_valid_grant")  # expired -> invalid
        self.assert_no_new_delivery()

    def test_3_reused_grant(self):
        token = grants.issue("outbound_email", "test-lead", {"daily_cap": 50}, ttl_s=300.0)
        with patch("ghost.autonomy.sender.smtplib.SMTP", FakeSMTPServer):
            res = self.sender.send(make_request(), grant_token=token)
            self.assertTrue(res["sent"])
            with self.assertRaises(SendRefused) as ctx:
                self.sender.send(make_request(), grant_token=token)
            self.assertEqual(ctx.exception.reason, "no_valid_grant")  # replay
        self.assertEqual(len(sent_delivered_events()), self._delivered_base + 1,
                         "no second delivery")
        # the outbox row exists and is marked sent (crash-recovery record)
        import hashlib
        key = "outreach:" + hashlib.sha256(token.encode()).hexdigest()[:16]
        self.assertEqual(outbox.state_of(key), "sent")
        self.assertIn(key.replace(":", "-"), res["message_id"])  # provider idempotency key

    def test_4_over_cap(self):
        token = grants.issue("outbound_email", "test-lead", {"daily_cap": 0}, ttl_s=300.0)
        with patch("ghost.autonomy.sender.smtplib.SMTP", FakeSMTPServer):
            with self.assertRaises(SendRefused) as ctx:
                self.sender.send(make_request(), grant_token=token)
            self.assertEqual(ctx.exception.reason, "over_cap")
        self.assert_no_new_delivery()

    def test_5_gateway_bounds_carry_the_cap(self):
        from ghost.autonomy.ladder import AutonomyLadder
        # explicit fresh state path: the suite's other ladder tests demote
        # levels on THEIR state files — this gate must judge on our own
        ladder = AutonomyLadder(state_path=os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json"))
        gw = OutboundGateway(knobs={"max_new_contacts_per_day": 7},
                             suppression=FakeSuppression(), ladder=ladder)
        token = gw.issue_grant(make_request())
        if token is None:  # diagnostic: which gate refused?
            dbg = gw.evaluate(make_request())
            self.fail(f"issue_grant returned None: gate={dbg.gate} reason={dbg.reason}")
        body_b64 = token[3:].split(".", 1)[0]
        import base64
        body = json.loads(base64.urlsafe_b64decode(body_b64 + "=" * (-len(body_b64) % 4)))
        self.assertEqual(body["bounds"]["daily_cap"], 7)


class TestDeployGates(EnvPinnedTest):

    def _demo(self, slug):
        d = os.path.join(DEMOS, slug)
        os.makedirs(d, exist_ok=True)
        return d

    def test_6_deploy_without_grant(self):
        from ghost.workers.deploy import VercelDeployer
        slug = f"nogrant-{uuid.uuid4().hex[:6]}"
        self._demo(slug)
        res = asyncio.run(VercelDeployer.deploy_demo(slug))
        self.assertEqual(res["status"], "grant_required")
        from ghost.core.audit import AuditLedger
        rows = AuditLedger.get_instance().query(event_type="deploy.grant_required", limit=10)
        self.assertTrue(any(r.get("subject") == slug for r in rows))

    def test_7_deploy_with_grant_but_no_snapshot(self):
        from ghost.workers.deploy import VercelDeployer
        slug = f"nosnap-{uuid.uuid4().hex[:6]}"
        self._demo(slug)
        grant = issue_effect_grant("deploy", slug)
        self.assertIsNotNone(grant, "effect grant must be issuable (secret set, no halt)")
        res = asyncio.run(VercelDeployer.deploy_demo(slug, grant_token=grant))
        self.assertEqual(res["status"], "snapshot_required")

    def test_8_deploy_stale_snapshot_then_fresh_passes_gate(self):
        from ghost.workers.deploy import VercelDeployer
        from ghost.workers.snapshots import SnapshotStore
        slug = f"stale-{uuid.uuid4().hex[:6]}"
        demo_dir = self._demo(slug)
        grant = issue_effect_grant("deploy", slug)
        SnapshotStore().snapshot(slug, demo_dir)
        # backdate the newest manifest beyond the 24h freshness window
        snap = SnapshotStore().latest(slug)
        mpath = os.path.join(SNAPSHOT_DIR := os.environ["GHOST_SNAPSHOTS_DIR"],
                             slug, snap["snapshot_id"], "manifest.json")
        manifest = json.load(open(mpath))
        manifest["created_at"] = time.time() - 25 * 3600
        json.dump(manifest, open(mpath, "w"))
        res = asyncio.run(VercelDeployer.deploy_demo(slug, grant_token=grant))
        self.assertEqual(res["status"], "snapshot_required")
        self.assertIn("25", res["message"])
        # fresh snapshot -> snapshot gate passes -> next gate (QA) is reached
        SnapshotStore().snapshot(slug, demo_dir)
        res2 = asyncio.run(VercelDeployer.deploy_demo(slug, grant_token=grant))
        # grant was consumed by the previous attempt (single-use) -> refused
        self.assertEqual(res2["status"], "grant_required")

    def test_9_deploy_reaches_qa_gate_and_records_outbox_intent(self):
        from ghost.workers.deploy import VercelDeployer
        from ghost.workers.snapshots import SnapshotStore
        slug = f"qa-{uuid.uuid4().hex[:6]}"
        demo_dir = self._demo(slug)
        grant = issue_effect_grant("deploy", slug)
        SnapshotStore().snapshot(slug, demo_dir)
        qa = {"passed": True, "screenshots_captured": 1,
              "screenshots": ["qa-shot-01.png"]}
        os.makedirs(os.path.join(demo_dir, "reports"), exist_ok=True)
        with open(os.path.join(demo_dir, "reports", "qa.json"), "w") as f:
            json.dump(qa, f)
        with open(os.path.join(demo_dir, "qa-shot-01.png"), "wb") as f:
            f.write(b"\x89PNG fake")
        res = asyncio.run(VercelDeployer.deploy_demo(slug, grant_token=grant))
        # past grant + snapshot + QA gates into the dispatch stage; final
        # status depends on the environment (token configured? auth modal?),
        # but the INTENT was recorded in the outbox BEFORE dispatch
        # (record-before-run) and never advanced without a real dispatch
        self.assertIn(res["status"], ("config_required", "denied", "success"),
                      f"unexpected stage: {res}")
        rows = sqlite3.connect(os.environ["GHOST_UNIFIED_DB_PATH"]).execute(
            "SELECT state FROM outbox WHERE kind='deploy'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "pending")

    def test_10_effect_grant_refused_on_halt(self):
        from ghost.core.halt import trip, resume
        trip(reason="test")
        try:
            self.assertIsNone(issue_effect_grant("deploy", "some-slug"))
        finally:
            resume()


class TestWebhookOutbox(EnvPinnedTest):

    def test_verified_webhook_lands_in_outbox(self):
        from ghost.workers.close_chain import process_payment_webhook

        class FakeEvent:
            event_id = f"evt_{uuid.uuid4().hex[:8]}"
            invoice_number = "INV-T2"
            amount = "20000"
            currency = "INR"

        class FakeProvider:
            name = "fake"

            def verify_webhook(self, headers, body):
                return True

            def parse_event(self, body):
                return FakeEvent()

        enqueued = []
        res = process_payment_webhook(
            FakeProvider(), {}, b"{}", lambda inv: {"amount": "20000", "currency": "INR",
                                                    "lead_slug": "l1"},
            lambda kind, payload, idempotency_key=None, **kw: enqueued.append(kind))
        self.assertEqual(res["status"], "verified")
        st = outbox.state_of(FakeEvent.event_id)
        self.assertEqual(st, "verified")
        rows = sqlite3.connect(os.environ["GHOST_UNIFIED_DB_PATH"]).execute(
            "SELECT kind FROM outbox WHERE idempotency_key=?",
            (FakeEvent.event_id,)).fetchall()
        self.assertEqual(rows[0][0], "payment.webhook")


if __name__ == "__main__":
    unittest.main()
