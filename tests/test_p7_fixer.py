"""P7 tests (Operator's 11): the customer-report fixer.

- THE DRILL: reported break → sidecar (injected worker) → independent
  verification → STAGED for the operator (live site UNTOUCHED) → approval
  applies it. An LLM patch is never 'mechanical' — it never auto-applies.
- Honesty: verifier failure discards; out-of-scope edits discard; no model
  escalates instead of pretending; halt refuses everything; the standalone
  worker script imports STDLIB ONLY (ghost is unimportable where it runs).
"""
import ast
import json
import os
import tempfile
import time
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-p7-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_SIDECAR_WORKROOT"] = os.path.join(TMP, "sidecars")
os.environ["GHOST_FIXER_STAGED"] = os.path.join(TMP, "fixer_staged")

from ghost.control.fixers import (FixIssue, approve_staged, diagnose_and_fix,
                                   list_staged, stage_patch)
from ghost.control.sidecars import SidecarManager


class EnvPinned(unittest.TestCase):

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_AUDIT_DB_PATH", "GHOST_SIDECAR_WORKROOT",
                        "GHOST_FIXER_STAGED", "GHOST_HALT_FILE", "GHOST_HALT_META",
                        "GHOST_QUEUE_DB_PATH")}
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
        os.environ["GHOST_SIDECAR_WORKROOT"] = os.path.join(TMP, f"side-{uuid.uuid4().hex}")
        os.environ["GHOST_FIXER_STAGED"] = os.path.join(TMP, f"staged-{uuid.uuid4().hex}")
        os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, f"H-{uuid.uuid4().hex}")
        os.environ["GHOST_HALT_META"] = os.environ["GHOST_HALT_FILE"] + ".json"
        os.environ["GHOST_QUEUE_DB_PATH"] = os.path.join(TMP, f"q-{uuid.uuid4().hex}.db")
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _ledger(self, event_type):
        from ghost.core.audit import AuditLedger
        return AuditLedger.get_instance().query(event_type=event_type, limit=50)

    def _site_file(self, broken=True):
        d = tempfile.mkdtemp(prefix="p7-site-", dir=TMP)
        path = os.path.join(d, "index.html")
        content = ('<a href="htp://broken.example"></a>' if broken
                   else '<a href="https://fixed.example"></a>')
        with open(path, "w") as f:
            f.write(content)
        return path

    def _issue(self, path, key=None):
        return FixIssue(
            key=key or f"issue-{uuid.uuid4().hex[:6]}",
            site_slug="acme-site",
            description="customer reports the contact link is broken",
            scope_files=[path],
            check_command='grep -q "fixed.example" "{file}"',
            source="customer_report")


GOOD_WORKER = lambda spec, ctx, cancelled: {
    "patch": {"index.html": '<a href="https://fixed.example"></a>\n'},
    "evidence": "repaired the contact link href", "tool_calls": 2, "tokens": 1}

BAD_WORKER = lambda spec, ctx, cancelled: {
    "patch": {"index.html": '<a href="still-broken.example"></a>\n'},
    "evidence": "tried something", "tool_calls": 2, "tokens": 1}

GREEDY_WORKER = lambda spec, ctx, cancelled: {
    "patch": {"index.html": '<a href="https://fixed.example"></a>\n',
              "secrets.txt": "pwned"},
    "evidence": "fixed + extra", "tool_calls": 3, "tokens": 1}


