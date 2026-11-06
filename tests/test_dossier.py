"""P2 tests (Operator's 11): the business dossier.

- e2e: lead + recon → dossier folder with facts.json, photos/, SOURCES.json,
  dossier.md — every photo provenance-tracked with a matching sha256.
- HONESTY: missing facts are null + listed in `inquire` + rendered
  [Inquire for quotation] — never guessed, never a placeholder image.
- VERIFIER: re-hashes every photo; tampering with a file or deleting a
  provenance row fails verification. No rows, no pass.
- QUALIFIER: need + pay-likelihood ride gaps_json with written reasons.
- DISPATCHER: dossier.build flows the strangler path (registry → adapter →
  executor → verifier-dict-is-the-result).
"""
import json
import os
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-p2-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
DOSSIER_DIR = os.path.join(TMP, "dossiers")
os.environ["GHOST_DOSSIERS_DIR"] = DOSSIER_DIR

from ghost.crm.db import CRMDatabase
from ghost.workers.dossier import DossierBuilder, verify_dossier
from ghost.workers.lead_qualifier import LeadQualifier

PHOTO_A = b"\xff\xd8\xe0 fake jpeg bytes A" * 50
PHOTO_B = b"\xff\xd8\xe0 fake jpeg bytes B" * 50

RECON = {
    "name": "Salt Restaurant",
    "category": "Fine dining",
    "address": "12 Test Lane, London",
    "phone": "+44 20 1234 5678",
    "website": None,               # recon found none — honest no_website case
    "hours": "Tu–Su 18:00–23:00",
    "rating": 4.7,
    "reviewsCount": 182,
    "photos": [
        {"url": "https://lh5.googleusercontent.com/p/ABC=w1600-h1200-k-no", "source": "google_maps_cdn"},
        {"url": "https://lh5.googleusercontent.com/a/user-avatar=s96-c"},   # avatar → must be skipped
        {"url": "https://example.com/menu.pdf"},                            # not a photo → skipped
    ],
}


def _fake_fetch(url):
    if "ABC" in url:
        return PHOTO_A
    if "menu.pdf" in url:
        return b"%PDF-not-a-photo"
    return PHOTO_B


class EnvPinned(unittest.TestCase):

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_AUDIT_DB_PATH", "GHOST_CRM_DB_PATH", "GHOST_DOSSIERS_DIR")}
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
        os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
        os.environ["GHOST_DOSSIERS_DIR"] = os.path.join(TMP, f"dossiers-{uuid.uuid4().hex}")
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestDossierBuild(EnvPinned):

    def _lead(self, slug="salt-restaurant", **over):
        crm = CRMDatabase.get_instance()
        row = dict(slug=slug, name="Salt", address=None, phone=None,
                   website=None, rating=None, review_count=None,
                   google_category="Restaurant", maps_cid=f"cid-{slug}")
        row.update(over)
        crm.add_or_update_lead(row)
        return slug

    def test_e2e_dossier_with_provenance(self):
        slug = self._lead()
        builder = DossierBuilder(fetch_binary=_fake_fetch)
        rep = builder.build_for_lead(slug, recon=RECON)
        self.assertEqual(rep["status"], "ok")
        self.assertEqual(rep["photos"], 1, "avatar + non-photo URL must be skipped")
        self.assertTrue(rep["verification"]["verified"], rep["verification"])

        d = rep["dir"]
        facts = json.load(open(os.path.join(d, "facts.json")))
        # recon filled what CRM lacked, honestly sourced
        self.assertEqual(facts["identity"]["phone"]["value"], "+44 20 1234 5678")
        self.assertEqual(facts["identity"]["phone"]["source"], "recon:google_maps")
        self.assertIsNone(facts["identity"]["website"]["value"])
        self.assertIn("website", facts["inquire"], "missing fact must be listed, not guessed")
        self.assertEqual(facts["site"]["verdict"], "no_website_found")
        self.assertEqual(facts["reputation"]["rating"]["value"], 4.7)

        rows = json.load(open(os.path.join(d, "SOURCES.json")))["photos"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["source_url"].startswith("https://lh5"))
        self.assertTrue(rows[0]["retrieved_at"])
        self.assertIn("license_note", rows[0])

        md = open(os.path.join(d, "dossier.md")).read()
        self.assertIn("[Inquire for quotation: website]", md)
        self.assertIn("sha256", md)
        # the photo is a LOCAL copy — the markdown cites it, nothing hotlinks
        self.assertTrue(os.path.exists(os.path.join(d, "photos", "photo-01.jpg")))

    def test_fetch_failure_skips_photo_honestly(self):
        slug = self._lead("failing-lead")

        def failing_fetch(url):
            raise ConnectionError("cdn unreachable")

        builder = DossierBuilder(fetch_binary=failing_fetch)
        rep = builder.build_for_lead(slug, recon=RECON)
        self.assertEqual(rep["photos"], 0, "failed downloads are skipped, never faked")
        self.assertTrue(rep["verification"]["verified"],
                        "a zero-photo dossier is still honest and verifiable")

    def test_unknown_lead_refused(self):
        rep = DossierBuilder(fetch_binary=_fake_fetch).build_for_lead("ghost-lead")
        self.assertEqual(rep["status"], "error")
        self.assertIn("not found", rep["reason"])


