"""T3 chaos tests — halt is a REVOCATION, not a flag (ROADMAP_TOTAL_AUTONOMY).

Exit tests:
- a grant minted BEFORE a halt cannot be used AFTER (chaos test, also run by
  `ghost chaos` via the test_chaos* pattern)
- trip halt during a running outbound batch -> in-flight effects stop in
  <5s (measured); no send starts after the trip
- resume re-enables with a NEW epoch (fresh authority regime)
- the deploy effect's last-instant halt re-check stops dispatch
"""
import os
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

TMP = tempfile.mkdtemp(prefix="ghost-t3-")

PINNED = ("GHOST_AUDIT_DB_PATH", "GHOST_UNIFIED_DB_PATH", "GHOST_GRANT_SECRET",
          "GHOST_REVOCATION_EPOCH", "GHOST_OUTBOUND_STATE_PATH",
          "GHOST_OUTREACH_SMTP_HOST", "GHOST_OUTREACH_SMTP_PORT",
          "GHOST_OUTREACH_SMTP_USER", "GHOST_OUTREACH_SMTP_PASSWORD",
          "GHOST_OUTREACH_FROM", "GHOST_OUTREACH_REPLY_TO",
          "GHOST_HALT_FILE", "GHOST_HALT_META")


class EnvPinned(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in PINNED}

        def apply():
            os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
            os.environ["GHOST_UNIFIED_DB_PATH"] = os.path.join(TMP, f"ghost-{uuid.uuid4().hex}.db")
            os.environ["GHOST_GRANT_SECRET"] = f"t3-secret-{uuid.uuid4().hex}"
            os.environ["GHOST_REVOCATION_EPOCH"] = os.path.join(TMP, f"epoch-{uuid.uuid4().hex}")
            os.environ["GHOST_OUTBOUND_STATE_PATH"] = os.path.join(TMP, f"ob-{uuid.uuid4().hex}.json")
            os.environ["GHOST_OUTREACH_SMTP_HOST"] = "smtp.test"
            os.environ["GHOST_OUTREACH_SMTP_PORT"] = "587"
            os.environ["GHOST_OUTREACH_SMTP_USER"] = "u"
            os.environ["GHOST_OUTREACH_SMTP_PASSWORD"] = "p"
            os.environ["GHOST_OUTREACH_FROM"] = "ghost@test"
            os.environ["GHOST_OUTREACH_REPLY_TO"] = "reply@test"

        apply()
        # NOTE: halt file paths are module constants bound at first import and
        # shared suite-wide — chaos tests here trip the REAL sentinel, so every
        # test carries a safety net: never leave the suite halted.
        def _ensure_resumed():
            try:
                from ghost.core.halt import is_halted, resume
                if is_halted():
                    resume(by="test-cleanup")
            except Exception:
                pass
        self.addCleanup(_ensure_resumed)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestRevocationEpoch(unittest.TestCase):

    def test_epoch_semantics(self):
        from ghost.core import revocation
        os.environ["GHOST_REVOCATION_EPOCH"] = os.path.join(TMP, f"ep-{uuid.uuid4().hex}")
        self.assertEqual(revocation.current_epoch(), 0)  # missing file = 0
        self.assertEqual(revocation.bump_epoch(), 1)
        self.assertEqual(revocation.bump_epoch(), 2)
        self.assertEqual(revocation.current_epoch(), 2)  # monotonic, atomic

    def test_trip_and_resume_bump(self):
        from ghost.core import revocation
        from ghost.core.halt import trip, resume
        os.environ["GHOST_REVOCATION_EPOCH"] = os.path.join(TMP, f"ep-{uuid.uuid4().hex}")
        e0 = revocation.current_epoch()
        trip(reason="test")
        e1 = revocation.current_epoch()
        resume()
        e2 = revocation.current_epoch()
        self.assertGreater(e1, e0)
        self.assertGreater(e2, e1, "resume opens a fresh authority regime")


class TestGrantRevocation(EnvPinned):

    def test_grant_minted_before_halt_dead_after(self):
        """THE chaos exit: pre-halt authority cannot survive the trip."""
        from ghost.core import grants
        from ghost.core.halt import trip, resume
        token = grants.issue("outbound_email", "lead-x", {"daily_cap": 5}, ttl_s=600.0)
        self.assertIsNotNone(token)
        # pre-halt: valid (consume-free check via verify — consumes, but fine)
        self.assertIsNotNone(grants.verify(token, capability="outbound_email",
                                            subject="lead-x"))
        token2 = grants.issue("outbound_email", "lead-y", {"daily_cap": 5}, ttl_s=600.0)
        trip(reason="chaos")
        try:
            # during halt: refused
            self.assertIsNone(grants.verify(token2, capability="outbound_email",
                                            subject="lead-y"))
        finally:
            resume()
        # after resume: STILL refused — the epoch moved on, the token didn't
        self.assertIsNone(grants.verify(token2, capability="outbound_email",
                                        subject="lead-y"))
        # fresh authority works in the new regime
        token3 = grants.issue("outbound_email", "lead-z", {"daily_cap": 5}, ttl_s=600.0)
        self.assertIsNotNone(grants.verify(token3, capability="outbound_email",
                                           subject="lead-z"))


