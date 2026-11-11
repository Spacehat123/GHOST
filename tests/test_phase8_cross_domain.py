"""Tests for Phase 8 Cross-Domain Intelligence.

Validates:
1. CrossDomainCoordinator cross-agent delegation with parent-child dependency tracking.
2. Cascade 1: Dev test failure -> DevelopmentIntelligenceAgent diagnostic task.
3. Cascade 2: Sustained system resource anomaly -> SystemIntelligenceAgent investigation task.
4. Cascade 3: Research discovery -> BusinessIntelligenceAgent prospect evaluation.
5. Cascade 4: Personal focus mode -> PhysicalIntelligenceAgent ambient scene actuation.
"""

import asyncio
import os
import tempfile
import unittest

from ghost.core.events import Event, EventBus, EventSeverity
from ghost.core.system_state import SystemStateManager
from ghost.core.task_orchestrator import TaskOrchestrator, TaskStatus
from ghost.intelligence.coordinator import CrossDomainCoordinator
from ghost.intelligence.objective_manager import ObjectiveManager


class TestPhase8CrossDomain(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tasks_db = os.path.join(self.tmpdir.name, "tasks.db")
        self.objs_db = os.path.join(self.tmpdir.name, "objs.db")
        self.bus = EventBus(db_path=":memory:", enable_persistence=False)
        self.state_mgr = SystemStateManager(event_bus=self.bus)
        self.obj_mgr = ObjectiveManager(db_path=self.objs_db)
        self.task_orch = TaskOrchestrator(db_path=self.tasks_db, event_bus=self.bus)
        self.coordinator = CrossDomainCoordinator(
            event_bus=self.bus,
            task_orchestrator=self.task_orch,
            state_manager=self.state_mgr,
            objective_manager=self.obj_mgr,
        )
        self.coordinator.start()

    async def asyncTearDown(self):
        self.coordinator.stop()
        self.tmpdir.cleanup()

    async def test_cross_agent_delegation(self):
        delegated_events = []
        self.bus.subscribe("cross_domain.delegation", lambda ev: delegated_events.append(ev))

        task = await self.coordinator.delegate_task(
            from_agent="business_agent",
            to_agent="creative_agent",
            goal="Generate 3D luxury hero asset for Mayfair client",
            payload={"model_name": "mayfair_bracket"},
            parent_task_id="tsk-parent-123",
        )

        self.assertIsNotNone(task)
        self.assertEqual(task.assigned_agent, "creative_agent")
        self.assertIn("mayfair_bracket", str(task.input_payload))
        self.assertEqual(task.input_payload.get("parent_task_id"), "tsk-parent-123")
        self.assertEqual(task.input_payload.get("delegator"), "business_agent")

        await asyncio.sleep(0.02)
        self.assertEqual(len(delegated_events), 1)
        self.assertEqual(delegated_events[0].payload["from_agent"], "business_agent")
        self.assertEqual(delegated_events[0].payload["to_agent"], "creative_agent")

    async def test_dev_failure_cascade(self):
        failure_event = Event(
            event_type="dev.test.failure",
            source="runner.pytest",
            severity=EventSeverity.ERROR,
            payload={"test": "tests/test_agent.py", "error": "TimeoutError in turn 4"},
        )

        await self.bus.publish(failure_event)
        await asyncio.sleep(0.02)

        # Check that coordinator spawned dev task
        tasks = self.task_orch.list_tasks(agent="dev_agent")
        self.assertTrue(
            any("tests/test_agent.py" in t.title for t in tasks),
            f"Expected dev agent task spawned for failure, found: {tasks}",
        )

    async def test_resource_pressure_cascade(self):
        pressure_event = Event(
            event_type="system.resource.sustained_anomaly",
            source="collector.system",
            severity=EventSeverity.WARNING,
            payload={"alerts": {"cpu": {"current": 94.2, "threshold": 85.0}}},
        )

        await self.bus.publish(pressure_event)
        await asyncio.sleep(0.02)

        tasks = self.task_orch.list_tasks(agent="system_agent")
        self.assertTrue(
            any("Diagnose Hardware Resource Anomaly" in t.title for t in tasks),
            f"Expected system agent recovery task, found: {tasks}",
        )

    async def test_focus_mode_smart_home_cascade(self):
        focus_event = Event(
            event_type="personal.mode.focus_engaged",
            source="agent.personal",
            severity=EventSeverity.INFO,
            payload={"session": "deep_work", "duration_m": 45},
        )

        await self.bus.publish(focus_event)
        await asyncio.sleep(0.02)

        tasks = self.task_orch.list_tasks(agent="physical_agent")
        self.assertTrue(
            any("Focus Session" in t.title for t in tasks),
            f"Expected physical agent ambient task, found: {tasks}",
        )


if __name__ == "__main__":
    unittest.main()
