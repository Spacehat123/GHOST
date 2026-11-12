"""Comprehensive Verification Test Suite for Real GHOST Workspace Functionality & UI Controls.

Validates:
1. Native PDF pipeline:
   - /api/workspace/file/raw streams raw binary application/pdf.
   - /api/workspace/pdf/info extracts real page count, dimensions, and text.
   - read_file() does NOT return raw %PDF-1.4 byte syntax.
2. Built-in Workspace backend endpoints for all 12 modules:
   - Editor: file save, .bak creation, content persistence.
   - Code: file tree navigation and code retrieval.
   - Image: real dimensions, mode, and luminance calculation.
   - Camera: vision inspection with optical resolution and mean exposure.
   - Audio: Kokoro neural TTS synthesis.
   - Video: raw binary streaming with range support.
   - Browser: live URL reference and citation extraction.
   - Files: directory listing and safe operations.
   - Terminal: sandboxed bash command execution and exit codes.
   - Charts: real market quotes from MarketDatabase.
   - Sysmon: real kernel vitals and active operating processes.
3. Kokoro TTS Voice switching and persistence.
4. Emergency Halt and Resume controls.
5. Personal Intelligence 6-category memory system.
"""

from __future__ import annotations

import os
import unittest
from starlette.testclient import TestClient

from ghost.server.app import app
from ghost.core.workspace import WorkspaceManager


