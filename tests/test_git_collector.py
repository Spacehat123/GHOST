"""Unit tests for GitCollector (ARCHITECTURE_TARGET §Layer 1 & §2 Development Intelligence)."""

import asyncio
import os
import subprocess
import tempfile
import unittest

from ghost.collectors.git_collector import GitCollector
from ghost.core.events import Event, EventBus


class TestGitCollector(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_dir = os.path.join(self.tmpdir.name, "test_repo")
        os.makedirs(self.repo_dir, exist_ok=True)

        # Initialize mock git repo
        subprocess.run(["git", "init"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, capture_output=True, check=True)

        # Initial commit
        test_file = os.path.join(self.repo_dir, "hello.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("initial content\n")
        subprocess.run(["git", "add", "hello.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_dir, capture_output=True, check=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_sample_repo_clean_and_dirty(self):
        """GitCollector accurately inspects branch, commit, and dirty files."""
        collector = GitCollector(repo_paths=[self.repo_dir])
        state1 = collector.sample_repo(self.repo_dir)

        self.assertIsNotNone(state1)
        self.assertFalse(state1.is_dirty)
        self.assertEqual(len(state1.modified_files), 0)
        self.assertEqual(len(state1.untracked_files), 0)

        # Modify file and create untracked file
        with open(os.path.join(self.repo_dir, "hello.txt"), "a", encoding="utf-8") as f:
            f.write("new modification\n")
        with open(os.path.join(self.repo_dir, "new_file.py"), "w", encoding="utf-8") as f:
            f.write("print('hi')\n")

        state2 = collector.sample_repo(self.repo_dir)
        self.assertIsNotNone(state2)
        self.assertTrue(state2.is_dirty)
        self.assertIn("hello.txt", state2.modified_files)
        self.assertIn("new_file.py", state2.untracked_files)

    def test_sample_and_publish_event(self):
        """GitCollector emits dev.git.change on EventBus when repo state transitions."""
        async def _run():
            bus = EventBus(db_path=os.path.join(self.tmpdir.name, "events.db"))
            collector = GitCollector(repo_paths=[self.repo_dir], event_bus=bus)

            events_received = []
            bus.subscribe("dev.git.change", lambda ev: events_received.append(ev))

            # Initial sample emits first snapshot
            evs = await collector.sample_and_publish()
            self.assertEqual(len(evs), 1)
            self.assertTrue(evs[0].payload["initial_sample"])

            # No changes -> no new events emitted
            evs_unchanged = await collector.sample_and_publish()
            self.assertEqual(len(evs_unchanged), 0)

            # Introduce change
            with open(os.path.join(self.repo_dir, "change.txt"), "w", encoding="utf-8") as f:
                f.write("change\n")

            evs_changed = await collector.sample_and_publish()
            self.assertEqual(len(evs_changed), 1)
            self.assertFalse(evs_changed[0].payload["initial_sample"])
            self.assertTrue(evs_changed[0].payload["is_dirty"])
            self.assertIn("change.txt", evs_changed[0].payload["untracked_files"])

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