class TestTheDrill(EnvPinned):

    def test_report_to_verified_staged_live_site_untouched(self):
        path = self._site_file(broken=True)
        original = open(path).read()
        out = diagnose_and_fix(self._issue(path), worker=GOOD_WORKER)
        self.assertEqual(out["status"], "staged", out)
        # the live site is UNTOUCHED by the fixer itself
        self.assertEqual(open(path).read(), original,
                         "an LLM patch stages — it never auto-applies")
        # the staged package holds the patched copy + evidence
        staged_dir = out["dir"]
        doc = json.load(open(os.path.join(staged_dir, "EVIDENCE.json")))
        self.assertEqual(doc["status"], "staged")
        self.assertEqual(doc["issue"]["scope_files"], [path])
        self.assertTrue(os.path.exists(os.path.join(staged_dir, "files", "index.html")))
        self.assertIn("exit=0", doc["evidence"],
                      "the evidence carries the independent verifier's own output")

    def test_operator_approval_applies_and_audits(self):
        path = self._site_file(broken=True)
        out = diagnose_and_fix(self._issue(path), worker=GOOD_WORKER)
        res = approve_staged(out["staged_id"], by="operator-test")
        self.assertTrue(res["applied"], res)
        self.assertIn("https://fixed.example", open(path).read())
        self.assertEqual(len(self._ledger("fixer.applied")), 1)
        # one action per package
        again = approve_staged(out["staged_id"])
        self.assertFalse(again["applied"])
        self.assertIn("already applied", again["reason"])

    def test_approval_refused_while_halted(self):
        from ghost.core.halt import trip, resume
        path = self._site_file(broken=True)
        out = diagnose_and_fix(self._issue(path), worker=GOOD_WORKER)
        trip(reason="test")
        try:
            res = approve_staged(out["staged_id"])
            self.assertFalse(res["applied"])
            self.assertIn("halt", res["reason"])
        finally:
            resume(by="test-cleanup")
        self.assertIn("broken.example", open(path).read(),
                      "nothing applies while frozen")

    def test_verifier_failure_discards_nothing_staged(self):
        path = self._site_file(broken=True)
        out = diagnose_and_fix(self._issue(path), worker=BAD_WORKER)
        self.assertEqual(out["status"], "unresolved")
        self.assertEqual(out["reason"], "verifier_failed")
        self.assertEqual(list_staged(), [], "a failed patch never reaches the queue")

    def test_out_of_scope_edit_is_discarded(self):
        path = self._site_file(broken=True)
        out = diagnose_and_fix(self._issue(path), worker=GREEDY_WORKER)
        self.assertEqual(out["status"], "unresolved")
        self.assertEqual(out["sidecar_status"], "discarded")
        self.assertIn("secrets.txt", " ".join(out["notes"]))

    def test_no_model_escalates_without_pretending(self):
        path = self._site_file(broken=True)
        import ghost.control.fixers as fx
        saved = fx.model_available
        fx.model_available = lambda: False
        try:
            out = diagnose_and_fix(self._issue(path), worker=None)
        finally:
            fx.model_available = saved
        self.assertEqual(out["status"], "escalated")
        self.assertEqual(out["reason"], "no_model_available")
        self.assertEqual(len(self._ledger("fixer.escalated")), 1)

    def test_halt_refuses_the_whole_pipeline(self):
        from ghost.core.halt import trip, resume
        path = self._site_file(broken=True)
        trip(reason="test")
        try:
            out = diagnose_and_fix(self._issue(path), worker=GOOD_WORKER)
            self.assertEqual(out["status"], "escalated")
            self.assertIn("halt", out["reason"])
        finally:
            resume(by="test-cleanup")


class TestStandaloneWorkerPurity(unittest.TestCase):

    def test_worker_script_imports_stdlib_only(self):
        """The sidecar env strips PYTHONPATH — the real worker must therefore
        import nothing but the stdlib (ghost unimportable, by construction)."""
        src_path = os.path.join(os.path.dirname(__file__), "..", "src",
                                "ghost", "control", "fixer_worker.py")
        tree = ast.parse(open(src_path).read())
        banned = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] not in ("json", "os", "sys", "time",
                                                    "urllib"):
                        banned.append(a.name)
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root not in ("json", "os", "sys", "time", "urllib"):
                    banned.append(node.module)
        self.assertEqual(banned, [], f"the fixer worker must stay stdlib-only: {banned}")


class TestWiring(EnvPinned):

    def test_runtime_handler_registered(self):
        from ghost.autonomy.runtime import AutonomyRuntime
        self.assertIn("fixer.customer_report", AutonomyRuntime()._build_handlers())

    def test_signal_endpoint_enqueues_fixer_job(self):
        from starlette.testclient import TestClient
        from ghost.server.app import app
        from ghost.server.agency_api import get_or_create_session_token
        client = TestClient(app)
        token = get_or_create_session_token()
        res = client.post("/api/watchdog/signal",
                          headers={"X-Session-Token": token},
                          json={"event_type": "customer_report", "source": "whatsapp",
                                "summary": "site down",
                                "payload": {"site_slug": "acme",
                                            "description": "contact page 500s"}})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["intake_poked"])
        from ghost.core.jobqueue import JobQueue
        self.assertGreaterEqual(JobQueue().stats()["pending"], 1)


if __name__ == "__main__":
    unittest.main()