class TestDossierVerifier(EnvPinned):

    def _built(self, slug):
        from ghost.workers import dossier as dossier_mod
        crm = CRMDatabase.get_instance()
        crm.add_or_update_lead(dict(slug=slug, name="T", google_category="X"))
        DossierBuilder(fetch_binary=_fake_fetch).build_for_lead(slug, recon=RECON)
        return os.path.join(dossier_mod.dossiers_dir(), slug)

    def test_tampered_photo_fails_verification(self):
        slug = "tampered"
        d = self._built(slug)
        self.assertTrue(verify_dossier(slug)["verified"])
        p = os.path.join(d, "photos", "photo-01.jpg")
        with open(p, "ab") as f:
            f.write(b"tampered")
        rep = verify_dossier(slug)
        self.assertFalse(rep["verified"])
        self.assertTrue(any("sha256 mismatch" in r for r in rep["reasons"]))

    def test_photo_without_row_fails_and_row_without_photo_fails(self):
        slug = "unprovened"
        d = self._built(slug)
        with open(os.path.join(d, "photos", "photo-02.jpg"), "wb") as f:
            f.write(PHOTO_B)
        rep = verify_dossier(slug)
        self.assertFalse(rep["verified"])
        self.assertTrue(any("without provenance row" in r for r in rep["reasons"]))

        src = json.load(open(os.path.join(d, "SOURCES.json")))
        src["photos"].append({"file": "photos/photo-09.jpg", "source_url": "https://x",
                              "sha256": "0" * 64, "retrieved_at": 1.0})
        json.dump(src, open(os.path.join(d, "SOURCES.json"), "w"))
        rep = verify_dossier(slug)
        self.assertFalse(rep["verified"])
        self.assertTrue(any("row without file" in r for r in rep["reasons"]))

    def test_missing_dossier_fails_closed(self):
        rep = verify_dossier("never-built")
        self.assertFalse(rep["verified"])
        self.assertTrue(any("missing facts.json" in r for r in rep["reasons"]))


class TestQualifierAssessment(EnvPinned):

    def test_no_site_high_need_written_reasons(self):
        a = LeadQualifier.assess_need_and_payment(
            {"rating": 4.8, "review_count": 210}, "no_site",
            ["no_website", "active_social_without_site"], 22.0, "limited_company")
        self.assertGreaterEqual(a["need"]["score"], 24.0)
        self.assertEqual(a["need"]["summary"].startswith("high"), True)
        self.assertGreaterEqual(a["pay_likelihood"]["score"], 28.0)
        self.assertTrue(any("limited company" in r for r in a["pay_likelihood"]["reasons"]))
        self.assertTrue(all(r for r in a["need"]["reasons"]),
                        "every reason is a written, auditable sentence")

    def test_strong_site_low_need(self):
        a = LeadQualifier.assess_need_and_payment(
            {"rating": 3.0, "review_count": 5}, "weak_site", [], 5.0, "sole_trader")
        self.assertLessEqual(a["need"]["score"], 8.0)
        self.assertLessEqual(a["pay_likelihood"]["score"], 12.0)

    def test_evaluate_lead_embeds_assessment_in_gaps_json(self):
        crm = CRMDatabase.get_instance()
        q = LeadQualifier(crm=crm)
        lead = dict(slug="assessed", name="Assess & Co Ltd", website=None,
                    rating=4.6, review_count=150, google_category="Gym")
        res = q.evaluate_lead(lead, "boutique gym")
        gaps = json.loads(res["gaps_json"])
        self.assertIn("need", gaps)
        self.assertIn("pay_likelihood", gaps)
        self.assertTrue(gaps["need"]["reasons"])


class TestDispatcherWiring(EnvPinned):

    def test_dossier_build_through_the_planes(self):
        import asyncio
        from unittest.mock import patch
        from ghost.brain.dispatcher import dispatch
        from ghost.brain.proposals import LOCAL_MUTATION, Proposal
        crm = CRMDatabase.get_instance()
        crm.add_or_update_lead(dict(slug="dispatched", name="D", google_category="X"))

        p = Proposal(action="dossier.build", risk_class=LOCAL_MUTATION,
                     args={"slug": "dispatched", "recon": RECON}).validated()
        # the adapter builds its own DossierBuilder — patch the module-level
        # fetcher it falls back to (the LLM never injects callables via args)
        with patch("ghost.workers.dossier._default_fetch_binary", _fake_fetch):
            out = asyncio.run(dispatch(p))
        self.assertEqual(out.get("status"), "ok", out)
        self.assertTrue(out.get("verified"), out)
        self.assertEqual(out.get("photos"), 1)
        self.assertEqual(p.risk_class, LOCAL_MUTATION)

    def test_registry_consistency(self):
        from ghost.brain.proposals import PROPOSAL_REGISTRY, LOCAL_MUTATION
        reg = PROPOSAL_REGISTRY["dossier.build"]
        self.assertEqual(reg["risk_class"], LOCAL_MUTATION)
        self.assertEqual(reg["executor"].endswith("build_for_lead"), True)
        self.assertEqual(reg["verifier"].endswith("verify_dossier"), True)


if __name__ == "__main__":
    unittest.main()
