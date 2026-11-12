"""Unit tests for WorkspaceManager and native modules."""

import os
import tempfile
import unittest

from ghost.core.workspace import WorkspaceManager


class TestWorkspaceManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mgr = WorkspaceManager()
        self.mgr.close_all()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_module_resolution(self):
        self.assertEqual(self.mgr.resolve_module_for_path("report.pdf"), "pdf")
        self.assertEqual(self.mgr.resolve_module_for_path("notes.md"), "editor")
        self.assertEqual(self.mgr.resolve_module_for_path("script.py"), "code")
        self.assertEqual(self.mgr.resolve_module_for_path("photo.jpg"), "image")
        self.assertEqual(self.mgr.resolve_module_for_path("song.wav"), "audio")
        self.assertEqual(self.mgr.resolve_module_for_path("video.mp4"), "video")
        self.assertEqual(self.mgr.resolve_module_for_path("data.csv"), "charts")

    def test_open_and_close_tab(self):
        test_file = os.path.join(self.temp_dir.name, "sample.md")
        with open(test_file, "w") as f:
            f.write("# Hello World\nTesting workspace native viewer.")

        tab = self.mgr.open_module("editor", file_path=test_file)
        self.assertEqual(self.mgr.active_module, "editor")
        self.assertEqual(self.mgr.active_file, test_file)
        self.assertIn("Hello World", self.mgr.active_context_summary)

        state = self.mgr.get_state()
        self.assertTrue(state["is_open"])
        self.assertEqual(state["total_open"], 1)

        # Close tab
        closed = self.mgr.close_tab(tab.id)
        self.assertTrue(closed)
        self.assertEqual(self.mgr.active_module, "none")
        self.assertFalse(self.mgr.get_state()["is_open"])

    def test_file_read_and_save(self):
        test_file = os.path.join(self.temp_dir.name, "doc.txt")
        save_res = self.mgr.save_file(test_file, "Line 1\nLine 2")
        self.assertTrue(save_res["ok"])

        read_res = self.mgr.read_file(test_file)
        self.assertTrue(read_res["ok"])
        self.assertEqual(read_res["content"], "Line 1\nLine 2")
        self.assertFalse(read_res["is_binary"])

    def test_destructive_delete_requires_confirmation(self):
        test_file = os.path.join(self.temp_dir.name, "delete_me.txt")
        self.mgr.save_file(test_file, "Delete test")

        # Without confirmation: must fail safely!
        del_attempt = self.mgr.delete_file(test_file, confirmed=False)
        self.assertFalse(del_attempt["ok"])
        self.assertEqual(del_attempt["status"], "confirmation_required")
        self.assertTrue(os.path.exists(test_file))

        # With explicit confirmation: succeeds
        del_confirmed = self.mgr.delete_file(test_file, confirmed=True)
        self.assertTrue(del_confirmed["ok"])
        self.assertFalse(os.path.exists(test_file))

    def test_list_directory(self):
        f1 = os.path.join(self.temp_dir.name, "file1.txt")
        f2 = os.path.join(self.temp_dir.name, "report.pdf")
        os.makedirs(os.path.join(self.temp_dir.name, "subfolder"), exist_ok=True)
        self.mgr.save_file(f1, "1")
        self.mgr.save_file(f2, "2")

        listing = self.mgr.list_directory(self.temp_dir.name)
        self.assertTrue(listing["ok"])
        self.assertEqual(listing["count"], 3)
        names = [e["name"] for e in listing["entries"]]
        self.assertIn("subfolder", names)
        self.assertIn("file1.txt", names)
        self.assertIn("report.pdf", names)

    def test_terminal_exec(self):
        res = self.mgr.execute_terminal("echo 'GHOST_WORKSPACE_ONLINE'")
        self.assertTrue(res["ok"])
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("GHOST_WORKSPACE_ONLINE", res["stdout"])
