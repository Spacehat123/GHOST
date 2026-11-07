"""Tests for the operator's Nov-2026 brief: photo policy split (demos stage
real photos immediately, websites WAIT for client photos), the SEO/AEO pack,
the editorial house style riding every design brief, and the OWASP ship-gate.
"""
import json
import os
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-webflow-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
os.environ["GHOST_DOSSIERS_DIR"] = os.path.join(TMP, "dossiers")
os.environ["GHOST_DOCUMENTS_DIR"] = os.path.join(TMP, "documents")

from ghost.crm.db import CRMDatabase
from ghost.workers.dossier import stage_photos_into_demo
from ghost.workers.seo_pack import (build_seo_pack, mark_client_photos_received,
                                    verify_ship_gate, website_ready_for_build)


class EnvPinned(unittest.TestCase):

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_AUDIT_DB_PATH", "GHOST_CRM_DB_PATH",
                        "GHOST_DOSSIERS_DIR", "GHOST_DOCUMENTS_DIR",
                        "GHOST_PROJECT_ROOT")}
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
        os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
        os.environ["GHOST_DOSSIERS_DIR"] = os.path.join(TMP, f"d-{uuid.uuid4().hex}")
        os.environ["GHOST_DOCUMENTS_DIR"] = os.path.join(TMP, f"doc-{uuid.uuid4().hex}")
        self.crm = CRMDatabase(os.environ["GHOST_CRM_DB_PATH"])
        CRMDatabase._instance = self.crm
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def tearDown(self):
        CRMDatabase._instance = None

    def _lead(self, slug="salt-w", **over):
        row = dict(slug=slug, name="Salt", google_category="Restaurant")
        row.update(over)
        self.crm.add_or_update_lead(row)
        return slug


class TestDemoPhotoStaging(EnvPinned):
    """Demos get the business's real photos IMMEDIATELY (no waiting), staged
    locally with provenance — never hotlinks; stock only backfills."""

    def test_staging_fills_slots_locally_with_provenance(self):
        demo_dir = tempfile.mkdtemp(prefix="demo-", dir=TMP)
        photos = [
            {"url": "https://lh5.googleusercontent.com/p/ABC=w1600-h1200-k-no"},
            {"url": "https://lh5.googleusercontent.com/p/DEF=w1600-h1200-k-no"},
            {"url": "https://lh5.googleusercontent.com/p/GHI=w1600-h1200-k-no"},
            {"url": "https://lh5.googleusercontent.com/a/avatar-x=s96-c"},  # avatar skipped
        ]
        out = stage_photos_into_demo(
            photos, demo_dir, "salt-d",
            fetch_binary=lambda url: b"\xff\xd8jpeg-" + url[-6:].encode())
        self.assertEqual(out["staged"], 3)
        self.assertEqual(out["slots"]["hero_bg"], "assets/photos/photo-01.jpg")
        self.assertTrue(os.path.isfile(os.path.join(demo_dir, "assets/photos/photo-01.jpg")))
        src = json.load(open(os.path.join(demo_dir, "SOURCES.json")))
        kinds = [a["type"] for a in src["assets"]]
        self.assertEqual(kinds.count("business_photo"), 3, "every photo has a row")

    def test_dossier_files_stage_without_fetch(self):
        demo_dir = tempfile.mkdtemp(prefix="demo-", dir=TMP)
        dd = os.path.join(os.environ["GHOST_DOSSIERS_DIR"], "salt-d2", "photos")
        os.makedirs(dd, exist_ok=True)
        for i, name in enumerate(("photo-01.jpg", "photo-02.jpg")):
            with open(os.path.join(dd, name), "wb") as f:
                f.write(b"\xff\xd8dossier" + bytes([i]))
        out = stage_photos_into_demo(
            [{"path": os.path.join(dd, "photo-01.jpg")},
             {"path": os.path.join(dd, "photo-02.jpg")}],
            demo_dir, "salt-d2", fetch_binary=None)
        self.assertEqual(out["staged"], 2, "dossier photos stage with no network")

    def test_state_machine_stage_method_prefers_real_photos(self):
        from ghost.workers.demo_state_machine import AutonomousDemoStateMachine
        sm = AutonomousDemoStateMachine("Salt Bistro", slug="salt-stg")
        demo_dir = sm.out_dir
        os.makedirs(demo_dir, exist_ok=True)
        dd = os.path.join(os.environ["GHOST_DOSSIERS_DIR"], sm.slug, "photos")
        os.makedirs(dd, exist_ok=True)
        with open(os.path.join(dd, "photo-01.jpg"), "wb") as f:
            f.write(b"\xff\xd8real")
        assets = {"hero_bg": "https://images.unsplash.com/old-stock", "gallery_1": "stock"}
        n = sm.stage_real_photos(assets, {})
        self.assertGreaterEqual(n, 1)
        self.assertTrue(assets["hero_bg"].startswith("assets/photos/"),
                        "the hero slot carries the business's OWN photo")
        self.assertNotIn("gallery_1", assets,
                         "unfilled slots stay UNFILLED — stock never backfills")

    def test_house_style_rides_the_design_brief(self):
        from ghost.workers.demo_state_machine import AutonomousDemoStateMachine
        sm = AutonomousDemoStateMachine("Salt Bistro", slug="salt-hs")
        section = sm._house_style_section()
        self.assertIn("HOUSE STYLE OVERRIDE", section)
        self.assertIn("editorial", section.lower())
        self.assertIn("Never hotlink stock", section)


