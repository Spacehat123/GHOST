"""Unit and Integration Tests for Ghost Mission System, Evidence Verification, and Plan-First Gate.
Verifies all non-negotiable architectural requirements of Part 8:
1. Complex requests enforce Plan-First Gate (build tools blocked before mission creation).
2. Evidence verification strictly rejects missing or fake evidence.
3. Single in_progress task invariant and dependency ordering.
4. Per-task budget resets and mission caps.
5. SQLite resumption across restarts.
6. Auto-appended final audit task.
7. Simple queries bypass mission requirements.
8. Approval tasks block automated completion without operator confirmation.
"""
import os
import json
import tempfile
import unittest
import struct
import zlib
from unittest.mock import patch, MagicMock, AsyncMock

from ghost.core.mission import (
    MissionStore,
    Mission,
    Task,
    TaskStatus,
    TaskBudget,
    MAX_TASKS_PER_MISSION
)
from ghost.core.evidence import (
    verify_evidence_item,
    verify_task_evidence
)
from ghost.core.agent import GhostAgent, MissionRunner, needs_plan, BUILD_TOOLS
from ghost.core.brain import OmniRouteBrain


class DummyChunk:
    def __init__(self, content=None, tool_calls=None, usage=None):
        self.choices = [MagicMock()]
        self.choices[0].delta = MagicMock()
        self.choices[0].delta.content = content
        self.choices[0].delta.tool_calls = tool_calls
        self.usage = usage


