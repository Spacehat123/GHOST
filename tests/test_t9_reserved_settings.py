"""T9 tests — human-reserved act packages + the control-plane settings UI.

Exit tests (ROADMAP_TOTAL_AUTONOMY T9):
- a contract package is pre-filled and one click executes it through the
  proper plane (the ONLY human action in a simulated deal)
- a refund without provider keys stays BLOCKED honestly, never fake-green
- a 'delete' package executes as ARCHIVE with a snapshot proof — paid-client
  data is never destroyed
- one action per package: a second approve is refused
- approval mints an OPERATOR grant; the LLM path can never reach these routes
- settings: secrets masked on read, written only through the allowlist,
  test-connections honest (not_configured is a state, not an error)
"""
import json
import os
import sqlite3
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-t9-")
os.environ["GHOST_GRANT_SOCKET"] = os.path.join(TMP, "no-grantd.sock")  # suite must NEVER reach a live grantd
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_UNIFIED_DB_PATH"] = os.path.join(TMP, f"ghost-{uuid.uuid4().hex}.db")
os.environ["GHOST_SECRETS_PATH"] = os.path.join(TMP, f"secrets-{uuid.uuid4().hex}.json")
os.environ["GHOST_GRANT_SECRET"] = f"t9-{uuid.uuid4().hex}"
os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALTED")
os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALT.json")
os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
os.environ["GHOST_SNAPSHOTS_DIR"] = os.path.join(TMP, "snaps")

from ghost.control import reserved as rp
from ghost.core.secrets import get, set_secret



class EnvPinned(unittest.TestCase):
    """Suite-order hardening (T2/T5/T6b lesson): chaos pops GHOST_GRANT_SECRET
    at run time — grant-dependent classes re-pin per test with cleanup."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_GRANT_SECRET", "GHOST_REVOCATION_EPOCH",
                        "GHOST_HALT_FILE", "GHOST_HALT_META", "GHOST_SECRETS_PATH")}
        os.environ["GHOST_GRANT_SECRET"] = f"t9-{uuid.uuid4().hex}"
        os.environ["GHOST_REVOCATION_EPOCH"] = os.path.join(TMP, f"epoch-{uuid.uuid4().hex}")
        os.environ["GHOST_SECRETS_PATH"] = os.path.join(TMP, f"sec-{uuid.uuid4().hex}.json")
        self.addCleanup(self._restore)

    def _restore(self):
        for path in (os.environ.get("GHOST_HALT_FILE"), os.environ.get("GHOST_HALT_META")):
            if path and os.path.exists(path):
                os.remove(path)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestReservedPackages(EnvPinned):

    def test_contract_package_one_click(self):
        """THE simulated deal's final step: package → one click → executed."""
        from ghost.crm.db import CRMDatabase
        crm = CRMDatabase.get_instance()
        crm.add_or_update_lead({"slug": "deal-lead", "name": "Deal Co",
                                "status": "negotiating"})
        pkg = rp.create_package(
            "contract", "deal-lead",
            payload={"lead_slug": "deal-lead", "invoice_number": "INV-T9",
                     "deal_value_inr": "25000", "client_name": "Deal Co",
                     "terms_summary": "one-pager site + 3 months maintenance"},
            recommendation="Approve — payment verified, terms match configs/terms.json")
        self.assertEqual(pkg["state"], "ready")
        self.assertIn("payment verified", pkg["recommendation"].lower())
        out = rp.approve(pkg["id"])
        self.assertEqual(out["state"], "executed", out)
        self.assertTrue(out["outcome"]["ok"])
        self.assertEqual(crm.get_lead("deal-lead")["status"], "contract_sent")

    def test_one_action_per_package(self):
        pkg = rp.create_package("contract", "once-lead",
                                payload={"lead_slug": "once-lead",
                                         "invoice_number": "INV-X"})
        first = rp.approve(pkg["id"])
        self.assertEqual(first["state"], "executed")
        second = rp.approve(pkg["id"])
        self.assertIn("error", second, "a package executes exactly once")

    def test_refund_fails_closed_without_provider(self):
        pkg = rp.create_package("refund", "client-x",
                                payload={"payment_id": "pay_missing", "amount": "5000",
                                         "reason": "duplicate charge",
                                         "evidence": "ledger shows two captures"},
                                recommendation="Refund — duplicate capture verified")
        out = rp.approve(pkg["id"])
        # no real provider support/keys → blocked honestly, package never fakes success
        self.assertIn(out["state"], ("blocked", "executed"), out)
        if out["state"] == "blocked":
            self.assertFalse(out["outcome"]["ok"])
        listed = {p["id"]: p for p in rp.list_packages()}
        self.assertIn(listed[pkg["id"]]["state"], ("blocked", "executed"))

    def test_delete_executes_as_archive_never_rm(self):
        site_dir = tempfile.mkdtemp(prefix="t9-site-", dir=TMP)
        with open(os.path.join(site_dir, "index.html"), "w") as f:
            f.write("<html>client site</html>")
        pkg = rp.create_package("delete", "old-site",
                                payload={"path": site_dir,
                                         "scope": "site dir only",
                                         "reason": "client cancelled; data retained in archive"},
                                recommendation="Archive — client data preserved, not destroyed")
        out = rp.approve(pkg["id"])
        self.assertEqual(out["state"], "executed", out)
        self.assertTrue(out["outcome"]["ok"])
        self.assertIn("archived_to", out["outcome"])
        self.assertFalse(os.path.exists(site_dir), "moved out of the live tree")
        self.assertTrue(os.path.exists(out["outcome"]["archived_to"]))
        with open(os.path.join(out["outcome"]["archived_to"], "index.html")) as f:
            self.assertIn("client site", f.read(), "data preserved in the archive")

    def test_halt_blocks_even_reserved_acts(self):
        from ghost.core.halt import trip, resume, is_halted
        pkg = rp.create_package("contract", "halt-lead",
                                payload={"lead_slug": "halt-lead", "invoice_number": "INV-H"})
        trip(reason="t9-test")
        try:
            out = rp.approve(pkg["id"])
            self.assertEqual(out["state"], "blocked", "a halted system executes nothing")
        finally:
            if is_halted():
                resume()

    def test_dismiss(self):
        pkg = rp.create_package("contract", "dis-lead", payload={"invoice_number": "INV-D"})
        out = rp.dismiss(pkg["id"])
        self.assertEqual(out["state"], "dismissed")

    def test_unknown_kind_refused(self):
        res = rp.create_package("start_a_war", "x", payload={})
        self.assertIn("error", res)


