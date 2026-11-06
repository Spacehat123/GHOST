"""P8 tests (Operator's 11): the micro-jobs copilot.

- PURITY: there is deliberately NO auto-bid code path (Upwork ToS). No
  api.upwork.com anywhere (the only Upwork touch is READ-ONLY search pages),
  no submit/send/auto-bid method in the copilot modules, every proposal is
  born 'awaiting_human_send'.
- e2e on fixtures: RSS + Upwork search HTML → parsed → scored with WRITTEN
  reasons → deduped → proposal drafted on YOUR click → sent/won/lost.
- Honesty: a source that fails or parses to zero tiles is REPORTED, never
  silently swallowed into a quiet zero.
"""
import json
import os
import tempfile
import unittest
import uuid
import ast

TMP = tempfile.mkdtemp(prefix="ghost-p8-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_MICROJOBS_DB"] = os.path.join(TMP, f"mj-{uuid.uuid4().hex}.db")

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

RSS_FIXTURE = """<?xml version="1.0"?>
<rss><channel>
<item><title>Build a restaurant landing page with booking</title>
<link>https://jobs.example/1</link>
<description><![CDATA[Need a landing page with booking form. Budget: $800]]></description></item>
<item><title>Logo design for bakery</title>
<link>https://jobs.example/2</link>
<description><![CDATA[Just a logo, no website.]]></description></item>
</channel></rss>"""

UPWORK_FIXTURE = """
<html><body>
<a href="/jobs/~0123abcdefRestaurant-site.html" data-test="JobTileLink">Restaurant website with online booking</a>
<a href="/jobs/~0456ghijklThreejs-product.html" data-test="JobTileLink">Three.js 3D product viewer for furniture store</a>
<a href="/jobs/~0123abcdefRestaurant-site.html" data-test="JobTileLink">Restaurant website with online booking</a>
</body></html>"""

EMPTY_PAGE = "<html><body>Nothing here (markup changed?)</body></html>"


class TestPurity(unittest.TestCase):
    """The ToS contract, enforced by static analysis."""

    def test_no_upwork_api_write_anywhere(self):
        """Ghost never touches the Upwork API at all — posting a proposal is
        done by the HUMAN on upwork.com. No api.upwork.com, no OAuth."""
        offenders = []
        for root in ("src",):
            for dirpath, _, files in os.walk(os.path.join(REPO, root)):
                for fn in files:
                    if fn.endswith(".py"):
                        p = os.path.join(dirpath, fn)
                        t = open(p, encoding="utf-8").read()
                        if "api.upwork.com" in t or "upwork_oauth" in t:
                            offenders.append(p)
        self.assertEqual(offenders, [],
                         "the Upwork API must never be touched — a human posts")

    def test_copilot_modules_have_no_send_or_submit(self):
        import ghost.workers.microjobs as mj
        import ghost.workers.growth as gr
        for mod in (mj, gr):
            for name, obj in vars(mod).items():
                if isinstance(obj, type):
                    for meth in vars(obj):
                        self.assertNotIn(meth.lower(),
                                         ("send", "submit", "auto_bid", "bid_now",
                                          "post_job", "apply_job"),
                                         f"{mod.__name__}.{name}.{meth} must not exist")

    def test_proposals_are_born_awaiting_human_send(self):
        src = open(os.path.join(REPO, "src", "ghost", "workers", "microjobs.py")).read()
        self.assertIn("awaiting_human_send", src)
        # the ONLY way a proposal exists is add_proposal — status hardcoded
        self.assertIn("'awaiting_human_send'", src)


class TestSweepE2E(unittest.TestCase):

    def setUp(self):
        self.db = os.path.join(TMP, f"mj-{uuid.uuid4().hex}.db")
        from ghost.workers.microjobs import JobStore
        self.store = JobStore(db_path=self.db)

    def test_rss_and_upwork_sources_stored_scored_deduped(self):
        from ghost.workers.microjobs import sweep
        sources = {"rss_feeds": ["https://feeds.example/jobs.rss"],
                   "upwork_search_urls": ["https://www.upwork.com/nx/search/"]}
        out = sweep(store=self.store, sources=sources,
                    fetch_page=lambda url: UPWORK_FIXTURE,
                    fetch_rss=lambda url: RSS_FIXTURE)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["failed"], 0)
        jobs = self.store.list_jobs()
        urls = {j["url"] for j in jobs}
        self.assertEqual(len(jobs), 4, "2 RSS + 2 unique Upwork tiles (the dup is deduped)")
        self.assertIn("https://jobs.example/1", urls)
        self.assertIn("https://www.upwork.com/jobs/~0456ghijklThreejs-product.html", urls)
        # fit scoring with written reasons; the logo-only job scores low
        by_url = {j["url"]: j for j in jobs}
        landing = by_url["https://jobs.example/1"]
        self.assertGreaterEqual(landing["fit_score"], 3.0)
        reasons = json.loads(landing["reasons_json"])
        self.assertTrue(any("booking_or_contact" in r for r in reasons))
        three_d = by_url["https://www.upwork.com/jobs/~0456ghijklThreejs-product.html"]
        self.assertGreaterEqual(three_d["fit_score"], 5.0)
        # second sweep: everything dedups to zero new rows
        out2 = sweep(store=self.store, sources=sources,
                     fetch_page=lambda url: UPWORK_FIXTURE,
                     fetch_rss=lambda url: RSS_FIXTURE)
        self.assertEqual(out2["stored"], 0, "URL dedup — no job is stored twice")

    def test_failed_source_is_reported_not_swallowed(self):
        from ghost.workers.microjobs import sweep
        sources = {"rss_feeds": ["https://feeds.example/jobs.rss"],
                   "upwork_search_urls": ["https://www.upwork.com/nx/search/"]}
        out = sweep(store=self.store, sources=sources,
                    fetch_page=lambda url: EMPTY_PAGE,
                    fetch_rss=lambda url: (_ for _ in ()).throw(RuntimeError("feed down")))
        self.assertEqual(out["failed"], 2, "both failures are counted")
        self.assertEqual(len(out["failures"]), 2)
        self.assertTrue(any("zero job tiles" in f for f in out["failures"]))
        self.assertEqual(self.store.list_jobs(), [])


