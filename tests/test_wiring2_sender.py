"""Wiring-2 tests: secrets vault, grant tokens, cold outreach sender locks."""
import os
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-w2-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALT")
os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALT.json")
os.environ["GHOST_SECRETS_PATH"] = os.path.join(TMP, f"secrets-{uuid.uuid4().hex}.json")

from ghost.core import grants, secrets
from ghost.core.audit import AuditLedger
from ghost.core.halt import trip, resume
from ghost.autonomy.outbound import OutboundGateway, SendRequest
from ghost.autonomy.sender import ColdOutreachSender, SendRefused, smtp_configured
from ghost.core.secrets import get as sec_get, redact, require


class FakeSuppression:
    def __init__(self, suppressed=()):
        self.suppressed = set(suppressed)
    def is_suppressed(self, email, lead_slug=None):
        return email in self.suppressed or lead_slug in self.suppressed


def make_gateway():
    from ghost.autonomy.ladder import AutonomyLadder
    gw = OutboundGateway(
        knobs={"max_new_contacts_per_day": 50},
        suppression=FakeSuppression(),
        ladder=AutonomyLadder(os.path.join(TMP, f"l-{uuid.uuid4().hex}.json")),
        state_path=os.path.join(TMP, f"o-{uuid.uuid4().hex}.json"))
    gw._state["campaign_started_at"] = __import__("time").time() - 30 * 86400
    gw._state["day"] = gw._ist_day()
    return gw


def make_request(**kw):
    base = dict(lead_slug="lead-1", to_email="o@x.com", subject="Hi", body="Demo inside",
                personalization_facts=["no website", "₹4,500 menu"])
    base.update(kw)
    return SendRequest(**base)


class TestSecrets(unittest.TestCase):

    def test_env_wins_over_vault(self):
        with open(os.environ["GHOST_SECRETS_PATH"], "w") as f:
            import json as _json
            _json.dump({"TEST_SECRET_X": "from-vault"}, f)
        self.assertEqual(sec_get("TEST_SECRET_X"), "from-vault")
        os.environ["TEST_SECRET_X"] = "from-env"
        self.assertEqual(sec_get("TEST_SECRET_X"), "from-env")
        del os.environ["TEST_SECRET_X"]

    def test_require_raises_with_instructions(self):
        with self.assertRaises(RuntimeError) as ctx:
            require("DEFINITELY_MISSING_SECRET_42")
        self.assertIn("fail", str(ctx.exception).lower())

    def test_redact_scrubs_vault_values_and_shapes(self):
        with open(os.environ["GHOST_SECRETS_PATH"], "w") as f:
            import json as _json
            _json.dump({"TEST_SECRET_X": "from-vault-super-secret-value"}, f)
        secrets._SECRET_CACHE = None  # force reload
        text = "key is sk-abcdefghijklmnop123456 and from-vault-super-secret-value inside"
        out = redact(text)
        self.assertNotIn("sk-abcdefghijklmnop123456", out)
        self.assertNotIn("from-vault-super-secret-value", out)
        self.assertIn("[REDACTED", out)


class TestGrantTokens(unittest.TestCase):

    def setUp(self):
        os.environ["GHOST_GRANT_SECRET"] = "test-secret-" + uuid.uuid4().hex

    def tearDown(self):
        os.environ.pop("GHOST_GRANT_SECRET", None)

    def test_roundtrip_issue_verify_consume(self):
        tok = grants.issue("outbound_email", "lead-1", {"to": "o@x.com"})
        self.assertTrue(tok.startswith("gt."))
        bounds = grants.verify(tok, capability="outbound_email", subject="lead-1")
        self.assertEqual(bounds["to"], "o@x.com")
        # replay refused
        self.assertIsNone(grants.verify(tok, capability="outbound_email", subject="lead-1"))

    def test_wrong_capability_refused(self):
        tok = grants.issue("outbound_email", "lead-1", {})
        self.assertIsNone(grants.verify(tok, capability="payments_capture"))

    def test_tampered_token_refused(self):
        tok = grants.issue("outbound_email", "lead-1", {})
        bad = tok[:-4] + ("0000" if not tok.endswith("0000") else "1111")
        self.assertIsNone(grants.verify(bad, capability="outbound_email"))

    def test_no_secret_issues_nothing(self):
        os.environ.pop("GHOST_GRANT_SECRET", None)
        self.assertIsNone(grants.issue("outbound_email", "lead-1", {}))


class TestSenderLocks(unittest.TestCase):

    def setUp(self):
        os.environ["GHOST_GRANT_SECRET"] = "test-secret-" + uuid.uuid4().hex
        self.addCleanup(lambda: os.environ.pop("GHOST_GRANT_SECRET", None))
        self.gw = make_gateway()
        self.sender = ColdOutreachSender(suppression=FakeSuppression())
        for key in ("GHOST_OUTREACH_SMTP_HOST", "GHOST_OUTREACH_SMTP_USER",
                    "GHOST_OUTREACH_SMTP_PASSWORD", "GHOST_OUTREACH_FROM"):
            os.environ.pop(key, None)

    def test_without_grant_refused_before_anything(self):
        with self.assertRaises(SendRefused) as ctx:
            self.sender.send(make_request(), grant_token=None)
        self.assertEqual(ctx.exception.reason, "no_valid_grant")

    def test_halt_refused_even_with_fresh_grant(self):
        token = self.gw.issue_grant(make_request())
        self.assertTrue(token)
        trip(reason="operator")
        try:
            with self.assertRaises(SendRefused) as ctx:
                self.sender.send(make_request(), grant_token=token)
            self.assertEqual(ctx.exception.reason, "halt")
        finally:
            resume()

    def test_suppressed_refused_even_with_fresh_grant(self):
        sender = ColdOutreachSender(suppression=FakeSuppression({"o@x.com"}))
        token = self.gw.issue_grant(make_request())
        with self.assertRaises(SendRefused) as ctx:
            sender.send(make_request(), grant_token=token)
        self.assertEqual(ctx.exception.reason, "suppressed")

    def test_unconfigured_smtp_becomes_crm_draft_not_send(self):
        token = self.gw.issue_grant(make_request())
        self.assertFalse(smtp_configured())
        r = self.sender.send(make_request(), grant_token=token)
        self.assertFalse(r["sent"])
        self.assertTrue(r["drafted"])
        self.assertEqual(r["reason"], "smtp_not_configured")

    def test_gateway_grant_couples_gates_to_authority(self):
        # A request the gates refuse cannot get a grant at all.
        req = make_request(personalization_facts=["only one fact"])
        self.assertIsNone(self.gw.issue_grant(req))


if __name__ == "__main__":
    unittest.main()
