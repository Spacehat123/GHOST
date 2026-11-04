import os
import unittest
from pathlib import Path

class TestGuiHudEmblem(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.gui_dir = self.repo_root / "gui"
        self.index_html = self.gui_dir / "index.html"
        self.styles_css = self.gui_dir / "static" / "css" / "styles.css"
        self.orb_js = self.gui_dir / "static" / "js" / "orb.js"
        self.app_js = self.gui_dir / "static" / "js" / "app.js"

    def test_gui_files_exist(self):
        self.assertTrue(self.index_html.is_file(), "index.html missing")
        self.assertTrue(self.styles_css.is_file(), "styles.css missing")
        self.assertTrue(self.orb_js.is_file(), "orb.js missing")
        self.assertTrue(self.app_js.is_file(), "app.js missing")

    def test_index_html_hud_elements(self):
        content = self.index_html.read_text(encoding="utf-8")
        # Pill buttons matching reference video
        self.assertIn('id="btn-hud-history"', content)
        self.assertIn('id="btn-hud-logs"', content)
        self.assertIn('id="btn-hud-dev"', content)
        self.assertIn('id="btn-hud-clear"', content)
        # Bottom telemetry bar
        self.assertIn('class="bottom-hud-bar"', content)
        self.assertIn('class="hud-telemetry-line"', content)
        self.assertIn('class="telemetry-status-tag"', content)
        self.assertIn('id="telemetry-audio-stat"', content)
        self.assertIn('id="telemetry-state-stat"', content)
        # Canvas
        self.assertIn('id="orb-canvas"', content)

    def test_styles_css_tactical_hud(self):
        content = self.styles_css.read_text(encoding="utf-8")
        self.assertIn(".hud-pill-group", content)
        self.assertIn(".hud-pill-btn", content)
        self.assertIn(".bottom-hud-bar", content)
        self.assertIn(".hud-telemetry-line", content)
        self.assertIn(".telemetry-status-tag", content)
        self.assertIn("body.clean-focus", content)

    def test_orb_js_engine_capabilities(self):
        content = self.orb_js.read_text(encoding="utf-8")
        self.assertIn("class GhostOrb", content)
        self.assertIn("setState(", content)
        self.assertIn("setAmplitude(", content)
        self.assertIn("setAudioBins(", content)
        self.assertIn("setEmblemText(", content)
        self.assertIn("_drawRoseGlyph", content)
        self.assertIn("_drawGhostGlyph", content)
        # Stencil and reticle rendering
        self.assertIn("drawStencilText", content)
        self.assertIn("particles", content)
        self.assertIn("#ff0055", content)  # Hot neon crimson
        # Intro timeline, 3D math & audio capabilities
        self.assertIn("startIntro(", content)
        self.assertIn("skipIntro(", content)
        self.assertIn("seek(", content)
        self.assertIn("introPose(", content)
        self.assertIn("rotXYZ(", content)
        self.assertIn("project(", content)
        self.assertIn("strokeArc3D(", content)
        self.assertIn("class GhostIntroAudio", content)

    def test_app_js_hud_wiring(self):
        content = self.app_js.read_text(encoding="utf-8")
        self.assertIn("btnHudClear", content)
        self.assertIn("btnHudHistory", content)
        self.assertIn("btnHudLogs", content)
        self.assertIn("btnHudDev", content)
        self.assertIn("clean-focus", content)
        self.assertIn("telemetryStateStat", content)
        self.assertIn("telemetryStatusTag", content)
        self.assertIn("window.ghostOrb = orb", content)
        self.assertIn("orb.startIntro", content)
        self.assertIn("orb.onIntroDone", content)

if __name__ == "__main__":
    unittest.main()