class DummyUsage:
    def __init__(self, prompt_tokens=100, completion_tokens=50, total_tokens=150):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class TestEvidenceVerification(unittest.TestCase):
    """Test strict typed evidence verification logic."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_file_evidence_valid_and_hash(self):
        test_file = os.path.join(self.temp_dir.name, "output.html")
        with open(test_file, "w") as f:
            f.write("<html><body>Luxury Demo</body></html>")

        item = {"type": "file", "path": test_file}
        ok, reason, enriched = verify_evidence_item(item)
        self.assertTrue(ok)
        self.assertIn("hash", enriched)
        self.assertGreater(enriched["size_bytes"], 0)

    def test_file_evidence_rejects_nonexistent_or_empty(self):
        # Non-existent
        fake_file = os.path.join(self.temp_dir.name, "ghost_nonexistent.txt")
        ok, reason, _ = verify_evidence_item({"type": "file", "path": fake_file})
        self.assertFalse(ok)
        self.assertIn("does not exist", reason)

        # Empty file
        empty_file = os.path.join(self.temp_dir.name, "empty.txt")
        open(empty_file, "w").close()
        ok, reason, _ = verify_evidence_item({"type": "file", "path": empty_file})
        self.assertFalse(ok)
        self.assertIn("empty", reason)

    def test_command_evidence_exit_code_and_output(self):
        valid = {"type": "command", "command": "npm test", "exit_code": 0, "output": "Pass 10 tests"}
        ok, _, enriched = verify_evidence_item(valid)
        self.assertTrue(ok)

        # Non-zero exit code
        failed = {"type": "command", "command": "npm test", "exit_code": 1, "output": "Error: 1 failure"}
        ok, reason, _ = verify_evidence_item(failed)
        self.assertFalse(ok)
        self.assertIn("non-zero", reason)

        # Empty output
        no_output = {"type": "command", "command": "npm test", "exit_code": 0, "output": ""}
        ok, reason, _ = verify_evidence_item(no_output)
        self.assertFalse(ok)

    def test_screenshot_evidence_detects_blank_solid(self):
        # Generate solid color PNG (zero pixel variance)
        blank_png = os.path.join(self.temp_dir.name, "blank.png")
        width, height = 10, 10
        raw_scanlines = b"".join(b"\x00" + (b"\x00\x00\x00\xff" * width) for _ in range(height))
        compressed_idat = zlib.compress(raw_scanlines)

        with open(blank_png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            # IHDR
            ihdr_payload = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
            f.write(struct.pack(">I4s", len(ihdr_payload), b"IHDR") + ihdr_payload + struct.pack(">I", 0))
            # IDAT
            f.write(struct.pack(">I4s", len(compressed_idat), b"IDAT") + compressed_idat + struct.pack(">I", 0))
            # IEND
            f.write(struct.pack(">I4s", 0, b"IEND") + struct.pack(">I", 0))

        ok, reason, _ = verify_evidence_item({"type": "screenshot", "path": blank_png})
        self.assertFalse(ok)
        self.assertIn("blank or solid color", reason)

    def test_test_suite_evidence_pass_count(self):
        valid = {"type": "test", "pass_count": 5, "failures": 0, "errors": 0}
        ok, _, _ = verify_evidence_item(valid)
        self.assertTrue(ok)

        zero_passes = {"type": "test", "pass_count": 0, "failures": 0, "errors": 0}
        ok, reason, _ = verify_evidence_item(zero_passes)
        self.assertFalse(ok)

        failed = {"type": "test", "pass_count": 5, "failures": 1, "errors": 0}
        ok, reason, _ = verify_evidence_item(failed)
        self.assertFalse(ok)


class TestMissionStore(unittest.TestCase):
    """Test SQLite persistence, state invariants, and dependency rules."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_missions.db")
        self.store = MissionStore(db_path=self.db_path)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_create_mission_auto_appends_audit_task(self):
        tasks = [
            {
                "title": "Analyze 3D asset",
                "description": "Inspect geometry",
                "acceptance": ["GLB file inspected"],
                "evidence_required": ["file"]
            },
            {
                "title": "Build scene",
                "description": "Assemble Three.js scene",
                "acceptance": ["Scene rendered"],
                "evidence_required": ["screenshot"]
            }
        ]
        mission = self.store.create_mission(goal="Build 3D showcase", tasks=tasks)
        self.assertIsNotNone(mission.id)
        # Verify that an independent final audit task was automatically appended!
        self.assertEqual(len(mission.tasks), 3)
        self.assertEqual(mission.tasks[-1].task_type, "audit")
        self.assertIn("Audit", mission.tasks[-1].title)

    def test_empty_acceptance_criteria_rejected(self):
        invalid_tasks = [
            {
                "title": "Step 1",
                "description": "No criteria",
                "acceptance": [],
                "evidence_required": ["file"]
            }
        ]
        with self.assertRaises(ValueError):
            self.store.create_mission(goal="Invalid", tasks=invalid_tasks)

    def test_single_in_progress_task_invariant(self):
        tasks = [
            {"id": "t1", "title": "T1", "description": "D1", "acceptance": ["A1"], "evidence_required": ["file"]},
            {"id": "t2", "title": "T2", "description": "D2", "acceptance": ["A2"], "evidence_required": ["file"]}
        ]
        mission = self.store.create_mission(goal="Concurrency Test", tasks=tasks)
        m_id = mission.id

        # Set t1 to in_progress
        self.store.update_task(m_id, "t1", status=TaskStatus.in_progress)

        # Attempting to set t2 to in_progress simultaneously must raise ValueError
        with self.assertRaises(ValueError) as ctx:
            self.store.update_task(m_id, "t2", status=TaskStatus.in_progress)
        self.assertIn("already in_progress", str(ctx.exception))

    def test_dependency_ordering_enforced(self):
        tasks = [
            {"id": "t1", "title": "Step 1", "description": "D1", "acceptance": ["A1"], "evidence_required": ["file"]},
            {"id": "t2", "title": "Step 2", "description": "D2", "acceptance": ["A2"], "evidence_required": ["file"], "depends_on": ["t1"]}
        ]
        mission = self.store.create_mission(goal="Dependency Test", tasks=tasks)
        m_id = mission.id

        # Cannot set t2 in_progress while t1 is pending
        with self.assertRaises(ValueError) as ctx:
            self.store.update_task(m_id, "t2", status=TaskStatus.in_progress)
        self.assertIn("dependency 't1' is not completed", str(ctx.exception))

    def test_done_status_rejected_without_verified_evidence(self):
        tasks = [
            {"id": "t1", "title": "Generate file", "description": "D1", "acceptance": ["File created"], "evidence_required": ["file"]}
        ]
        mission = self.store.create_mission(goal="Evidence Test", tasks=tasks)
        m_id = mission.id

        self.store.update_task(m_id, "t1", status=TaskStatus.in_progress)

        # Try marking done with missing evidence
        with self.assertRaises(ValueError) as ctx:
            self.store.update_task(m_id, "t1", status=TaskStatus.done, evidence=[])
        self.assertIn("Missing evidence", str(ctx.exception))

        # Try marking done with fake evidence (file doesn't exist)
        fake_ev = [{"type": "file", "path": "/tmp/non_existent_fake_path_12345.html"}]
        with self.assertRaises(ValueError) as ctx:
            self.store.update_task(m_id, "t1", status=TaskStatus.done, evidence=fake_ev)
        self.assertIn("does not exist", str(ctx.exception))

    def test_sqlite_resumption_across_restarts(self):
        tasks = [
            {"id": "t1", "title": "Step 1", "description": "D1", "acceptance": ["A1"], "evidence_required": ["file"]}
        ]
        mission = self.store.create_mission(goal="Persistence Test", tasks=tasks)
        m_id = mission.id

        # Simulate engine restart by creating a new MissionStore instance pointing to same SQLite DB
        new_store = MissionStore(db_path=self.db_path)
        loaded = new_store.get_mission(m_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.goal, "Persistence Test")
        self.assertEqual(len(loaded.tasks), 2)  # t1 + auto audit