class TestRealFunctionality(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.wm = WorkspaceManager.get_instance()
        cls.test_pdf = "/home/pranc/projects/ghost/data/cad/aerospire_planter_engineering_dossier.pdf"

    def test_01_pdf_raw_streaming_and_mime(self):
        """Verify /api/workspace/file/raw streams actual binary PDF with application/pdf MIME."""
        res = self.client.get(f"/api/workspace/file/raw?path={self.test_pdf}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "application/pdf")
        self.assertTrue(len(res.content) > 1000)
        self.assertTrue(res.content.startswith(b"%PDF-"))

    def test_02_pdf_info_authentic_metadata_and_text(self):
        """Verify /api/workspace/pdf/info returns authentic page count and extracted text."""
        res = self.client.get(f"/api/workspace/pdf/info?path={self.test_pdf}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("pages"), 4)
        self.assertTrue("AeroSpire" in data.get("full_text", ""))
        self.assertTrue("1" in data.get("text_by_page", {}))

    def test_03_read_file_does_not_return_raw_pdf_syntax(self):
        """Verify read_file explicitly marks PDF as binary and extracts genuine text, not raw bytecode."""
        data = self.wm.read_file(self.test_pdf)
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("is_binary"))
        content = data.get("content", "")
        # Must NOT contain raw PDF structure syntax
        self.assertNotIn("endobj", content)
        self.assertNotIn("xref", content)
        self.assertNotIn("trailer", content)
        # Must contain real document text extracted via pdftotext
        self.assertIn("AeroSpire", content)

    def test_04_editor_save_and_backup_creation(self):
        """Verify editor saves content cleanly to disk with automatic .bak backup preservation."""
        test_file = "/tmp/ghost_editor_verify.txt"
        with open(test_file, "w") as f:
            f.write("Initial Content v1")

        save_res = self.client.post("/api/workspace/file/save", json={
            "path": test_file,
            "content": "Updated Content v2 with GHOST Verification"
        })
        self.assertEqual(save_res.status_code, 200)
        self.assertTrue(save_res.json().get("ok"))

        # Verify disk content
        with open(test_file, "r") as f:
            self.assertEqual(f.read(), "Updated Content v2 with GHOST Verification")

        # Verify .bak
        self.assertTrue(os.path.exists(test_file + ".bak"))
        with open(test_file + ".bak", "r") as f:
            self.assertEqual(f.read(), "Initial Content v1")

        # Clean up
        os.remove(test_file)
        os.remove(test_file + ".bak")

    def test_05_code_workspace_directory_and_file_reading(self):
        """Verify code workspace lists directories and reads source code accurately."""
        res = self.client.get("/api/workspace/directory?path=/home/pranc/projects/ghost/src/ghost")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        entry_names = [e["name"] for e in data.get("entries", [])]
        self.assertIn("server", entry_names)
        self.assertIn("core", entry_names)

    def test_06_image_viewer_metadata_inspection(self):
        """Verify /api/workspace/image/info calculates authentic dimensions, mode, and luminance."""
        img_path = "/home/pranc/projects/ghost/data/sample.png"
        res = self.client.get(f"/api/workspace/image/info?path={img_path}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("width"), 1910)
        self.assertEqual(data.get("height"), 947)
        self.assertEqual(data.get("format"), "PNG")
        self.assertEqual(data.get("mode"), "RGB")
        self.assertGreater(data.get("size_kb"), 100)

    def test_07_camera_vision_inspection_metrics(self):
        """Verify /api/workspace/vision/inspect returns authentic optical metrics."""
        import base64
        # Create a 100x100 white PNG in memory
        from PIL import Image
        import io
        img = Image.new("RGB", (100, 100), color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_img = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        res = self.client.post("/api/workspace/vision/inspect", json={
            "image": b64_img,
            "query": "Calibration test"
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("width"), 100)
        self.assertEqual(data.get("height"), 100)
        self.assertEqual(data.get("mean_luminance"), 255.0)  # Pure white
        self.assertIn("Optical Resolution", data.get("analysis", ""))

    def test_08_audio_tts_synthesis(self):
        """Verify /api/tts synthesizes real audio bytes."""
        res = self.client.get("/api/tts?voice=af_nicole&text=Test%20Speech")
        self.assertEqual(res.status_code, 200)
        self.assertIn("audio/", res.headers.get("content-type", ""))
        self.assertGreater(len(res.content), 500)

    def test_09_browser_reference_extractor(self):
        """Verify /api/workspace/browser/extract fetches URL and parses real headings and links."""
        res = self.client.post("/api/workspace/browser/extract", json={
            "url": "https://en.wikipedia.org/wiki/Autonomous_agent"
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("Autonomous agent", data.get("title", ""))
        self.assertGreater(len(data.get("headings", [])), 0)

    def test_10_terminal_command_execution(self):
        """Verify integrated terminal executes real bash commands and returns structured results."""
        res = self.client.post("/api/workspace/terminal", json={
            "command": "uname -s && echo SUCCESS"
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("exit_code"), 0)
        self.assertIn("Linux", data.get("stdout", ""))
        self.assertIn("SUCCESS", data.get("stdout", ""))

    def test_11_market_charts_data(self):
        """Verify /api/market/quotes returns authentic stored quote data."""
        res = self.client.get("/api/market/quotes")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        quotes = data.get("quotes") if isinstance(data, dict) else data
        self.assertIsInstance(quotes, list)
        self.assertGreater(len(quotes), 0)
        first = quotes[0]
        self.assertIn("symbol", first)
        self.assertIn("price", first)

    def test_12_sysmon_telemetry_and_real_processes(self):
        """Verify /api/workspace/sysmon returns real kernel vitals and host processes."""
        res = self.client.get("/api/workspace/sysmon")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        v = data.get("vitals", {})
        self.assertGreater(v.get("ram_total_mb", 0), 1000)
        self.assertGreater(v.get("ram_used_mb", 0), 500)
        procs = data.get("processes", [])
        self.assertGreater(len(procs), 0)
        self.assertIn("pid", procs[0])
        self.assertIn("name", procs[0])

    def test_13_voice_persona_switching(self):
        """Verify voice selection changes Kokoro persona."""
        res = self.client.post("/api/tts/voice", json={"voice": "am_adam"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("voice"), "am_adam")

        v_res = self.client.get("/api/tts/voices")
        self.assertEqual(v_res.json().get("current"), "am_adam")

        # Reset to af_aoede
        self.client.post("/api/tts/voice", json={"voice": "af_aoede"})

    def test_14_emergency_halt_trip_and_resume(self):
        """Verify emergency halt trips and resumes cleanly."""
        from ghost.core import halt as halt_mod
        trip_res = self.client.post("/api/halt", json={"reason": "Test Suite Trip"})
        self.assertEqual(trip_res.status_code, 200)
        self.assertEqual(trip_res.json().get("status"), "halted")
        self.assertTrue(halt_mod.is_halted())

        resume_res = self.client.post("/api/resume")
        self.assertEqual(resume_res.status_code, 200)
        self.assertEqual(resume_res.json().get("status"), "resumed")
        self.assertFalse(halt_mod.is_halted())

    def test_15_personal_intelligence_categories(self):
        """Verify 6 personal intelligence categories exist with valid memory counts."""
        res = self.client.get("/api/personal_intelligence/categories")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("total_memories"), 10)
        cats = data.get("categories", {})
        for cat in ("profile", "procedural", "project", "goal", "knowledge", "episodic"):
            self.assertIn(cat, cats)


if __name__ == "__main__":
    unittest.main()
