"""P9 tests (Operator's 11): the single-window operator surface.

- THE LOOP: every panel the deck renders has a real endpoint behind it —
  dossiers, autonomy bundle, notifications (pings + digests), staged fixes
  with approval, payments overview. The operator's full loop works without
  leaving the one window.
- TAB WIRING: every data-view tab in index.html has a loader wired in
  agency_deck.js (a missing loader = a dead tab = a lie in the nav).
"""
import json
import os
import re
import tempfile
import time
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-p9-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
os.environ["GHOST_DOSSIERS_DIR"] = os.path.join(TMP, "dossiers")
os.environ["GHOST_FIXER_STAGED"] = os.path.join(TMP, "fixer_staged")

from starlette.testclient import TestClient

from ghost.crm.db import CRMDatabase
from ghost.server.app import app
from ghost.server.agency_api import get_or_create_session_token

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class EnvPinned(unittest.TestCase):

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_AUDIT_DB_PATH", "GHOST_CRM_DB_PATH", "GHOST_DOSSIERS_DIR",
                        "GHOST_FIXER_STAGED", "GHOST_HALT_FILE", "GHOST_HALT_META",
                        "GHOST_UNIFIED_DB_PATH", "GHOST_DATA_DIR")}
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
        os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
        os.environ["GHOST_DOSSIERS_DIR"] = os.path.join(TMP, f"dossiers-{uuid.uuid4().hex}")
        os.environ["GHOST_FIXER_STAGED"] = os.path.join(TMP, f"staged-{uuid.uuid4().hex}")
        os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, f"H-{uuid.uuid4().hex}")
        os.environ["GHOST_HALT_META"] = os.environ["GHOST_HALT_FILE"] + ".json"
        os.environ["GHOST_UNIFIED_DB_PATH"] = os.path.join(TMP, f"g-{uuid.uuid4().hex}.db")
        os.environ["GHOST_DATA_DIR"] = os.path.join(TMP, f"data-{uuid.uuid4().hex}")
        self.crm = CRMDatabase(os.environ["GHOST_CRM_DB_PATH"])
        CRMDatabase._instance = self.crm
        self.client = TestClient(app)
        self.headers = {"X-Session-Token": get_or_create_session_token()}
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def tearDown(self):
        CRMDatabase._instance = None


