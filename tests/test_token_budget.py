"""Unit and Contract Tests for Cumulative Task Token Tracker & Cost Accounting.
Enforces non-negotiable architectural rule:
Accumulate actual usage across every model request in a multi-turn task.
Track breakdown: memory, tool definitions, tool outputs, final synthesis, and cost.
"""
import unittest
import tempfile
import shutil
import os
import json
from ghost.core.token_tracker import TaskTokenTracker

class TestTokenBudget(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.stats_file = os.path.join(self.temp_dir, "token_stats.json")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_multi_turn_cumulative_aggregation(self):
        """Tokens and model calls accumulate across multiple turns rather than overwriting with only the last turn."""
        tracker = TaskTokenTracker(task_id="task_multi_turn_01", model="antigravity/gemini-3.8-flash-low")

        # Turn 1: Initial user request -> model returns tool call
        tracker.add_turn_usage(prompt_tokens=1500, completion_tokens=80, total_tokens=1580)
        tracker.record_tool_call()
        tracker.record_category_tokens("memory", 200)
        tracker.record_category_tokens("tool_definitions", 800)

        self.assertEqual(tracker.model_calls, 1)
        self.assertEqual(tracker.tool_calls, 1)
        self.assertEqual(tracker.prompt_tokens, 1500)
        self.assertEqual(tracker.completion_tokens, 80)
        self.assertEqual(tracker.total_tokens, 1580)

        # Turn 2: Tool output fed back -> model returns another tool call
        tracker.add_turn_usage(prompt_tokens=2200, completion_tokens=120, total_tokens=2320)
        tracker.record_tool_call()
        tracker.record_category_tokens("tool_outputs", 600)

        self.assertEqual(tracker.model_calls, 2)
        self.assertEqual(tracker.tool_calls, 2)
        self.assertEqual(tracker.prompt_tokens, 3700)
        self.assertEqual(tracker.completion_tokens, 200)
        self.assertEqual(tracker.total_tokens, 3900)

        # Turn 3: Final synthesis pass
        tracker.add_turn_usage(prompt_tokens=2800, completion_tokens=250, total_tokens=3050)
        tracker.record_category_tokens("final_synthesis", 250)

        self.assertEqual(tracker.model_calls, 3)
        self.assertEqual(tracker.tool_calls, 2)
        self.assertEqual(tracker.prompt_tokens, 6500)
        self.assertEqual(tracker.completion_tokens, 450)
        self.assertEqual(tracker.total_tokens, 6950)

        # Breakdown categories verified
        self.assertEqual(tracker.breakdown["memory"], 200)
        self.assertEqual(tracker.breakdown["tool_definitions"], 800)
        self.assertEqual(tracker.breakdown["tool_outputs"], 600)
        self.assertEqual(tracker.breakdown["final_synthesis"], 250)

    def test_cost_calculation(self):
        """USD cost is calculated correctly based on model pricing table."""
        tracker = TaskTokenTracker(task_id="task_cost", model="antigravity/gemini-3.8-flash-low")
        # 1,000,000 prompt tokens ($0.10) + 1,000,000 completion tokens ($0.40) = $0.50
        tracker.add_turn_usage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
        cost = tracker.compute_cost()
        self.assertAlmostEqual(cost, 0.50, places=4)

        # Small turn calculation
        tracker_small = TaskTokenTracker(task_id="task_small", model="antigravity/gemini-3.8-flash-low")
        tracker_small.add_turn_usage(prompt_tokens=10_000, completion_tokens=1_000)
        cost_small = tracker_small.compute_cost()
        # 10k prompt = $0.001, 1k comp = $0.0004 -> total $0.0014
        self.assertAlmostEqual(cost_small, 0.0014, places=4)

    def test_persistence_to_disk(self):
        """Token stats persist to JSON file and aggregate daily totals."""
        tracker1 = TaskTokenTracker(task_id="t1", model="antigravity/gemini-3.8-flash-low")
        tracker1.add_turn_usage(100, 50, 150)
        stats1 = tracker1.save_to_disk(filepath=self.stats_file)

        self.assertEqual(stats1["last"], 150)
        self.assertEqual(stats1["today"], 150)
        self.assertEqual(len(stats1["history"]), 1)

        tracker2 = TaskTokenTracker(task_id="t2", model="antigravity/gemini-3.8-flash-low")
        tracker2.add_turn_usage(200, 100, 300)
        stats2 = tracker2.save_to_disk(filepath=self.stats_file)

        self.assertEqual(stats2["last"], 300)
        self.assertEqual(stats2["today"], 450)
        self.assertEqual(len(stats2["history"]), 2)

if __name__ == "__main__":
    unittest.main()