class TestBatchStopsUnder5s(EnvPinned):
    """Exit test: trip mid-batch, measure time-to-stop, count starts."""

    def test_batch_halts_in_under_5_seconds(self):
        from ghost.autonomy.sender import ColdOutreachSender, SendRefused
        from ghost.core import grants
        from ghost.core.halt import trip, resume

        state = {"starts": 0, "trip_at": None}

        class RecordingSMTP:
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
                state["starts"] += 1
                if state["starts"] == 3:
                    state["trip_at"] = time.time()
                    trip(reason="chaos-mid-batch")   # the throat-cut, mid-flight
                time.sleep(0.25)                     # in-flight send takes time
                return {}

        sender = ColdOutreachSender(FakeSup(), crm=None)

        def make_req(i):
            from ghost.autonomy.outbound import SendRequest
            return SendRequest(lead_slug=f"batch-lead-{i}", to_email=f"o{i}@t.c",
                               subject="s", body="b",
                               personalization_facts=["f1 about the place", "f2 about menu"])

        # ten grants minted BEFORE the batch (pre-halt authority)
        tokens = [grants.issue("outbound_email", f"batch-lead-{i}", {"daily_cap": 50},
                               ttl_s=600.0) for i in range(10)]

        with patch("ghost.autonomy.sender.smtplib.SMTP", RecordingSMTP):
            for i, tok in enumerate(tokens):
                try:
                    sender.send(make_req(i), grant_token=tok)
                except SendRefused:
                    break  # batch stops at the first refusal

        elapsed = time.time() - state["trip_at"]
        # starts==3 IS the invariant: the trip happens inside send #3's
        # provider call, and no send #4.. ever began.
        self.assertEqual(state["starts"], 3, "no send may START after the trip")
        self.assertLess(elapsed, 5.0, f"batch took {elapsed:.2f}s to stop (<5s required)")


class FakeSup:
    def is_suppressed(self, value, lead_slug=None):
        return False


class TestDeployPreDispatchCheck(EnvPinned):

    def test_deploy_stops_between_authorization_and_dispatch(self):
        from ghost.workers.deploy import VercelDeployer
        from ghost.workers.snapshots import SnapshotStore
        from ghost.core.halt import resume
        slug = f"t3dep-{uuid.uuid4().hex[:6]}"
        demo_dir = os.path.join(os.environ.get("GHOST_DEMOS_DIR")
                                or os.path.join(TMP, "demos"), slug)
        os.makedirs(demo_dir, exist_ok=True)
        os.environ["GHOST_DEMOS_DIR"] = os.path.dirname(demo_dir)
        grant = None
        from ghost.autonomy.outbound import issue_effect_grant

        def auth_then_trip(prompt):
            trip_loudly()
            return True

        def trip_loudly():
            from ghost.core.halt import trip
            trip(reason="t3-deploy-test")

        grant = issue_effect_grant("deploy", slug)
        SnapshotStore().snapshot(slug, demo_dir)
        qa = {"passed": True, "screenshots_captured": 1, "screenshots": ["s.png"]}
        os.makedirs(os.path.join(demo_dir, "reports"), exist_ok=True)
        json.dump(qa, open(os.path.join(demo_dir, "reports", "qa.json"), "w"))
        open(os.path.join(demo_dir, "s.png"), "wb").write(b"\x89PNG")

        with patch("ghost.workers.deploy.request_tactical_authorization",
                   side_effect=auth_then_trip):
            res = asyncio.run(VercelDeployer.deploy_demo(slug, grant_token=grant))
        self.assertEqual(res["status"], "halted", res)
        # the recorded intent must be failed, not left pending or 'sent'
        import hashlib
        key = f"deploy:{slug}:{VercelDeployer._qa_hash(demo_dir)[:12]}"
        from ghost.store import outbox
        self.assertEqual(outbox.state_of(key), "failed")
        resume()  # clean the shared sentinel for the rest of the suite


import asyncio  # noqa: E402
import json  # noqa: E402

if __name__ == "__main__":
    unittest.main()
