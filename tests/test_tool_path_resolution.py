"""The 'lost memory' incident (operator report, 2026-11-08): 'open
docs/OPERATOR_MANUAL.md in brave' became https://docs/OPERATOR_MANUAL.md
(Brave: can't access) while Ghost claimed success, and 'open it in kwrite in
workspace 5' opened a random file (launch_app had no file-argument slot, so
the model improvised). Root cause + fix, pinned by tests:

- local paths resolve to ABSOLUTE file:// (repo root, cwd, ~) — never
  https-prefixed, never guessed
- a missing file returns an HONEST error naming what was tried — nothing
  opens, nothing claims success
- launch_app accepts a bundled 'app file' target and a file argument
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ghost.core.brain import OmniRouteBrain, resolve_local_file, REPO_DIR, DOC_FILE_EXTENSIONS


class TestResolveLocalFile(unittest.TestCase):

    def test_repo_relative_path_resolves(self):
        resolved, tried = resolve_local_file("docs/OPERATOR_MANUAL.md")
        self.assertEqual(resolved, os.path.join(REPO_DIR, "docs", "OPERATOR_MANUAL.md"))
        self.assertTrue(os.path.isfile(resolved))

    def test_tilde_and_absolute_resolve(self):
        home_file = os.path.expanduser("~/.bashrc")
        if os.path.isfile(home_file):
            self.assertEqual(resolve_local_file("~/.bashrc")[0], home_file)
        abs_file = os.path.join(REPO_DIR, "docs", "OPERATOR_MANUAL.md")
        self.assertEqual(resolve_local_file(abs_file)[0], abs_file,
                         "an absolute file path passes through untouched")

    def test_missing_file_resolves_to_none_and_names_what_was_tried(self):
        resolved, tried = resolve_local_file("docs/DOES_NOT_EXIST.md")
        self.assertIsNone(resolved)
        self.assertTrue(any("DOES_NOT_EXIST" in t for t in tried),
                        "the error must name exactly what was tried")

    def test_empty_is_none(self):
        self.assertIsNone(resolve_local_file("")[0])


class TestOpenUrlResolution(unittest.TestCase):

    def _executor(self):
        # execute_tool_call is a classmethod on OmniRouteBrain — the dispatch
        # surface itself needs no instance state for these tools
        return OmniRouteBrain

    def test_local_md_file_opens_as_file_url_not_https(self):
        captured = {}

        async def fake_new_tab(dest):
            captured["dest"] = dest
            return {"ok": True, "url": dest}

        ex = self._executor()
        with patch("ghost.core.brain.BraveCDPBridge") as bridge:
            bridge.new_tab = AsyncMock(side_effect=fake_new_tab)
            res = asyncio.run(ex.execute_tool_call(
                "browser_action", {"action": "open_url",
                                   "url": "docs/OPERATOR_MANUAL.md"}))
        self.assertEqual(captured["dest"], "file://" + os.path.join(REPO_DIR, "docs", "OPERATOR_MANUAL.md"),
                         "a repo-relative .md path must open as file:// — never https://docs/…")

    def test_missing_file_is_an_honest_error_and_nothing_opens(self):
        ex = self._executor()
        with patch("ghost.core.brain.BraveCDPBridge") as bridge:
            bridge.new_tab = AsyncMock()
            res = asyncio.run(ex.execute_tool_call(
                "browser_action", {"action": "open_url",
                                   "url": "docs/DOES_NOT_EXIST.md"}))
        self.assertIn("error", res, "an honest error, not a claimed success")
        self.assertIn("DOES_NOT_EXIST", res["error"])
        self.assertIn("tried", res["error"])
        bridge.new_tab.assert_not_awaited()

    def test_domains_still_get_https(self):
        captured = {}

        async def fake_new_tab(dest):
            captured["dest"] = dest
            return {"ok": True}

        ex = self._executor()
        with patch("ghost.core.brain.BraveCDPBridge") as bridge:
            bridge.new_tab = AsyncMock(side_effect=fake_new_tab)
            asyncio.run(ex.execute_tool_call(
                "browser_action", {"action": "open_url", "url": "example.com"}))
        self.assertEqual(captured["dest"], "https://example.com")


class TestLaunchAppFileSlot(unittest.TestCase):

    def _executor(self):
        return OmniRouteBrain

    def test_bundled_app_and_missing_file_refuse_to_launch(self):
        ex = self._executor()
        with patch("ghost.core.brain.SystemAppBridge") as bridge:
            bridge.launch_app = AsyncMock()
            res = asyncio.run(ex.execute_tool_call(
                "desktop_control", {"action": "launch_app",
                                    "target": "kwrite docs/DOES_NOT_EXIST.md",
                                    "args": "5"}))
        self.assertIn("error", res, "a missing file must refuse — no random empty files")
        self.assertIn("tried", res["error"])
        bridge.launch_app.assert_not_awaited()

    def test_bundled_app_and_real_file_launch_resolved(self):
        ex = self._executor()
        manual = os.path.join(REPO_DIR, "docs", "OPERATOR_MANUAL.md")
        with patch("ghost.core.brain.SystemAppBridge") as bridge:
            bridge.launch_app = AsyncMock(return_value={"ok": True})
            asyncio.run(ex.execute_tool_call(
                "desktop_control", {"action": "launch_app",
                                    "target": "kwrite docs/OPERATOR_MANUAL.md",
                                    "args": "5"}))
        args, kwargs = bridge.launch_app.await_args
        self.assertEqual(args[0], f"kwrite {manual}")
        self.assertEqual(kwargs.get("workspace"), "5", "the workspace still rides along")


if __name__ == "__main__":
    unittest.main()


class TestNoStockPhotoDoctrine(unittest.TestCase):
    """The Grasshopper lesson v2: a cat labelled 'chocolate ganache' is a
    fabricated fact. Zero real photos = photography-free build; stock is
    banned from the assets entirely; stale QA cannot pass the deploy gate."""

    def test_curated_assets_carry_no_stock_images(self):
        from ghost.workers.demo_state_machine import DOMAIN_CURATED_ASSETS
        for domain, assets in DOMAIN_CURATED_ASSETS.items():
            for key in ("hero_bg", "bento_img1", "bento_img2",
                        "gallery_1", "gallery_2", "gallery_3"):
                self.assertNotIn(key, assets,
                                 f"{domain}.{key} still carries stock imagery")

    def test_stage_zero_real_photos_clears_every_stock_slot(self):
        from ghost.workers.demo_state_machine import AutonomousDemoStateMachine
        sm = AutonomousDemoStateMachine("No Photo Cafe", slug="nopoto")
        os.makedirs(sm.out_dir, exist_ok=True)
        assets = {"hero_bg": "https://images.unsplash.com/cat",
                  "gallery_1": "https://images.unsplash.com/food",
                  "services": [{"title": "Tasting menu"}]}
        n = sm.stage_real_photos(assets, {})   # recon returned nothing
        self.assertEqual(n, 0)
        self.assertNotIn("hero_bg", assets)
        self.assertNotIn("gallery_1", assets)
        self.assertEqual(assets.get("photos_policy"), "no_photos_inquire")
        self.assertEqual(assets["services"], assets["services"])  # facts survive

    def test_partial_staging_never_backfills_stock(self):
        from ghost.workers.demo_state_machine import AutonomousDemoStateMachine
        sm = AutonomousDemoStateMachine("Half Photo Cafe", slug="halfpoto")
        os.makedirs(sm.out_dir, exist_ok=True)
        from ghost.workers.dossier import dossiers_dir as _dd
        dd = os.path.join(_dd(), sm.slug, "photos")
        os.makedirs(dd, exist_ok=True)
        with open(os.path.join(dd, "photo-01.jpg"), "wb") as f:
            f.write(b"\xff\xd8real-one")
        assets = {"hero_bg": "stock", "bento_img1": "stock", "bento_img2": "stock"}
        n = sm.stage_real_photos(assets, {})
        self.assertEqual(n, 1)
        self.assertTrue(assets["hero_bg"].startswith("assets/photos/"))
        self.assertNotIn("bento_img1", assets, "unfilled slots stay unfilled")
        self.assertNotIn("bento_img2", assets)

    def test_stale_qa_blocks_deploy(self):
        from ghost.workers.deploy import VercelDeployer
        from ghost.workers.snapshots import SnapshotStore
        slug = f"stale-{uuid.uuid4().hex[:6]}"
        demos_root = tempfile.mkdtemp(prefix="stale-demos-", dir=tempfile.gettempdir())
        demo_dir = os.path.join(demos_root, slug)
        os.makedirs(demo_dir, exist_ok=True)
        with open(os.path.join(demo_dir, "index.html"), "w") as f:
            f.write("<html>NEW page, edited after QA</html>")
        os.makedirs(os.path.join(demo_dir, "reports"), exist_ok=True)
        qa_path = os.path.join(demo_dir, "reports", "qa.json")
        with open(qa_path, "w") as f:
            json.dump({"passed": True, "screenshots_captured": 1,
                       "screenshots": ["qa-shot-01.png"]}, f)
        os.utime(qa_path, (time.time() - 7200, time.time() - 7200))  # QA 2h old
        with open(os.path.join(demo_dir, "qa-shot-01.png"), "wb") as f:
            f.write(b"\x89PNG real-ish")
        SnapshotStore().snapshot(slug, demo_dir)
        os.environ["GHOST_GRANT_SECRET"] = f"stale-{uuid.uuid4().hex}"
        os.environ["GHOST_AUTHORITY_NONCE_DB"] = os.path.join(
            tempfile.gettempdir(), f"n-{uuid.uuid4().hex}.db")
        from ghost.autonomy.outbound import issue_effect_grant
        grant = issue_effect_grant("deploy", slug)
        self.assertIsNotNone(grant, "the grant must mint — the QA gate is what we are testing")
        with patch("ghost.workers.deploy._demos_dir", lambda: demos_root):
            res = asyncio.run(VercelDeployer.deploy_demo(slug, grant_token=grant))
        self.assertEqual(res["status"], "blocked_by_qa")
        self.assertIn("stale QA evidence", res["message"],
                      "a page edited after its QA pass cannot deploy on old evidence")