class TestSeoPack(EnvPinned):

    def test_pack_built_from_dossier_facts(self):
        from ghost.workers.dossier import DossierBuilder
        slug = self._lead("seo-lead")
        DossierBuilder(fetch_binary=None).build_for_lead(slug, recon={
            "name": "Salt", "category": "Fine dining", "address": "12 Test Ln, Mumbai",
            "phone": "+91 98765 43210", "website": None, "hours": "Tu-Su 18-23",
            "rating": 4.7, "reviewsCount": 180, "photos": []})
        out = build_seo_pack(slug, crm=self.crm)
        self.assertEqual(out["status"], "ok")
        p = out["dir"]
        for f in ("meta.json", "llms.txt", "local_seo_checklist.md",
                  "photo_request_email.md", "security_checklist.md",
                  "schema/LocalBusiness.jsonld", "schema/FAQPage.jsonld"):
            self.assertTrue(os.path.exists(os.path.join(p, f)), f)
        lb = json.load(open(os.path.join(p, "schema/LocalBusiness.jsonld")))
        self.assertEqual(lb["telephone"], "+91 98765 43210")
        self.assertIn("Mumbai", lb["address"]["addressLocality"])
        faq = json.load(open(os.path.join(p, "schema/FAQPage.jsonld")))
        self.assertGreaterEqual(len(faq["mainEntity"]), 4)
        llms = open(os.path.join(p, "llms.txt")).read()
        self.assertIn("Salt", llms)
        # missing facts surface as inquiries — never invented
        self.assertIsInstance(out["inquiries"], list)

    def test_faq_answers_contain_no_fabricated_prices(self):
        slug = self._lead("seo-faq")
        out = build_seo_pack(slug, crm=self.crm)
        faq = json.load(open(os.path.join(out["dir"], "schema/FAQPage.jsonld")))
        blob = json.dumps(faq).lower()
        self.assertNotIn("₹", blob)
        self.assertNotIn("costs ₹", blob)
        self.assertNotIn("only ₹", blob)


class TestPhotoGate(EnvPinned):
    """Websites WAIT for client photos; the operator's mark opens the gate."""

    def test_gate_closed_until_marked(self):
        slug = self._lead("gate-lead")
        res = website_ready_for_build(slug, crm=self.crm)
        self.assertFalse(res["ready"])
        self.assertIn("photos", res["reason"])
        out = mark_client_photos_received(slug, by="operator-test")
        self.assertTrue(out["received"])
        res2 = website_ready_for_build(slug, crm=self.crm)
        self.assertTrue(res2["ready"], res2)
        docs = self.crm.get_documents_for_lead(self.crm.get_lead(slug)["id"])
        self.assertTrue(any(d["kind"] == "client_photos" for d in docs))

    def test_mark_unknown_lead_refused(self):
        res = mark_client_photos_received("ghost-lead", by="test")
        self.assertFalse(res["received"])


