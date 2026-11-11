"""Automated Integration Tests for Core Foundation APIs."""

import unittest
from fastapi.testclient import TestClient
from ghost.server.app import app
from ghost.core.events import Event, EventSeverity, get_event_bus
from ghost.core.system_state import HardwareVitals, get_system_state_manager


class TestCoreFoundationAPI(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        self.bus = get_event_bus()
        self.state_mgr = get_system_state_manager()
        self.state_mgr.set_vitals(HardwareVitals(
            cpu_percent=25.4,
            ram_used_mb=3072.0,
            ram_total_mb=8192.0,
            ram_percent=37.5,
            disk_free_gb=150.0,
        ))

    def test_system_state_endpoint(self):
        resp = self.client.get("/api/system/state")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("vitals", data)
        self.assertEqual(data["vitals"]["cpu_percent"], 25.4)

    def test_awareness_telemetry_endpoint(self):
        resp = self.client.get("/api/awareness/telemetry")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("vitals", data)
        self.assertIn("git", data)
        self.assertIn("recent_alerts", data)

    def test_agents_endpoints(self):
        resp = self.client.get("/api/agents")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["count"], 4)

        # Plan task
        plan_resp = self.client.post("/api/agents/system_agent/plan", json={"target": "vitals"})
        self.assertEqual(plan_resp.status_code, 200)
        plan_data = plan_resp.json()
        self.assertIn("tasks", plan_data)
        self.assertTrue(len(plan_data["tasks"]) > 0)

        # Execute task
        task = plan_data["tasks"][0]
        exec_resp = self.client.post("/api/agents/system_agent/execute", json={"task": task})
        self.assertEqual(exec_resp.status_code, 200)
        exec_data = exec_resp.json()
        self.assertIn("evidence", exec_data)
        self.assertTrue(exec_data["evidence"]["verified"])

    def test_tasks_endpoints(self):
        # Create task
        task_payload = {
            "title": "Test Task Orchestration",
            "assigned_agent": "dev_agent",
            "description": "Integration test task",
        }
        create_resp = self.client.post("/api/tasks", json=task_payload)
        self.assertEqual(create_resp.status_code, 200)
        created = create_resp.json()["task"]
        task_id = created["task_id"]

        # List tasks
        list_resp = self.client.get("/api/tasks")
        self.assertEqual(list_resp.status_code, 200)
        task_ids = [t["task_id"] for t in list_resp.json()["tasks"]]
        self.assertIn(task_id, task_ids)

        # Cancel task
        cancel_resp = self.client.post(f"/api/tasks/{task_id}/cancel")
        self.assertEqual(cancel_resp.status_code, 200)
        self.assertTrue(cancel_resp.json()["cancelled"])

    def test_projects_endpoint(self):
        resp = self.client.get("/api/projects")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("repository", data)
        self.assertIn("branch", data["repository"])

    def test_tools_endpoint(self):
        resp = self.client.get("/api/tools")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["count"], 40)

    def test_universal_events_endpoint(self):
        # Publish an event
        self.bus.publish_sync(Event(
            event_type="test.api.sample",
            source="test.api",
            severity=EventSeverity.INFO,
            payload={"msg": "hello from api test"},
        ))
        resp = self.client.get("/api/universal/events?prefix=test.api")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["count"], 1)
