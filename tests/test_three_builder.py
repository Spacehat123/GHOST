"""Unit and Integration Tests for Ghost 3D Site Builder & QA."""
import os
import json
import tempfile
import unittest

from ghost.workers.three_site_builder import ThreeSiteBuilder
from ghost.workers.three_qa import ThreeQA


class TestThreeSiteBuilder(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        # Create a dummy GLB
        self.glb_path = os.path.join(self.temp_dir.name, "sample.glb")
        with open(self.glb_path, "wb") as f:
            # Minimal GLB magic header
            f.write(b"glTF\x02\x00\x00\x00\x18\x00\x00\x00\x00\x00\x00\x00JSON{}")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_build_requires_operator_approval(self):
        res = ThreeSiteBuilder.build(
            slug="topaz-detailing",
            subject="Porsche 911 GT3 RS",
            glb_path=self.glb_path,
            operator_approved=False
        )
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "approval_required")
        self.assertIn("explicit operator authorization", res["error"])

    def test_build_fails_on_missing_glb(self):
        res = ThreeSiteBuilder.build(
            slug="topaz-detailing",
            subject="Porsche 911 GT3 RS",
            glb_path="/path/that/does/not/exist/missing.glb",
            operator_approved=True
        )
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "failed")
        self.assertIn("not found on disk", res["error"])

    def test_build_success_scaffolds_demo(self):
        dest_dir = os.path.join(self.temp_dir.name, "output_demo")
        res = ThreeSiteBuilder.build(
            slug="topaz-detailing",
            subject="Porsche 911 GT3 RS",
            glb_path=self.glb_path,
            business_name="Topaz Detailing Studio",
            operator_approved=True,
            destination_override=dest_dir
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "success")

        # Verify scaffolded files
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "index.html")))
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "stages.json")))
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "assets", "model.glb")))
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "SOURCES.json")))

        # Verify business name injected
        with open(os.path.join(dest_dir, "index.html"), "r") as f:
            html = f.read()
        self.assertIn("Topaz Detailing Studio", html)

        # Verify stages tailored to vehicle
        with open(os.path.join(dest_dir, "stages.json"), "r") as f:
            stages = json.load(f)
        self.assertIsInstance(stages, list)
        self.assertEqual(len(stages), 5)
        self.assertIn("Hero", stages[0]["name"])

        # Verify SOURCES.json ledger
        with open(os.path.join(dest_dir, "SOURCES.json"), "r") as f:
            ledger = json.load(f)
        self.assertEqual(ledger["business_name"]["value"], "Topaz Detailing Studio")
        self.assertIn("Concept design", ledger["disclosure"])


class TestThreeQA(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_three_qa_on_valid_site(self):
        # Scaffold a valid site first
        fake_glb = os.path.join(self.temp_dir.name, "car.glb")
        with open(fake_glb, "wb") as f:
            f.write(b"glTF\x02\x00\x00\x00\x18\x00\x00\x00\x00\x00\x00\x00JSON{}")

        demo_dir = os.path.join(self.temp_dir.name, "test_site")
        ThreeSiteBuilder.build(
            slug="test-site",
            subject="Porsche Car",
            glb_path=fake_glb,
            operator_approved=True,
            destination_override=demo_dir
        )

        qa_res = await ThreeQA.run_qa(demo_dir=demo_dir)
        self.assertTrue(qa_res["ok"])
        self.assertEqual(qa_res["status"], "success")

        # Check reports written
        self.assertTrue(os.path.exists(os.path.join(demo_dir, "reports", "qa.json")))
        self.assertTrue(os.path.exists(os.path.join(demo_dir, "reports", "perf.json")))

        with open(os.path.join(demo_dir, "reports", "qa.json"), "r") as f:
            report = json.load(f)
        self.assertTrue(report["passed"])
        self.assertEqual(report["stages_count"], 5)

    async def test_three_qa_missing_dir(self):
        qa_res = await ThreeQA.run_qa(demo_dir="/non/existent/path")
        self.assertFalse(qa_res["ok"])
        self.assertEqual(qa_res["status"], "failed")
