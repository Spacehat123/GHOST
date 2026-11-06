"""Wiring-6 tests: model routing, per-intent budgets, agy sandbox, DNS deliverability check."""
import json
import os
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-w6-test-")
os.environ["GHOST_GRANT_SOCKET"] = os.path.join(TMP, "no-grantd.sock")  # suite must NEVER reach a live grantd
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_MODEL_ROUTING_PATH"] = os.path.join(TMP, f"routing-{uuid.uuid4().hex}.json")

from ghost.core import model_routing as mr
from ghost.core.sandbox import (
    assert_path_allowed, build_safe_env, path_allowed, scrub_command,
)
from ghost.core.audit import AuditLedger


class TestModelRouting(unittest.TestCase):

    def test_route_copywriting_to_big_model(self):
        r = mr.route("copywriting")
        self.assertEqual(r["model"], "gemini-1.5-pro")
        self.assertEqual(r["backend"], "cloud")

    def test_local_down_falls_back_down_not_up(self):
        # Ollama unreachable -> classify degrades to the SMALL cloud model,
        # never to the big one (cheap-first holds in degraded mode).
        mr._probe_cache["ts"] = 0.0
        mr._probe_cache["result"] = {"status": "not_configured", "models": []}
        r = mr.route("classify")
        self.assertEqual(r["model"], "gemini-2.0-flash")
        self.assertTrue(r.get("degraded"))

    def test_local_available_routes_local(self):
        import time as _t
        mr._probe_cache["ts"] = _t.time()  # fresh cache entry
        mr._probe_cache["result"] = {"status": "available",
                                     "models": ["qwen2.5:7b", "llama3"]}
        r = mr.route("classify")
        self.assertEqual(r["model"], "local:qwen2.5:7b")
        self.assertEqual(r["backend"], "ollama")

    def test_probe_unreachable_is_honest(self):
        r = mr.check_local_model(base_url="http://127.0.0.1:1", timeout=0.3)
        self.assertEqual(r["status"], "not_configured")

    def test_per_intent_budgets_enforced_from_ledger(self):
        ledger = AuditLedger(os.path.join(TMP, f"bud-{uuid.uuid4().hex}.db"))
        self.assertGreater(mr.budget_remaining("codegen", ledger=ledger), 0)
        mr.record_spend("codegen", 300000, ledger=ledger)   # 100k left
        self.assertEqual(mr.budget_remaining("codegen", ledger=ledger), 100000)
        mr.record_spend("codegen", 150000, ledger=ledger)   # overdrawn
        self.assertEqual(mr.budget_remaining("codegen", ledger=ledger), 0)

    def test_unbudgeted_intent_is_unlimited(self):
        self.assertIsNone(mr.token_budget_for("nonexistent_intent"))
        self.assertIsNone(mr.budget_remaining("nonexistent_intent",
                                              ledger=AuditLedger(os.path.join(TMP, "x.db"))))


class TestSandbox(unittest.TestCase):

    def test_credentials_never_enter_subprocess_env(self):
        os.environ["OPENAI_API_KEY_TEST_VALUE"] = "sk-abc123"
        os.environ["SOME_TOKEN_XYZ"] = "tok"
        self.addCleanup(os.environ.pop, "OPENAI_API_KEY_TEST_VALUE", None)
        self.addCleanup(os.environ.pop, "SOME_TOKEN_XYZ", None)
        env = build_safe_env()
        joined = json.dumps(env)
        self.assertNotIn("sk-abc123", joined)
        self.assertNotIn("SOME_TOKEN_XYZ", joined)

    def test_extra_credential_shaped_vars_refused(self):
        env = build_safe_env({"MY_API_KEY": "sk-xyz"})
        self.assertNotIn("MY_API_KEY", env)

    def test_path_allowlist(self):
        self.assertTrue(path_allowed(os.path.join(TMP, "sub", "file.txt")))  # /tmp allowlisted
        self.assertFalse(path_allowed("/etc/passwd"))
        self.assertFalse(path_allowed(os.path.expanduser("~/.ssh/id_rsa")))
        with self.assertRaises(PermissionError):
            assert_path_allowed("/etc/shadow")

    def test_launch_scrubs_secrets_from_command(self):
        os.environ["GHOST_GRANT_SECRET"] = "super-secret-value-123"
        cmd = "agy -p 'use token super-secret-value-123 please'"
        self.assertNotIn("super-secret-value-123", scrub_command(cmd))
        del os.environ["GHOST_GRANT_SECRET"]


class TestDNSDeliverability(unittest.TestCase):

    def test_spf_dmarc_check_shape(self):
        from ghost.core.dnscheck import check_domain_deliverability
        # Real DNS lookup — example.com has no SPF; the result must be an
        # honest structured report either way (no exceptions).
        report = check_domain_deliverability("example.com")
        for key in ("domain", "spf", "dmarc", "dkim", "ready", "missing"):
            self.assertIn(key, report)
        self.assertIsInstance(report["ready"], bool)
        self.assertIsInstance(report["missing"], list)

    def test_local_domain_check(self):
        from ghost.core.dnscheck import check_domain_deliverability
        r = check_domain_deliverability("gmail.com")  # gmail has SPF+DMARC
        if r["spf"].get("found"):
            self.assertTrue(r["spf"]["record"].lower().startswith("v=spf1"))


if __name__ == "__main__":
    unittest.main()
