"""Unit tests for Specialized Domain Intelligence Agents."""

import unittest
from ghost.agents import (
    SystemIntelligenceAgent,
    DevelopmentIntelligenceAgent,
    ResearchIntelligenceAgent,
    BusinessIntelligenceAgent,
    get_available_agents,
)
from ghost.core.events import EventBus
from ghost.core.system_state import HardwareVitals, get_system_state_manager


class TestSpecializedAgents(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.bus = EventBus(db_path=":memory:")
        self.state_mgr = get_system_state_manager(bus=self.bus)
        self.state_mgr.set_vitals(HardwareVitals(
            cpu_percent=12.5,
            ram_used_mb=2048.0,
            ram_total_mb=8192.0,
            ram_percent=25.0,
            disk_free_gb=120.0,
        ))

    async def test_available_agents_registry(self):
        agents = get_available_agents()
        self.assertEqual(len(agents), 7)
        ids = {a.agent_id for a in agents}
        self.assertIn("system_agent", ids)
        self.assertIn("dev_agent", ids)
        self.assertIn("research_agent", ids)
        self.assertIn("business_agent", ids)
        self.assertIn("personal_agent", ids)
        self.assertIn("physical_agent", ids)
        self.assertIn("creative_agent", ids)

    async def test_system_agent_lifecycle(self):
        agent = SystemIntelligenceAgent(event_bus=self.bus)
        await agent.initialize()
        snapshot = self.state_mgr.get_snapshot()
        self.assertIn(agent.name, snapshot.active_agents)

        tasks = await agent.plan_task({"target": "vitals", "objective_id": "test_obj"})
        self.assertEqual(len(tasks), 1)
        
        output = await agent.execute_task(tasks[0], {})
        self.assertEqual(output["status"], "healthy")

        evidence = await agent.verify_outcome(tasks[0], output)
        self.assertTrue(evidence.verified)
        self.assertEqual(evidence.evidence_type, "readback")

        await agent.shutdown()
        snapshot2 = self.state_mgr.get_snapshot()
        self.assertNotIn(agent.name, snapshot2.active_agents)

    async def test_dev_agent_execution(self):
        agent = DevelopmentIntelligenceAgent(event_bus=self.bus)
        tasks = await agent.plan_task({"action": "inspect_git_diff"})
        output = await agent.execute_task(tasks[0], {})
        self.assertTrue(output["ok"])

        evidence = await agent.verify_outcome(tasks[0], output)
        self.assertTrue(evidence.verified)

    async def test_research_agent_execution(self):
        agent = ResearchIntelligenceAgent(event_bus=self.bus)
        tasks = await agent.plan_task({"query": "Topaz Detailing London"})
        output = await agent.execute_task(tasks[0], {})
        self.assertTrue(output["ok"])

        evidence = await agent.verify_outcome(tasks[0], output)
        self.assertTrue(evidence.verified)

    async def test_business_agent_execution(self):
        agent = BusinessIntelligenceAgent(event_bus=self.bus)
        tasks = await agent.plan_task({"action": "build_luxury_demo", "slug": "test-venue"})
        output = await agent.execute_task(tasks[0], {})
        self.assertTrue(output["ok"])

        evidence = await agent.verify_outcome(tasks[0], output)
        self.assertTrue(evidence.verified)
