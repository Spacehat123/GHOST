"""P3 tests (Operator's 11): the per-demo approval loop — goal 5.

Open locally → the operator's eyes → ONE approval click = ONE single-use
deploy grant → VercelDeployer with every existing gate → demo_url backfilled.

The load-bearing invariant: hosting is REFUSED without the click (no grant
exists to spend), and the click does NOT bypass QA/snapshot/halt — approval
is authority, never evidence.
"""
import asyncio
import json
import os
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, patch

TMP = tempfile.mkdtemp(prefix="ghost-p3-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
os.environ["GHOST_DEMOS_DIR"] = os.path.join(TMP, "demos")
os.environ["GHOST_SNAPSHOTS_DIR"] = os.path.join(TMP, "snapshots")

from starlette.testclient import TestClient

from ghost.crm.db import CRMDatabase
from ghost.server.app import app
from ghost.server.agency_api import get_or_create_session_token


class EnvPinned(unittest.TestCase):

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_AUDIT_DB_PATH", "GHOST_CRM_DB_PATH", "GHOST_DEMOS_DIR",
                        "GHOST_SNAPSHOTS_DIR", "GHOST_GRANT_SECRET",
                        "GHOST_AUTHORITY_NONCE_DB", "GHOST_GRANT_SOCKET",
                        "GHOST_UNIFIED_DB_PATH", "GHOST_HALT_FILE", "GHOST_HALT_META")}
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
        os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
        os.environ["GHOST_DEMOS_DIR"] = os.path.join(TMP, f"demos-{uuid.uuid4().hex}")
        os.environ["GHOST_SNAPSHOTS_DIR"] = os.path.join(TMP, f"snap-{uuid.uuid4().hex}")
        os.environ["GHOST_GRANT_SECRET"] = f"p3-{uuid.uuid4().hex}"
        os.environ["GHOST_AUTHORITY_NONCE_DB"] = os.path.join(TMP, f"n-{uuid.uuid4().hex}.db")
        os.environ.pop("GHOST_GRANT_SOCKET", None)   # in-process fallback: daemon not under test here
        os.environ["GHOST_UNIFIED_DB_PATH"] = os.path.join(TMP, f"ghost-{uuid.uuid4().hex}.db")
        os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, f"HALTED-{uuid.uuid4().hex}")
        os.environ["GHOST_HALT_META"] = os.environ["GHOST_HALT_FILE"] + ".json"
        self._crm()
        self.token = get_or_create_session_token()
        self.client = TestClient(app)
        self.headers = {"X-Session-Token": self.token}
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def _crm():
        crm = CRMDatabase(os.environ["GHOST_CRM_DB_PATH"])
        CRMDatabase._instance = crm
        return crm

    def tearDown(self):
        CRMDatabase._instance = None

    def _ledger(self, event_type):
        from ghost.core.audit import AuditLedger
        return AuditLedger.get_instance().query(event_type=event_type, limit=50)

    def _demo(self, slug, qa=False):
        """Fixture: a built demo (+ passing QA evidence + snapshot when qa)."""
        from ghost.workers.snapshots import SnapshotStore
        d = os.path.join(os.environ["GHOST_DEMOS_DIR"], slug)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w") as f:
            f.write("<html><body>demo</body></html>")
        if qa:
            os.makedirs(os.path.join(d, "reports"), exist_ok=True)
            with open(os.path.join(d, "reports", "qa.json"), "w") as f:
                json.dump({"passed": True, "screenshots_captured": 1,
                           "screenshots": ["qa-shot-01.png"]}, f)
            with open(os.path.join(d, "qa-shot-01.png"), "wb") as f:
                f.write(b"\x89PNG fake-with-variance-not-blank")
            SnapshotStore().snapshot(slug, d)
        self._crm().add_or_update_lead(dict(slug=slug, name="Salt", google_category="Restaurant"))
        return d


