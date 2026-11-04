"""Unit and Integration Tests for Ghost Motion Flagship Builder & QA."""
import os
import json
import tempfile
import unittest

from ghost.workers.motion_site_builder import MotionSiteBuilder, MotionQA


class TestMotionSiteBuilder(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_build_requires_operator_approval(self):
        res = MotionSiteBuilder.build(
            slug="apex-detailing-london",
            business_name="Apex Detailing London",
            category="detailing",
            operator_approved=False
        )
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "approval_required")
        self.assertIn("operator authorization", res["error"])

    def test_build_success_scaffolds_motion_flagship(self):
        dest_dir = os.path.join(self.temp_dir.name, "apex-demo")
        res = MotionSiteBuilder.build(
            slug="apex-detailing-london",
            business_name="Apex Detailing London",
            category="automotive_detailing",
            city="Mayfair, London",
            phone="+44 20 7946 0888",
            whatsapp="+44 7700 900123",
            operator_approved=True,
            destination_override=dest_dir
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "success")

        # Verify files created
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "index.html")))
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "src", "style.css")))
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "src", "main.js")))
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "signature.json")))
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "SOURCES.json")))

        # Verify HTML replacements
        with open(os.path.join(dest_dir, "index.html"), "r") as f:
            html = f.read()
        self.assertIn("Apex Detailing London", html)
        self.assertIn("Mayfair, London", html)
        self.assertIn("+44 20 7946 0888", html)
        self.assertIn("https://wa.me/447700900123", html)

        # Verify signature configuration
        with open(os.path.join(dest_dir, "signature.json"), "r") as f:
            sig = json.load(f)
        self.assertEqual(sig["interaction_type"], "before_after_slider")
        self.assertTrue(sig["is_placeholder"])

        # Verify SOURCES.json ledger
        with open(os.path.join(dest_dir, "SOURCES.json"), "r") as f:
            ledger = json.load(f)
        self.assertEqual(ledger["business_name"]["value"], "Apex Detailing London")
        self.assertIn("Concept design", ledger["disclosure"])


class TestMotionQA(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_motion_qa_passes_on_valid_site(self):
        demo_dir = os.path.join(self.temp_dir.name, "valid-motion-site")
        build_res = MotionSiteBuilder.build(
            slug="valid-motion-site",
            business_name="Atelier Aesthetics",
            category="dental_medical",
            operator_approved=True,
            destination_override=demo_dir
        )
        self.assertTrue(build_res["ok"])

        qa_res = await MotionQA.run_qa(demo_dir=demo_dir)
        self.assertTrue(qa_res["ok"])
        self.assertEqual(qa_res["status"], "success")
        self.assertTrue(qa_res["qa_report"]["reduced_motion_supported"])
        self.assertEqual(len(qa_res["violations"]), 0)

        # Check real measurement reporting
        perf = qa_res["perf_report"]
        self.assertGreater(perf["html_bytes"], 0)
        self.assertGreater(perf["css_bytes"], 0)
        self.assertGreater(perf["js_bytes"], 0)
        self.assertEqual(perf["measurement_mode"], "actual_file_bytes")

        # Verify report files on disk
        self.assertTrue(os.path.exists(os.path.join(demo_dir, "reports", "qa.json")))
        self.assertTrue(os.path.exists(os.path.join(demo_dir, "reports", "perf.json")))

    async def test_motion_qa_fails_on_unverified_claim(self):
        demo_dir = os.path.join(self.temp_dir.name, "fake-claims-site")
        MotionSiteBuilder.build(
            slug="fake-claims-site",
            business_name="Unverified Shop",
            operator_approved=True,
            destination_override=demo_dir
        )

        # Inject fake 'Verified' stamp into HTML
        html_path = os.path.join(demo_dir, "index.html")
        with open(html_path, "r") as f:
            content = f.read()
        content = content.replace("</h1>", "</h1><span class='badge'>Verified</span>")
        with open(html_path, "w") as f:
            f.write(content)

        qa_res = await MotionQA.run_qa(demo_dir=demo_dir)
        self.assertFalse(qa_res["ok"])
        self.assertEqual(qa_res["status"], "failed")
        self.assertTrue(any("Verified" in v for v in qa_res["violations"]))

    async def test_motion_qa_fails_on_unledgered_asset(self):
        demo_dir = os.path.join(self.temp_dir.name, "unledgered-asset-site")
        MotionSiteBuilder.build(
            slug="unledgered-asset-site",
            business_name="Unledgered Shop",
            operator_approved=True,
            destination_override=demo_dir
        )

        # Add unledgered image
        assets_dir = os.path.join(demo_dir, "assets")
        os.makedirs(assets_dir, exist_ok=True)
        with open(os.path.join(assets_dir, "untracked.png"), "wb") as f:
            f.write(b"fake image bytes")

        qa_res = await MotionQA.run_qa(demo_dir=demo_dir)
        self.assertFalse(qa_res["ok"])
        self.assertEqual(qa_res["status"], "failed")
        self.assertTrue(any("unledgered asset" in v.lower() for v in qa_res["violations"]))
