"""Unit and Integration Tests for 3-Layer Memory System in Ghost.
Enforces non-negotiable architectural rule:
Never silently delete or overwrite contradictory memories. Preserve their sources, flag conflicts,
and resolve them through explicit evidence or user clarification. Enforce the 250-token limit on the compiled core-memory snapshot.
"""
import unittest
import tempfile
import shutil
import os
import time
from ghost.core.memory import CoreMemoryStore, EpisodicMemoryStore, WorkingMemory

class TestMemorySystem(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "directives.json")
        self.db_path = os.path.join(self.temp_dir, "memory.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_lossless_contradiction_handling(self):
        """Contradictory memories must never be deleted or overwritten; both are preserved with conflict flags."""
        store = CoreMemoryStore(config_path=self.config_path)

        res1 = store.add_memory(
            content="Primary browser is Brave",
            mem_type="fact",
            source="user_directive",
            importance=0.9
        )
        id1 = res1["item"]["id"]
        self.assertFalse(res1.get("conflict_detected"))

        # Add opposing contradiction
        res2 = store.add_memory(
            content="Primary browser is Firefox",
            mem_type="fact",
            source="chat_observation",
            importance=0.8
        )
        id2 = res2["item"]["id"]

        # Both must have conflict detected
        self.assertTrue(res2.get("conflict_detected"))
        self.assertEqual(res2.get("conflict_with_id"), id1)

        # Reload store from disk to verify persistence
        store_reloaded = CoreMemoryStore(config_path=self.config_path)
        data = store_reloaded.load()
        memories = store_reloaded._extract_memories(data)

        # Neither memory was deleted
        mem1 = next((m for m in memories if m.id == id1), None)
        mem2 = next((m for m in memories if m.id == id2), None)
        self.assertIsNotNone(mem1)
        self.assertIsNotNone(mem2)
        self.assertTrue(mem1.conflict_detected)
        self.assertTrue(mem2.conflict_detected)
        self.assertEqual(mem1.conflict_with_id, id2)

        # Conflicted memories MUST be excluded from the active prompt snapshot to prevent model confusion
        snapshot = store_reloaded.get_snapshot()
        self.assertNotIn("Primary browser is Brave", snapshot)
        self.assertNotIn("Primary browser is Firefox", snapshot)

    def test_explicit_evidence_conflict_resolution(self):
        """Conflicts are resolved explicitly through evidence without silent deletion of the loser."""
        store = CoreMemoryStore(config_path=self.config_path)
        res1 = store.add_memory("Primary browser is Brave", mem_type="fact", importance=0.9)
        res2 = store.add_memory("Primary browser is Firefox", mem_type="fact", importance=0.8)
        id1 = res1["item"]["id"]
        id2 = res2["item"]["id"]

        # Resolve in favor of Brave with evidence
        resolved = store.resolve_conflict(
            accepted_id=id1,
            retired_id=id2,
            resolution_evidence="Verified brave --version (Brave 1.76.71) running on port 9222"
        )
        self.assertTrue(resolved)

        store_reloaded = CoreMemoryStore(config_path=self.config_path)
        snapshot = store_reloaded.get_snapshot()

        # Brave is now active in snapshot
        self.assertIn("Primary browser is Brave", snapshot)
        # Firefox is retired from active snapshot
        self.assertNotIn("Primary browser is Firefox", snapshot)

        # But Firefox is STILL preserved in persistent records (marked archived)
        data = store_reloaded.load()
        memories = store_reloaded._extract_memories(data)
        retired_mem = next((m for m in memories if m.id == id2), None)
        self.assertIsNotNone(retired_mem)
        self.assertIn("[ARCHIVED/SUPERSEDED", retired_mem.content)

    def test_strict_snapshot_token_budget_limit(self):
        """Core memory compiled snapshot must NEVER exceed 250 tokens regardless of stored items."""
        store = CoreMemoryStore(config_path=self.config_path)

        # Inject 40 lengthy directives
        for i in range(40):
            store.add_memory(
                content=f"Detailed operational directive number {i+1} specifies standard protocols for task execution.",
                mem_type="directive",
                importance=0.5 + (i * 0.01)
            )

        snapshot = store.get_snapshot()
        token_count = len(snapshot) // 4
        self.assertLessEqual(token_count, 250)
        self.assertTrue(len(snapshot) > 0)

    def test_episodic_memory_fts5_bm25_retrieval(self):
        """Episodic memory store indexes past tasks and retrieves them via BM25 semantic text ranking."""
        store = EpisodicMemoryStore(db_path=self.db_path)

        store.record_episode(
            task_id="t1",
            query="Deploy luxury restaurant demo on Vercel",
            summary="Deployed Apex Detailing demo successfully",
            outcome="success",
            lessons_learned="Always pass --prod flag when updating live URL",
            importance=0.9
        )
        store.record_episode(
            task_id="t2",
            query="Fix Brave CDP websocket error",
            summary="Investigated CDP connection drop on port 9222",
            outcome="failure",
            lessons_learned="Ensure Brave instance launched with --remote-debugging-port=9222",
            importance=0.85
        )
        store.record_episode(
            task_id="t3",
            query="Scrape dental leads in London",
            summary="Scraped 20 dental clinics from Google Maps",
            outcome="success",
            lessons_learned="Filter out chains without direct decision maker contact",
            importance=0.7
        )

        # Search for Vercel deployment
        results_vercel = store.retrieve_relevant("How to deploy demo to Vercel?", limit=1)
        self.assertEqual(len(results_vercel), 1)
        self.assertEqual(results_vercel[0]["task_id"], "t1")
        self.assertIn("Always pass --prod flag", results_vercel[0]["lessons_learned"])

        # Search for CDP websocket
        results_cdp = store.retrieve_relevant("Brave CDP websocket disconnected", limit=1)
        self.assertEqual(len(results_cdp), 1)
        self.assertEqual(results_cdp[0]["task_id"], "t2")
        self.assertIn("remote-debugging-port", results_cdp[0]["lessons_learned"])

if __name__ == "__main__":
    unittest.main()