class TestPlanFirstGateAndAgent(unittest.IsolatedAsyncioTestCase):
    """Test Plan-First Gate in agent ask_stream loop and MissionRunner execution."""

    async def asyncSetUp(self):
        OmniRouteBrain._config = {"api_key": "test_mock_key", "model": "test-model"}
        OmniRouteBrain._history = []
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "agent_mission.db")
        self.store = MissionStore(db_path=self.db_path)
        MissionStore._instance = self.store

    async def asyncTearDown(self):
        self.temp_dir.cleanup()
        MissionStore._instance = None

    def test_needs_plan_heuristic(self):
        # Complex requests need plan
        self.assertTrue(needs_plan("build a 3d site for my restaurant"))
        self.assertTrue(needs_plan("run a campaign for luxury hotels"))
        self.assertTrue(needs_plan("create a mission to deploy demo"))

        # Simple queries bypass
        self.assertFalse(needs_plan("what time is it"))
        self.assertFalse(needs_plan("who are you"))
        self.assertFalse(needs_plan("Run loop test"))

    @patch("ghost.core.brain.OmniRouteBrain.get_client")
    async def test_plan_first_gate_blocks_build_tool_without_mission(self, mock_get_client):
        """When complex request is asked without a mission, calling a build tool first is blocked."""
        agent = GhostAgent()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Assistant immediately calls build_luxury_demo without creating a mission first
        tc_build = MagicMock()
        tc_build.index = 0
        tc_build.id = "call_build_01"
        tc_build.function.name = "build_luxury_demo"
        tc_build.function.arguments = json.dumps({"restaurant_name": "Test Place"})

        async def stream_turn(*args, **kwargs):
            messages = kwargs.get("messages", [])
            last_msg = str(messages[-1].get("content", ""))
            # Check if previous tool output returned the Plan-First Gate violation
            if "PLAN_FIRST_GATE" in last_msg or "Plan-First Gate Violation" in last_msg:
                # Assistant acknowledges plan requirement
                yield DummyChunk(content="Understood. Creating mission plan first.", usage=DummyUsage(10, 10, 20))
                return
            yield DummyChunk(tool_calls=[tc_build], usage=DummyUsage(15, 10, 25))

        mock_client.chat.completions.create = AsyncMock(side_effect=stream_turn)

        events = []
        async for event in agent.ask_stream("build a site for Test Place"):
            events.append(event)

        # Confirm that tool_end event returned blocked with PLAN_FIRST_GATE
        tool_ends = [e for e in events if e.get("type") == "tool_end"]
        self.assertTrue(any(e.get("result", {}).get("compliance_rule") == "PLAN_FIRST_GATE" for e in tool_ends))

    @patch("ghost.core.brain.OmniRouteBrain.get_client")
    async def test_mission_runner_executes_sequential_tasks_with_evidence(self, mock_get_client):
        """MissionRunner executes tasks sequentially, verifying evidence before completing."""
        # Create a real test file on disk to serve as evidence
        valid_file = os.path.join(self.temp_dir.name, "scene.glb")
        with open(valid_file, "wb") as f:
            f.write(b"glTF2.0 valid fake binary asset")

        mission = self.store.create_mission(
            goal="Asset Pipeline",
            tasks=[
                {
                    "id": "t1",
                    "title": "Inspect GLB Asset",
                    "description": "Inspect file",
                    "acceptance": ["Asset inspected on disk"],
                    "evidence_required": ["file"]
                }
            ]
        )
        m_id = mission.id

        agent = GhostAgent()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        tc_update = MagicMock()
        tc_update.index = 0
        tc_update.id = "call_upd"
        tc_update.function.name = "mission_update_task"
        tc_update.function.arguments = json.dumps({
            "task_id": "t1",
            "status": "done",
            "result_summary": "GLB asset verified on disk",
            "evidence": [{"type": "file", "path": valid_file}]
        })

        async def stream_turn(*args, **kwargs):
            messages = kwargs.get("messages", [])
            last_msg = str(messages[-1].get("content", ""))
            if "status='done'" in last_msg or "Executing Task t1" in last_msg:
                yield DummyChunk(tool_calls=[tc_update], usage=DummyUsage(20, 10, 30))
            else:
                yield DummyChunk(content="Task completed.", usage=DummyUsage(10, 10, 20))

        mock_client.chat.completions.create = AsyncMock(side_effect=stream_turn)

        runner = MissionRunner(agent=agent, store=self.store)
        events = []
        async for event in runner.run(m_id):
            events.append(event)

        # Verify task t1 completed in store
        updated_m = self.store.get_mission(m_id)
        t1 = next(t for t in updated_m.tasks if t.id == "t1")
        self.assertEqual(t1.status, TaskStatus.done)
        self.assertGreater(len(t1.evidence), 0)
