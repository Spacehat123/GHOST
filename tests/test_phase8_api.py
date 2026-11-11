"""API Tests for Phase 5-8 Capabilities.

Validates:
1. /api/objectives CRUD
2. /api/intelligence/mode get & set
3. /api/intelligence/decisions
4. /api/tasks/{id}/approve & reject
5. /api/physical/states & /api/physical/call
6. /api/creative/scad
7. /api/settings/operator get & update
"""

import os
import unittest
from starlette.testclient import TestClient

from ghost.server.app import app
from ghost.core.task_orchestrator import get_task_orchestrator, TaskStatus


class TestPhase8API(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_objectives_api(self):
        # Create objective
        post_res = self.client.post("/api/objectives", json={
            "title": "API Test Goal",
            "domain": "system",
            "target_agent": "system_agent",
            "criteria": ["metric_ok"],
        })
        self.assertEqual(post_res.status_code, 200)
        obj = post_res.json()["objective"]
        self.assertEqual(obj["title"], "API Test Goal")

        # List objectives
        get_res = self.client.get("/api/objectives?domain=system")
        self.assertEqual(get_res.status_code, 200)
        objs = get_res.json()["objectives"]
        self.assertTrue(any(o["title"] == "API Test Goal" for o in objs))

    def test_intelligence_mode_api(self):
        # Set to REACTIVE
        set_res = self.client.post("/api/intelligence/mode", json={"mode": "REACTIVE"})
        self.assertEqual(set_res.status_code, 200)
        self.assertEqual(set_res.json()["mode"], "REACTIVE")

        # Get mode
        get_res = self.client.get("/api/intelligence/mode")
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json()["mode"], "REACTIVE")

        # Reset back to AMBIENT
        self.client.post("/api/intelligence/mode", json={"mode": "AMBIENT"})

    def test_task_approval_and_rejection_api(self):
        orch = get_task_orchestrator()
        task = orch.submit_task(
            objective_id="obj-api-gate",
            assigned_agent="business_agent",
            title="Outbound Outreach Approval Test",
            initial_status=TaskStatus.WAITING_FOR_APPROVAL,
        )

        # Approve
        app_res = self.client.post(f"/api/tasks/{task.task_id}/approve")
        self.assertEqual(app_res.status_code, 200)
        self.assertEqual(app_res.json()["status"], "QUEUED")

        # Submit another task to reject
        task2 = orch.submit_task(
            objective_id="obj-api-gate-2",
            assigned_agent="physical_agent",
            title="Actuator Approval Test",
            initial_status=TaskStatus.WAITING_FOR_APPROVAL,
        )

        rej_res = self.client.post(f"/api/tasks/{task2.task_id}/reject", json={"reason": "Denied by test operator"})
        self.assertEqual(rej_res.status_code, 200)
        self.assertEqual(rej_res.json()["status"], "CANCELLED")

    def test_physical_smart_home_api(self):
        # Get states
        states_res = self.client.get("/api/physical/states")
        self.assertEqual(states_res.status_code, 200)
        data = states_res.json()
        self.assertIn("states", data)
        self.assertIn("configured", data)

        # Call service
        call_res = self.client.post("/api/physical/call", json={
            "domain": "switch",
            "service": "turn_on",
            "data": {"entity_id": "switch.desk_lamp"},
        })
        self.assertEqual(call_res.status_code, 200)

    def test_creative_cad_api(self):
        cad_res = self.client.post("/api/creative/scad", json={
            "model_name": "api_test_bracket",
            "parameters": {"width": 30, "length": 45, "height": 10, "wall": 1.5},
        })
        self.assertEqual(cad_res.status_code, 200)
        data = cad_res.json()
        self.assertIn("result", data)
        self.assertIn("evidence", data)
        self.assertTrue(data["evidence"]["verified"])

    def test_operator_settings_api(self):
        # Get settings (masked)
        get_res = self.client.get("/api/settings/operator")
        self.assertEqual(get_res.status_code, 200)
        settings = get_res.json()["settings"]
        self.assertIn("HOMEASSISTANT_URL", settings)
        self.assertIn("MQTT_BROKER_HOST", settings)

        # Update setting
        update_res = self.client.post("/api/settings/operator", json={
            "HOMEASSISTANT_URL": "http://192.168.1.150:8123",
            "MQTT_BROKER_HOST": "192.168.1.150",
        })
        self.assertEqual(update_res.status_code, 200)
        self.assertIn("HOMEASSISTANT_URL", update_res.json()["updated"])

    def test_router_agents_and_classify_api(self):
        # 1. Get agents from router registry
        res = self.client.get("/api/router/agents")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("agents", data)
        self.assertEqual(data["count"], 7)
        agent_ids = [a["agent_id"] for a in data["agents"]]
        self.assertIn("system_agent", agent_ids)
        self.assertIn("research_agent", agent_ids)
        self.assertIn("business_agent", agent_ids)

        # 2. Classify intent
        classify_res = self.client.post("/api/router/classify", json={
            "query": "Find high-ticket venues lacking an official digital presence, verify their pricing and aggregator status, collect authentic media, and prepare qualified dossiers for my CRM and Obsidian ledger."
        })
        self.assertEqual(classify_res.status_code, 200)
        cdata = classify_res.json()
        self.assertEqual(cdata["decision_type"], "MULTI_AGENT_WORKFLOW")
        self.assertEqual(cdata["workflow_name"], "business_reconnaissance")
        self.assertGreaterEqual(len(cdata["contracts"]), 3)

        # 3. Chains API
        chains_res = self.client.get("/api/router/chains")
        self.assertEqual(chains_res.status_code, 200)
        self.assertIn("chains", chains_res.json())


if __name__ == "__main__":
    unittest.main()