class TestDossierPanel(EnvPinned):

    def test_list_detail_and_photo_endpoints(self):
        from ghost.workers.dossier import DossierBuilder
        self.crm.add_or_update_lead(dict(slug="salt-d", name="Salt", google_category="R"))
        DossierBuilder(fetch_binary=lambda u: b"\xff\xd8jpeg-bytes").build_for_lead(
            "salt-d", recon={"name": "Salt", "category": "R", "rating": 4.5,
                             "reviewsCount": 90,
                             "photos": [{"url": "https://cdn.example/p/x=w1600-h1200-k-no"}]})
        res = self.client.get("/api/dossiers", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        dossiers = res.json()["dossiers"]
        self.assertEqual(len(dossiers), 1)
        self.assertEqual(dossiers[0]["photos"], 1)
        slug = dossiers[0]["slug"]
        res = self.client.get(f"/api/dossiers/{slug}", headers=self.headers)
        body = res.json()
        self.assertEqual(body["facts"]["identity"]["name"]["value"], "Salt")
        self.assertTrue(body["photos"])
        res = self.client.get(f"/api/dossiers/{slug}/photo/{body['photos'][0]}",
                              headers=self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertGreater(len(res.content), 0)
        # path traversal refused
        res = self.client.get(f"/api/dossiers/{slug}/photo/..%2F..%2Fsecrets",
                              headers=self.headers)
        self.assertIn(res.status_code, (400, 404))


class TestAutonomyAndNotifications(EnvPinned):

    def test_autonomy_bundle_fields(self):
        res = self.client.get("/api/autonomy", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        b = res.json()
        for key in ("halt", "ladder", "runtime", "supervisor", "rhythm", "darkrun"):
            self.assertIn(key, b)
        self.assertIn("scheduler_cycle", b["rhythm"])
        self.assertFalse(b["halt"]["halted"])

    def test_pings_visible_and_digest_readable(self):
        from ghost.core.audit import audit
        audit("reply.complaint", subject="angry-lead", ping="(b) client distress: test")
        os.makedirs(os.path.join(TMP, "digests"), exist_ok=True)
        # the digest dir lives under the repo data dir; the endpoint reads
        # REPO_DIR/data/digests — write today's fixture there
        import ghost.server.agency_api as api_mod
        digest_dir = os.path.join(api_mod.REPO_DIR, "data", "digests")
        os.makedirs(digest_dir, exist_ok=True)
        day = time.strftime("%Y-%m-%d")
        with open(os.path.join(digest_dir, f"{day}.md"), "w") as f:
            f.write("# Daily Digest\n- leads contacted: 3\n")
        self.addCleanup(lambda: os.remove(os.path.join(digest_dir, f"{day}.md")))
        res = self.client.get("/api/notifications", headers=self.headers)
        body = res.json()
        self.assertTrue(any(p["type"] == "reply.complaint" and "distress" in p["ping"]
                            for p in body["pings"]),
                        "the (b) ping must be visible in the center")
        self.assertIn(f"{day}.md", body["digests"])
        res = self.client.get(f"/api/notifications/digest/{day}", headers=self.headers)
        self.assertIn("Daily Digest", res.json()["markdown"])
        res = self.client.get("/api/notifications/digest/not-a-day", headers=self.headers)
        self.assertEqual(res.status_code, 400)


class TestFixesAndPayments(EnvPinned):

    def test_staged_fix_lifecycle_through_the_deck(self):
        from ghost.control.fixers import FixIssue, stage_patch
        site_dir = tempfile.mkdtemp(prefix="p9-site-", dir=TMP)
        site = os.path.join(site_dir, "index.html")   # patches key by BASENAME
        with open(site, "w") as f:
            f.write('<a href="htp://broken.example"></a>')
        issue = FixIssue(key="k1", site_slug="acme", description="broken contact link",
                         scope_files=[site],
                         check_command='grep -q "fixed.example" "{file}"')
        staged = stage_patch(issue, {"index.html": '<a href="https://fixed.example"></a>'},
                             "exit=0")
        self.assertIn("broken.example", open(site).read())
        res = self.client.get("/api/fixer/staged", headers=self.headers)
        rows = res.json()["staged"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "staged")
        res = self.client.post(f"/api/fixer/{rows[0]['staged_id']}/approve",
                               headers=self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertIn("fixed.example", open(site).read())

    def test_payments_overview_shape_and_collect_only_note(self):
        self.crm.add_or_update_client(slug="acme-pay", business_name="Acme Ltd",
                                      deal_value=1200.0, status="deposit_received")
        res = self.client.get("/api/payments/overview", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        for key in ("rail", "deals", "note"):
            self.assertIn(key, body)
        self.assertIn("never sends", body["note"])
        self.assertIn("razorpay", body["rail"])
        self.assertTrue(any(d["slug"] == "acme-pay" for d in body["deals"]))


class TestTabWiring(unittest.TestCase):
    """A dead tab is a lie in the nav — every data-view must have a loader."""

    def test_every_tab_has_a_loader(self):
        html = open(os.path.join(REPO, "gui", "index.html"), encoding="utf-8").read()
        js = open(os.path.join(REPO, "gui", "static", "js", "agency_deck.js"),
                  encoding="utf-8").read()
        views = set(re.findall(r'data-view="([a-z]+)"', html))
        self.assertTrue(views, "the deck nav must define tabs")
        # the loaders map inside safeLoad is the single source of truth
        map_match = re.search(r"const loaders = \{(.*?)\};", js, re.S)
        self.assertTrue(map_match, "safeLoad's loaders map is missing")
        loader_keys = set(re.findall(r"(\w+):\s*load\w+", map_match.group(1)))
        missing = [v for v in sorted(views) if v not in loader_keys]
        self.assertEqual(missing, [], f"tabs without loaders: {missing}")
        # and every view section exists
        for v in views:
            self.assertIn(f'id="view-{v}"', html, f"tab {v} has no section")


if __name__ == "__main__":
    unittest.main()