class TestProposalFlow(unittest.TestCase):

    def setUp(self):
        self.db = os.path.join(TMP, f"mj-{uuid.uuid4().hex}.db")
        from ghost.workers.microjobs import JobStore
        self.store = JobStore(db_path=self.db)
        self.store.add_job({"source": "upwork_search",
                            "url": f"https://www.upwork.com/jobs/~{uuid.uuid4().hex[:8]}",
                            "title": "Three.js 3D product viewer for furniture store",
                            "budget_text": "$500", "fit_score": 6.0,
                            "reasons": ["three_d_showcase (+5): matched three.js, 3d"]})

    def test_draft_once_then_already_drafted(self):
        from ghost.workers.microjobs import JobStore, draft_proposal_text, score_fit
        job = self.store.list_jobs()[0]
        body = draft_proposal_text(job, {"deliverables": ["three_d_showcase"]})
        self.assertIn("3D", body)
        self.assertIn("GLB", body, "the scope question is tailored to the deliverable")
        prop = self.store.add_proposal(job["id"], body)
        self.assertEqual(prop["status"], "awaiting_human_send")
        self.assertEqual(self.store.get_job(job["id"])["status"], "proposed")
        self.assertIsNotNone(self.store.get_proposal(job["id"]))
        # status transitions: the human's journey
        self.assertTrue(self.store.set_proposal_status(job["id"], "sent"))
        self.assertTrue(self.store.set_proposal_status(job["id"], "won"))
        self.assertFalse(self.store.set_proposal_status(job["id"], "nonsense"))

    def test_fit_scoring_written_reasons(self):
        from ghost.workers.microjobs import score_fit
        fit = score_fit({"title": "Fix slow WordPress site, speed up lighthouse",
                         "description": "page speed is terrible"})
        self.assertGreaterEqual(fit["fit_score"], 5.0)
        self.assertTrue(all(r and isinstance(r, str) for r in fit["reasons"]))
        self.assertTrue(any("fix_or_speed" in r for r in fit["reasons"]))
        fit2 = score_fit({"title": "Voice actor needed", "description": "narration for audiobook"})
        self.assertEqual(fit2["fit_score"], 0.0)
        self.assertEqual(fit2["reasons"], [])


class TestAPI(unittest.TestCase):

    def setUp(self):
        os.environ["GHOST_MICROJOBS_DB"] = os.path.join(TMP, f"mj-api-{uuid.uuid4().hex}.db")
        self._saved_crm = os.environ.get("GHOST_CRM_DB_PATH")
        os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
        from ghost.crm.db import CRMDatabase
        CRMDatabase._instance = CRMDatabase(os.environ["GHOST_CRM_DB_PATH"])
        self.addCleanup(self._restore)
        from starlette.testclient import TestClient
        from ghost.server.app import app
        from ghost.server.agency_api import get_or_create_session_token
        self.client = TestClient(app)
        self.headers = {"X-Session-Token": get_or_create_session_token()}

    def _restore(self):
        from ghost.crm.db import CRMDatabase
        CRMDatabase._instance = None
        if self._saved_crm is None:
            os.environ.pop("GHOST_CRM_DB_PATH", None)
        else:
            os.environ["GHOST_CRM_DB_PATH"] = self._saved_crm

    def test_board_and_propose_via_api(self):
        from ghost.workers.microjobs import JobStore
        store = JobStore()
        store.add_job({"source": "rss", "url": "https://jobs.example/x",
                       "title": "Landing page for dental clinic", "budget_text": "$600",
                       "fit_score": 5.0, "reasons": ["site_or_landing_page (+4)"]})
        res = self.client.get("/api/microjobs", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        jobs = res.json()["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertIn("you press send", res.json()["note"])
        job_id = jobs[0]["id"]
        res = self.client.post(f"/api/microjobs/{job_id}/propose", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["proposal"]["status"], "awaiting_human_send")
        res = self.client.post(f"/api/microjobs/{job_id}/propose", headers=self.headers)
        self.assertEqual(res.json()["status"], "already_drafted")
        res = self.client.post(f"/api/microjobs/{job_id}/status", headers=self.headers,
                               json={"status": "sent"})
        self.assertEqual(res.json()["new_status"], "sent")
        res = self.client.post(f"/api/microjobs/{job_id}/status", headers=self.headers,
                               json={"status": "bogus"})
        self.assertEqual(res.status_code, 400)

    def test_sweep_endpoint_honest_with_no_sources(self):
        res = self.client.post("/api/microjobs/sweep", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["considered"], 0, "no sources configured yet — honest zero")


if __name__ == "__main__":
    unittest.main()
