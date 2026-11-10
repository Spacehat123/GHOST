"""Unit and lifecycle tests for TaskOrchestrator (INTERFACE_CONTRACTS §2)."""

import os
import tempfile
import time
import unittest

from ghost.core.events import EventBus
from ghost.core.task_orchestrator import (
    Task,
    TaskOrchestrator,
    TaskStatus,
)


class TestTaskOrchestrator(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "tasks_test.db")
        self.bus = EventBus(db_path=os.path.join(self.tmpdir.name, "events_test.db"))
        self.orchestrator = TaskOrchestrator(db_path=self.db_path, event_bus=self.bus)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_submit_and_get_task(self):
        """Task is durably persisted and queryable."""
        task = self.orchestrator.submit_task(
            objective_id="obj-001",
            assigned_agent="lead_hunter",
            title="Scan Luxury Detailing in Mayfair",
            description="Find detailing studios with low ratings",
            input_payload={"vertical": "detailing", "city": "London"},
            required_permissions=["web.search"],
        )
        self.assertTrue(task.task_id.startswith("tsk-"))
        self.assertEqual(task.status, TaskStatus.QUEUED)
        self.assertEqual(task.attempts, 0)

        retrieved = self.orchestrator.get_task(task.task_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.task_id, task.task_id)
        self.assertEqual(retrieved.input_payload["vertical"], "detailing")
        self.assertEqual(retrieved.required_permissions, ["web.search"])

    def test_lease_acquisition_and_renewal(self):
        """Worker can acquire lease on queued task and extend it."""
        task = self.orchestrator.submit_task(
            objective_id="obj-002",
            assigned_agent="qa_worker",
            title="Run DOM Linting",
        )

        # Worker 1 acquires lease
        acquired = self.orchestrator.acquire_lease(task.task_id, worker_id="w-1", lease_duration_s=30.0)
        self.assertTrue(acquired)

        running_task = self.orchestrator.get_task(task.task_id)
        self.assertEqual(running_task.status, TaskStatus.RUNNING)
        self.assertEqual(running_task.lease_owner, "w-1")
        self.assertEqual(running_task.attempts, 1)
        self.assertIsNotNone(running_task.lease_until)

        # Worker 2 cannot steal an active lease
        steal_attempt = self.orchestrator.acquire_lease(task.task_id, worker_id="w-2", lease_duration_s=30.0)
        self.assertFalse(steal_attempt)

        # Worker 1 can renew lease
        renewed = self.orchestrator.renew_lease(task.task_id, worker_id="w-1", lease_duration_s=60.0)
        self.assertTrue(renewed)

    def test_lifecycle_transitions(self):
        """Task completes full valid lifecycle transition."""
        task = self.orchestrator.submit_task(
            objective_id="obj-003",
            assigned_agent="builder",
            title="Generate Demo Markup",
        )
        self.orchestrator.acquire_lease(task.task_id, worker_id="w-builder")

        # RUNNING -> VERIFYING
        t_verifying = self.orchestrator.transition(
            task.task_id,
            TaskStatus.VERIFYING,
            output_payload={"html_file": "/tmp/demo.html"},
        )
        self.assertEqual(t_verifying.status, TaskStatus.VERIFYING)
        self.assertEqual(t_verifying.output_payload["html_file"], "/tmp/demo.html")

        # VERIFYING -> COMPLETED
        t_completed = self.orchestrator.transition(
            task.task_id,
            TaskStatus.COMPLETED,
            output_payload={"html_file": "/tmp/demo.html", "verified": True},
        )
        self.assertEqual(t_completed.status, TaskStatus.COMPLETED)
        self.assertIsNone(t_completed.lease_owner)

    def test_illegal_transition_rejected(self):
        """Illegal state transition raises ValueError."""
        task = self.orchestrator.submit_task(
            objective_id="obj-004",
            assigned_agent="builder",
            title="Direct jump test",
        )
        # Attempting jump from QUEUED directly to COMPLETED
        with self.assertRaises(ValueError):
            self.orchestrator.transition(task.task_id, TaskStatus.COMPLETED)

    def test_retry_ladder_on_failure(self):
        """Failed tasks automatically retry until max_attempts is exceeded."""
        task = self.orchestrator.submit_task(
            objective_id="obj-005",
            assigned_agent="flaky_worker",
            title="Flaky network request",
            max_attempts=2,
        )

        # Attempt 1
        self.orchestrator.acquire_lease(task.task_id, worker_id="w-flaky")
        t_retry = self.orchestrator.transition(
            task.task_id,
            TaskStatus.FAILED,
            error_reason="Network 503",
        )
        # Should transition to RETRYING because attempts (1) < max_attempts (2)
        self.assertEqual(t_retry.status, TaskStatus.RETRYING)
        self.assertEqual(t_retry.error_reason, "Network 503")

        # Attempt 2
        self.orchestrator.acquire_lease(task.task_id, worker_id="w-flaky")
        t_failed = self.orchestrator.transition(
            task.task_id,
            TaskStatus.FAILED,
            error_reason="Network 503 second time",
        )
        # Should now permanently transition to FAILED because attempts (2) >= max_attempts (2)
        self.assertEqual(t_failed.status, TaskStatus.FAILED)

    def test_stale_lease_reaper(self):
        """Expired worker leases are reaped and rescheduled."""
        task = self.orchestrator.submit_task(
            objective_id="obj-006",
            assigned_agent="worker",
            title="Died worker task",
            max_attempts=3,
        )
        self.orchestrator.acquire_lease(task.task_id, worker_id="w-dead", lease_duration_s=1.0)

        # Fast forward time past lease expiration
        future_time = time.time() + 10.0
        reaped = self.orchestrator.reap_stale_leases(now=future_time)

        self.assertIn(task.task_id, reaped)
        reaped_task = self.orchestrator.get_task(task.task_id)
        self.assertEqual(reaped_task.status, TaskStatus.RETRYING)
        self.assertIsNone(reaped_task.lease_owner)


if __name__ == "__main__":
    unittest.main()