class TestOpenLocally(EnvPinned):

    def test_open_audits_and_returns_local_url(self):
        slug = f"open-{uuid.uuid4().hex[:6]}"
        self._demo(slug)
        res = self.client.post(f"/api/demos/{slug}/open", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["url"].startswith("/demos/"))
        self.assertIn("local preview", body["note"])
        rows = self._ledger("demo.opened")
        self.assertEqual(len(rows), 1, "the open click is audited")

    def test_open_unknown_demo_404(self):
        res = self.client.post("/api/demos/no-such-demo/open", headers=self.headers)
        self.assertEqual(res.status_code, 404)

    def test_board_lists_qa_and_host_state(self):
        slug = f"board-{uuid.uuid4().hex[:6]}"
        self._demo(slug, qa=True)
        res = self.client.get("/api/demos", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        demos = {d["slug"]: d for d in res.json()["demos"]}
        self.assertIn(slug, demos)
        self.assertTrue(demos[slug]["qa_passed"])
        self.assertFalse(demos[slug]["deployed"])


class TestAuthorizeHost(EnvPinned):

    def test_denied_without_the_click(self):
        slug = f"noclick-{uuid.uuid4().hex[:6]}"
        self._demo(slug, qa=True)
        with patch("ghost.workers.deploy.VercelDeployer.deploy_demo",
                   new=AsyncMock()) as dep:
            res = self.client.post(f"/api/demos/{slug}/authorize-host",
                                   headers=self.headers, json={"confirmed": False})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "denied")
        dep.assert_not_awaited()
        self.assertEqual(self._ledger("effect.grant_issued"), [],
                         "no click, no grant — hosting authority never existed")
        self.assertEqual(self._ledger("demo.host_authorized"), [])

    def test_refused_while_halted(self):
        from ghost.core.halt import trip
        trip(reason="test")
        slug = f"halt-{uuid.uuid4().hex[:6]}"
        self._demo(slug, qa=True)
        try:
            res = self.client.post(f"/api/demos/{slug}/authorize-host",
                                   headers=self.headers, json={"confirmed": True})
            self.assertEqual(res.status_code, 409)
            self.assertIn("HALTED", res.json()["detail"])
        finally:
            from ghost.core.halt import resume
            resume(by="test-cleanup")
        self.assertEqual(self._ledger("effect.grant_issued"), [],
                         "even the operator's click mints nothing while frozen")

    def test_unknown_demo_404_no_grant(self):
        res = self.client.post("/api/demos/ghost-demo/authorize-host",
                               headers=self.headers, json={"confirmed": True})
        self.assertEqual(res.status_code, 404)

    def test_approve_grant_deploy_url_backfill(self):
        slug = f"e2e-{uuid.uuid4().hex[:6]}"
        self._demo(slug, qa=True)
        with patch("ghost.workers.deploy.VercelDeployer.deploy_demo",
                   new=AsyncMock(return_value={"status": "success",
                                               "url": "https://e2e-demo.vercel.app"})) as dep:
            res = self.client.post(f"/api/demos/{slug}/authorize-host",
                                   headers=self.headers,
                                   json={"confirmed": True, "operator_note": "looked great"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["url"], "https://e2e-demo.vercel.app")
        # the deploy ran WITH the single-use grant the click minted
        dep.assert_awaited_once()
        self.assertTrue(dep.await_args.kwargs.get("grant_token", "").startswith("gt."))
        # approval evidence + grant issuance + hosted outcome, all on the ledger
        self.assertEqual(len(self._ledger("demo.host_authorized")), 1)
        self.assertEqual(len(self._ledger("effect.grant_issued")), 1)
        self.assertEqual(len(self._ledger("demo.hosted")), 1)
        # demo_url backfilled into the CRM — pitch drafts pick it up from here
        lead = CRMDatabase.get_instance().get_lead(slug)
        self.assertEqual(lead["demo_url"], "https://e2e-demo.vercel.app")

    def test_approval_is_not_evidence_qa_gate_still_refuses(self):
        """The click mints authority; it does NOT fake a QA pass. With no QA
        evidence on disk the real deploy refuses at its own gate."""
        slug = f"noqa-{uuid.uuid4().hex[:6]}"
        self._demo(slug, qa=False)
        res = self.client.post(f"/api/demos/{slug}/authorize-host",
                               headers=self.headers, json={"confirmed": True})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertNotEqual(body.get("status"), "success")
        lead = CRMDatabase.get_instance().get_lead(slug)
        self.assertIsNone(lead.get("demo_url"),
                          "no URL may be backfilled without a real deploy")


if __name__ == "__main__":
    unittest.main()
