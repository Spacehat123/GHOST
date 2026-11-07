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
import os
import sys
import unittest
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
