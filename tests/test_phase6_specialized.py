"""Tests for Phase 6 Complete Specialized Intelligence.

Validates:
1. Registration of all 7 specialized domain intelligence agents.
2. PersonalIntelligenceAgent briefing synthesis and verification.
3. PhysicalIntelligenceAgent Home Assistant / MQTT execution and readback verification.
4. CreativeIntelligenceAgent OpenSCAD 3D STL generation and geometry verification.
5. Settable secrets expansion in operator control vault.
"""

import os
import unittest
from pathlib import Path

from ghost.agents import (
    BusinessIntelligenceAgent,
    CreativeIntelligenceAgent,
    DevelopmentIntelligenceAgent,
    PersonalIntelligenceAgent,
    PhysicalIntelligenceAgent,
    ResearchIntelligenceAgent,
    SystemIntelligenceAgent,
    get_available_agents,
)
from ghost.core import secrets
from ghost.core.events import EventBus
from ghost.core.task_orchestrator import Task, TaskStatus


class TestPhase6Specialized(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bus = EventBus(db_path=":memory:", enable_persistence=False)

    def test_all_seven_agents_registered(self):
        agents = get_available_agents()
        self.assertEqual(len(agents), 7, f"Expected 7 domain agents, found {len(agents)}")
        agent_ids = {a.agent_id for a in agents}
        expected_ids = {
            "system_agent",
            "dev_agent",
            "research_agent",
            "business_agent",
            "personal_agent",
            "physical_agent",
            "creative_agent",
        }
        self.assertEqual(agent_ids, expected_ids)

    def test_secrets_vault_operator_keys_present(self):
        self.assertIn("HOMEASSISTANT_URL", secrets.SETTABLE_SECRETS)
        self.assertIn("HOMEASSISTANT_TOKEN", secrets.SETTABLE_SECRETS)
        self.assertIn("MQTT_BROKER_HOST", secrets.SETTABLE_SECRETS)
        self.assertIn("OPENSCAD_BINARY_PATH", secrets.SETTABLE_SECRETS)
        self.assertIn("BLENDER_BINARY_PATH", secrets.SETTABLE_SECRETS)

    async def test_personal_agent_briefing_workflow(self):
        agent = PersonalIntelligenceAgent(event_bus=self.bus)
        await agent.initialize()

        plans = await agent.plan_task({"type": "daily_briefing"})
        self.assertEqual(len(plans), 1)
        task = plans[0]

        result = await agent.execute_task(task, {})
        self.assertEqual(result.get("status"), "success")
        self.assertTrue(os.path.isfile(result["briefing_path"]))

        evidence = await agent.verify_outcome(task, result)
        self.assertTrue(evidence.verified)
        self.assertEqual(evidence.evidence_type, "briefing_document")
        self.assertIn(result["briefing_path"], evidence.artifacts)

        await agent.shutdown()

    async def test_physical_agent_device_control_and_readback(self):
        agent = PhysicalIntelligenceAgent(event_bus=self.bus)
        await agent.initialize()

        # Plan inspection
        inspect_plans = await agent.plan_task({"type": "inspect_environment"})
        self.assertEqual(len(inspect_plans), 1)
        inspect_res = await agent.execute_task(inspect_plans[0], {})
        self.assertEqual(inspect_res.get("status"), "success")
        self.assertGreater(inspect_res.get("entities_count", 0), 0)

        # Plan switch toggle
        control_plans = await agent.plan_task({
            "type": "toggle_switch",
            "entity_id": "switch.desk_lamp",
            "target_state": "on",
        })
        self.assertEqual(len(control_plans), 1)
        control_res = await agent.execute_task(control_plans[0], {})
        self.assertEqual(control_res.get("status"), "success")
        self.assertEqual(control_res["readback_state"]["state"], "on")

        # Independent verification
        evidence = await agent.verify_outcome(control_plans[0], control_res)
        self.assertTrue(evidence.verified)
        self.assertEqual(evidence.evidence_type, "readback")
        self.assertEqual(evidence.details["observed_state"], "on")

        await agent.shutdown()

    async def test_creative_agent_cad_generation_and_geometry_verification(self):
        agent = CreativeIntelligenceAgent(event_bus=self.bus)
        await agent.initialize()

        plans = await agent.plan_task({
            "type": "generate_scad_model",
            "model_name": "test_enclosure",
            "parameters": {"width": 40, "length": 60, "height": 15, "wall": 2.0},
        })
        self.assertEqual(len(plans), 1)
        task = plans[0]

        result = await agent.execute_task(task, {})
        self.assertEqual(result.get("status"), "success")
        self.assertTrue(os.path.isfile(result["scad_path"]))
        self.assertTrue(os.path.isfile(result["stl_path"]))

        evidence = await agent.verify_outcome(task, result)
        self.assertTrue(evidence.verified)
        self.assertEqual(evidence.evidence_type, "3d_geometry")
        self.assertTrue(evidence.details.get("has_mesh_header"))

        await agent.shutdown()


if __name__ == "__main__":
    unittest.main()