class TestShipGate(EnvPinned):
    """OWASP ship-gate: CSP, no secrets, schema + llms.txt, NAP consistency."""

    def test_clean_site_passes(self):
        slug = self._lead("clean-site")
        from ghost.workers.dossier import DossierBuilder
        DossierBuilder(fetch_binary=None).build_for_lead(slug, recon={
            "name": "Salt", "category": "R", "phone": "+91 90000 00000",
            "address": "1 Ln, Mumbai", "rating": 4.5, "reviewsCount": 50})
        site = tempfile.mkdtemp(prefix="site-", dir=TMP)
        with open(os.path.join(site, "index.html"), "w") as f:
            f.write("<html><head><meta http-equiv='Content-Security-Policy' "
                    "content=\"default-src 'self'\">"
                    "<script type='application/ld+json'>{\"@type\":\"LocalBusiness\"}"
                    "</script></head><body>Call +91 90000 00000</body></html>")
        with open(os.path.join(site, "llms.txt"), "w") as f:
            f.write("# Salt\n")
        res = verify_ship_gate(slug, site)
        self.assertTrue(res["passed"], res)

    def test_missing_csp_and_leaked_key_fail(self):
        slug = self._lead("bad-site")
        site = tempfile.mkdtemp(prefix="site-", dir=TMP)
        with open(os.path.join(site, "index.html"), "w") as f:
            f.write("<html><body>hi</body>"
                    "<script>const k='sk-abcdefghijklmnop123456';</script></html>")
        res = verify_ship_gate(slug, site)
        self.assertFalse(res["passed"])
        blob = " ".join(res["reasons"])
        self.assertIn("Content-Security-Policy", blob)
        self.assertIn("API key", blob)
        self.assertIn("llms.txt", blob)


if __name__ == "__main__":
    unittest.main()


class TestPackInUI(EnvPinned):

    def test_payments_overview_carries_gate_state(self):
        from starlette.testclient import TestClient
        from ghost.server.app import app
        from ghost.server.agency_api import get_or_create_session_token
        self.crm.add_or_update_client(slug="ui-deal", business_name="UI Deal Ltd",
                                      deal_value=900.0, status="won")
        client = TestClient(app)
        headers = {"X-Session-Token": get_or_create_session_token()}
        res = client.get("/api/payments/overview", headers=headers)
        self.assertEqual(res.status_code, 200)
        deal = next(d for d in res.json()["deals"] if d["slug"] == "ui-deal")
        self.assertIn("photos_received", deal)
        self.assertFalse(deal["photos_received"])
        # generate + read a pack file through the deck's endpoint
        res = client.post("/api/leads/ui-deal/seo-pack", headers=headers)
        self.assertEqual(res.status_code, 200)
        res = client.get("/api/leads/ui-deal/seo-pack/file/local_seo_checklist", headers=headers)
        self.assertEqual(res.status_code, 200)
        self.assertIn("6 moves", res.json()["markdown"])
        res = client.get("/api/leads/ui-deal/seo-pack/file/../secrets", headers=headers)
        self.assertIn(res.status_code, (400, 404))


class TestDossierPhotoUpload(EnvPinned):
    """Option A: the operator drops real photos onto the dossier in the deck;
    they land in dossiers/<slug>/photos/ and the next build stages them."""

    def test_upload_endpoint_saves_images_and_refuses_junk(self):
        from starlette.testclient import TestClient
        from ghost.server.app import app
        from ghost.server.agency_api import get_or_create_session_token
        client = TestClient(app)
        headers = {"X-Session-Token": get_or_create_session_token()}
        self._lead("upload-lead")
        fake_jpg = b"\xff\xd8\xff\xe0 fake jpeg payload" * 10
        res = client.post("/api/dossiers/upload-lead/photos", headers=headers,
                          files=[("files", ("shot1.jpg", fake_jpg, "image/jpeg")),
                                 ("files", ("notes.txt", b"not an image", "text/plain"))])
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(len(body["saved"]), 1)
        self.assertEqual(body["refused"], ["notes.txt"], "non-images are refused")
        from ghost.workers.dossier import dossiers_dir
        photo_dir = os.path.join(dossiers_dir(), "upload-lead", "photos")
        self.assertEqual(len(os.listdir(photo_dir)), 1)

    def test_bad_slug_refused(self):
        from starlette.testclient import TestClient
        from ghost.server.app import app
        from ghost.server.agency_api import get_or_create_session_token
        client = TestClient(app)
        headers = {"X-Session-Token": get_or_create_session_token()}
        res = client.post("/api/dossiers/../secrets/photos", headers=headers,
                          files=[("files", ("x.jpg", b"j", "image/jpeg"))])
        self.assertIn(res.status_code, (400, 404))

    def test_ghost_asks_when_a_demo_has_no_photos(self):
        from ghost.workers.demo_state_machine import AutonomousDemoStateMachine
        sm = AutonomousDemoStateMachine("No Photo Cafe", slug="askpoto")
        os.makedirs(sm.out_dir, exist_ok=True)
        assets = {"hero_bg": "stock", "gallery_1": "stock"}
        sm.stage_real_photos(assets, {})
        from ghost.core.audit import AuditLedger
        rows = AuditLedger.get_instance().query(event_type="demo.photos_needed", limit=5)
        self.assertEqual(len(rows), 1,
                         "Ghost must ASK for photos through the ping contract")
        self.assertIn("Dossiers", rows[0]["payload"].get("ping", ""))
