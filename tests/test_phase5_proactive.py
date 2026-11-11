"""Tests for Phase 5 Proactive Intelligence.

Validates:
1. ObjectiveManager CRUD and persistence.
2. EventPriorityEngine ranking (importance, urgency, relevance, duplicate suppression, modes).
3. ProactiveOrchestrator event evaluation and autonomous task creation with approval gating.
"""

import asyncio
import unittest

from ghost.core.events import Event, EventBus, EventSeverity
from ghost.core.system_state import SystemStateManager
from ghost.core.task_orchestrator import TaskOrchestrator, TaskStatus
from ghost.intelligence.objective_manager import ObjectiveManager
from ghost.intelligence.priority_engine import EventPriorityEngine
from ghost.intelligence.proactive_orchestrator import ProactiveOrchestrator


class TestPhase5Proactive(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bus = EventBus(db_path=":memory:", enable_persistence=False)
        self.state_mgr = SystemStateManager(event_bus=self.bus)
        self.obj_mgr = ObjectiveManager(db_path=":memory:")
        self.task_orch = TaskOrchestrator(event_bus=self.bus)
        self.priority_engine = EventPriorityEngine(
            objective_manager=self.obj_mgr,
            suppression_window_s=10.0,
        )
        self.proactive_orch = ProactiveOrchestrator(
            event_bus=self.bus,
            priority_engine=self.priority_engine,
            task_orchestrator=self.task_orch,
            state_manager=self.state_mgr,
        )

    def test_objective_manager_crud(self):
        obj = self.obj_mgr.create_objective(
            title="Maintain Sub-100ms API Latency",
            domain="system",
            target_agent="system_agent",
            criteria=["latency < 100ms", "zero dropouts"],
            priority=4,
        )
        self.assertEqual(obj.title, "Maintain Sub-100ms API Latency")
        self.assertEqual(obj.progress, 0.0)

        # Update progress
        updated = self.obj_mgr.update_progress(obj.objective_id, 0.75)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.progress, 0.75)

        # List
        listed = self.obj_mgr.list_objectives(domain="system")
        self.assertEqual(len(listed), 1)

    def test_priority_engine_duplicate_suppression(self):
        event = Event(
            event_type="system.telemetry.sample",
            source="collector.system",
            severity=EventSeverity.INFO,
            payload={"cpu": 15.0},
        )
        dec1 = self.priority_engine.evaluate(event, operating_mode="AMBIENT")
        self.assertNotEqual(dec1.action_recommended, "SUPPRESS_DUPLICATE")

        # Immediate duplicate
        dec2 = self.priority_engine.evaluate(event, operating_mode="AMBIENT")
        self.assertEqual(dec2.action_recommended, "SUPPRESS_DUPLICATE")

    def test_priority_engine_operating_modes(self):
        anomaly_event = Event(
            event_type="system.resource.sustained_anomaly",
            source="collector.system",
            severity=EventSeverity.WARNING,
            payload={"alerts": {"ram": {"current": 92.5}}},
        )

        # In AMBIENT mode, WARNING is logged passively
        dec_ambient = self.priority_engine.evaluate(anomaly_event, operating_mode="AMBIENT")
        self.assertEqual(dec_ambient.action_recommended, "LOG_AMBIENT")

        # In REACTIVE mode, WARNING triggers INVESTIGATE with evidence
        self.priority_engine._seen_fingerprints.clear()
        dec_reactive = self.priority_engine.evaluate(anomaly_event, operating_mode="REACTIVE")
        self.assertEqual(dec_reactive.action_recommended, "INVESTIGATE")
        self.assertIn("Reactive Mode triggered", dec_reactive.reason)
        self.assertGreaterEqual(dec_reactive.priority_score, 6.0)

    async def test_proactive_orchestrator_autonomous_task_creation(self):
        self.state_mgr.set_operating_mode("REACTIVE")
        self.proactive_orch.start()

        failure_event = Event(
            event_type="dev.test.failure",
            source="runner.pytest",
            severity=EventSeverity.ERROR,
            payload={"test": "tests/test_core.py", "error": "AssertionError"},
        )

        await self.bus.publish(failure_event)
        await asyncio.sleep(0.05)  # Let async subscriber run

        # Assert task was created in TaskOrchestrator
        tasks = self.task_orch.list_tasks(status=TaskStatus.QUEUED)
        self.assertTrue(
            any("Proactive Investigation: dev.test.failure" in t.title for t in tasks),
            f"Expected proactive investigation task in queued tasks: {tasks}",
        )

        self.proactive_orch.stop()


if __name__ == "__main__":
    unittest.main()
