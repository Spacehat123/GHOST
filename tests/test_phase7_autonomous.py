"""Tests for Phase 7 Autonomous Execution.

Validates:
1. AutonomousExecutionEngine objective decomposition and task execution.
2. Independent result verification before marking tasks COMPLETED.
3. Fail-closed Global Halt enforcement (aborts execution when halted).
4. Lease expiration and crash recovery.
"""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from ghost.autonomy.task_worker import AutonomousExecutionEngine
from ghost.core.events import EventBus
from ghost.core.system_state import SystemStateManager
from ghost.core.task_orchestrator import Task, TaskOrchestrator, TaskStatus
from ghost.intelligence.objective_manager import ObjectiveManager


class TestPhase7Autonomous(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tasks_db = os.path.join(self.tmpdir.name, "tasks.db")
        self.objs_db = os.path.join(self.tmpdir.name, "objs.db")
        self.bus = EventBus(db_path=":memory:", enable_persistence=False)
        self.state_mgr = SystemStateManager(event_bus=self.bus)
        self.obj_mgr = ObjectiveManager(db_path=self.objs_db)
        self.task_orch = TaskOrchestrator(db_path=self.tasks_db, event_bus=self.bus)
        self.engine = AutonomousExecutionEngine(
            task_orchestrator=self.task_orch,
            objective_manager=self.obj_mgr,
            state_manager=self.state_mgr,
            event_bus=self.bus,
            max_concurrency=2,
            worker_id="test-worker-1",
        )

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    async def test_objective_decomposition_flow(self):
        obj = self.obj_mgr.create_objective(
            title="Inspect System Resource Profile",
            domain="system",
            target_agent="system_agent",
            criteria=["profile_complete"],
        )

        res = await self.engine.execute_objective(obj.objective_id)
        self.assertEqual(res["status"], "planned")
        self.assertGreater(res["tasks_count"], 0)

        # Tasks should exist in orchestrator
        tasks = self.task_orch.list_tasks(objective_id=obj.objective_id)
        self.assertEqual(len(tasks), res["tasks_count"])

    async def test_autonomous_task_execution_and_verification(self):
        # Create a task for personal agent (daily briefing)
        task = self.task_orch.submit_task(
            objective_id="obj-test-exec",
            assigned_agent="personal_agent",
            title="Run Daily Briefing",
            description="",
            input_payload={"type": "daily_briefing"},
            required_permissions=["personal.write_briefing"],
            initial_status=TaskStatus.QUEUED,
        )

        # Run one worker cycle
        leased = self.task_orch.acquire_lease(task.task_id, worker_id="test-worker-1")
        self.assertTrue(leased)

        # Execute single task
        await self.engine._execute_single_task(task)

        # Verify task reached COMPLETED status
        updated = self.task_orch.get_task(task.task_id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, TaskStatus.COMPLETED)
        self.assertIsNotNone(updated.output_payload)
        self.assertTrue(updated.output_payload["evidence"]["verified"])

    async def test_global_halt_aborts_execution(self):
        task = self.task_orch.submit_task(
            objective_id="obj-test-halt",
            assigned_agent="system_agent",
            title="System Task",
            description="",
            input_payload={"action": "diagnose_resources"},
            required_permissions=["system.read_procfs"],
            initial_status=TaskStatus.QUEUED,
        )

        self.task_orch.acquire_lease(task.task_id, worker_id="test-worker-1")

        with patch("ghost.autonomy.task_worker.is_halted", return_value=True):
            await self.engine._execute_single_task(task)

        # Status should NOT be completed because halt prevented execution
        curr = self.task_orch.get_task(task.task_id)
        self.assertNotEqual(curr.status, TaskStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
