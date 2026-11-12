"""Unit tests for Personal Intelligence & Persistent Memory System."""

import os
import tempfile
import unittest

from ghost.core.personal_intelligence import PersonalIntelligenceStore, PersonalMemory


class TestPersonalIntelligence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_personal.db")
        self.store = PersonalIntelligenceStore(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initial_seeding(self):
        summary = self.store.get_categories_summary()
        self.assertGreaterEqual(summary["total_memories"], 8)
        self.assertIn("profile", summary["categories"])
        self.assertIn("procedural", summary["categories"])
        self.assertIn("project", summary["categories"])
        self.assertIn("goal", summary["categories"])

        # Check seeded profile facts
        profile_mems = self.store.list_memories(category="profile")
        subjects = [m.subject for m in profile_mems]
        self.assertIn("User Identity", subjects)
        self.assertIn("Hardware & OS Environment", subjects)
        self.assertIn("Communication Style", subjects)

    def test_add_and_get_memory(self):
        mem = self.store.add_memory(
            category="knowledge",
            subject="Three.js Shaders",
            content="Use RawShaderMaterial with GLSL ES 3.0 for custom filament rendering.",
            source="user_prompt",
            confidence=0.9,
            is_inferred=True
        )
        self.assertTrue(mem.id.startswith("pi_knowledge_"))
        self.assertTrue(mem.is_inferred)

        fetched = self.store.get_memory(mem.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.subject, "Three.js Shaders")
        self.assertEqual(fetched.content, "Use RawShaderMaterial with GLSL ES 3.0 for custom filament rendering.")
        self.assertTrue(fetched.is_inferred)

    def test_update_and_correction(self):
        mem = self.store.add_memory(
            category="procedural",
            subject="Python Code Style",
            content="Use 2 spaces indentation.",
            is_inferred=True
        )
        self.assertTrue(mem.is_inferred)

        # User corrects memory
        updated = self.store.update_memory(
            memory_id=mem.id,
            content="Strictly adhere to PEP 8 with 4 spaces indentation and typing annotations."
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.content, "Strictly adhere to PEP 8 with 4 spaces indentation and typing annotations.")
        self.assertFalse(updated.is_inferred)  # Auto promoted to user_confirmed
        self.assertEqual(updated.source, "user_confirmed")
        self.assertEqual(updated.confidence, 1.0)

    def test_confirm_inferred_memory(self):
        mem = self.store.add_memory(
            category="goal",
            subject="Learn Rust",
            content="Wants to rewrite high-frequency modules in Rust.",
            is_inferred=True,
            confidence=0.7
        )
        self.assertTrue(mem.is_inferred)

        confirmed = self.store.confirm_memory(mem.id)
        self.assertIsNotNone(confirmed)
        self.assertFalse(confirmed.is_inferred)
        self.assertEqual(confirmed.source, "user_confirmed")
        self.assertEqual(confirmed.confidence, 1.0)

    def test_delete_memory(self):
        mem = self.store.add_memory(
            category="episodic",
            subject="Temporary Experiment",
            content="Tested temporary docker container.",
        )
        self.assertTrue(self.store.delete_memory(mem.id))
        self.assertIsNone(self.store.get_memory(mem.id))

    def test_search_and_retrieval(self):
        # Stock search retrieval
        res = self.store.retrieve_context(query="stock analysis NIFTY", limit=3)
        self.assertIn("snapshot", res)
        self.assertIn("retrieved_ids", res)
        self.assertGreaterEqual(len(res["retrieved_ids"]), 1)
        self.assertIn("Stock & Market Research Requirements", res["snapshot"])

        # Check influence count incremented
        mem = self.store.get_memory(res["retrieved_ids"][0])
        self.assertGreaterEqual(mem.influence_count, 1)

    def test_filter_by_inferred(self):
        # Add an inferred item
        self.store.add_memory(
            category="profile",
            subject="Favorite Tea",
            content="Inferred preference for Earl Grey.",
            is_inferred=True
        )
        inferred = self.store.list_memories(is_inferred=True)
        self.assertTrue(any(m.subject == "Favorite Tea" for m in inferred))
        confirmed = self.store.list_memories(is_inferred=False)
        self.assertFalse(any(m.subject == "Favorite Tea" for m in confirmed))