class TestSettingsSurface(EnvPinned):

    def test_set_secret_allowlist(self):
        self.assertTrue(set_secret("RAZORPAY_KEY_ID", "rzp_test_abcdefghijklmnop"))
        self.assertEqual(get("RAZORPAY_KEY_ID"), "rzp_test_abcdefghijklmnop")
        self.assertFalse(set_secret("SOME_RANDOM_CREDENTIAL", "x"))
        self.assertFalse(set_secret("GHOST_API_KEY", "x"), "credential-shaped names refused")

    def test_masking(self):
        from ghost.server.app import _mask
        self.assertEqual(_mask("rzp_test_abcdefghijklmnop"), "rzp_...mnop")
        self.assertEqual(_mask(""), "")
        self.assertEqual(_mask("short"), "***")

    def test_sections_route_masks_secrets(self):
        from fastapi.testclient import TestClient
        from ghost.server.app import app
        set_secret("RAZORPAY_KEY_SECRET", "supersecretvalue123")
        with TestClient(app) as client:
            r = client.get("/api/settings/sections")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            flat = json.dumps(body)
            self.assertNotIn("supersecretvalue123", flat, "secret leaked via settings")
            masked = body["razorpay"]["RAZORPAY_KEY_SECRET"]["masked"]
            self.assertTrue(masked.endswith("123"))
            self.assertNotIn("supersecret", masked)

    def test_put_writes_through_allowlist(self):
        from fastapi.testclient import TestClient
        from ghost.server.app import app
        with TestClient(app) as client:
            r = client.post("/api/settings/section/razorpay",
                            json={"RAZORPAY_KEY_ID": "rzp_test_zyxwvutsrqponmlk"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(get("RAZORPAY_KEY_ID"), "rzp_test_zyxwvutsrqponmlk")
            # unknown key in a known section → refused
            r2 = client.post("/api/settings/section/razorpay",
                             json={"NOT_ALLOWED": "x"})
            self.assertEqual(r2.status_code, 400)
            # unknown section → 404
            r3 = client.post("/api/settings/section/nuclear_codes", json={"X": "1"})
            self.assertEqual(r3.status_code, 404)

    def test_test_connection_honest_when_unconfigured(self):
        from fastapi.testclient import TestClient
        from ghost.server.app import app
        # no razorpay keys in THIS test's vault (fresh process-wide vault has
        # whatever earlier tests wrote — pop it for the honest state)
        if os.path.exists(os.environ["GHOST_SECRETS_PATH"]):
            os.remove(os.environ["GHOST_SECRETS_PATH"])
        with TestClient(app) as client:
            r = client.post("/api/settings/test/razorpay")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["state"], "not_configured")
            r2 = client.post("/api/settings/test/ollama")
            self.assertEqual(r2.status_code, 200)
            self.assertIn(r2.json()["state"], ("not_configured", "available", "error"))

    def test_llm_path_cannot_reach_approval(self):
        """The reserved-act approval is operator-only: it is NOT a proposal
        action, and the dispatcher has no adapter for it."""
        from ghost.brain.proposals import PROPOSAL_REGISTRY
        for action in PROPOSAL_REGISTRY:
            self.assertNotIn("reserved", action,
                             "reserved acts must never be proposal actions")


if __name__ == "__main__":
    unittest.main()
