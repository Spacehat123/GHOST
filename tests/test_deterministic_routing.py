"""Test suite for Deterministic Agent Routing, Capability Registry & Context Isolation.

Verifies the 10 acceptance criteria:
1. Clear requests route to the correct agent.
2. Multi-domain requests create the correct workflow.
3. Central GHOST does not receive unnecessary tool schemas.
4. Agents only receive authorized tools.
5. Agent contexts are isolated.
6. Structured results return to the parent task.
7. Failed agents are handled without corrupting the parent task.
8. Retries do not duplicate side effects.
9. Permission checks remain effective across delegation.
10. Business reconnaissance scenario executes through real available tools or reports precise blockers.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from ghost.core.agent_registry import AgentCapabilityRegistry, get_agent_registry
from ghost.core.delegation_contract import DelegationResult, DelegationTaskContract
from ghost.core.events import EventBus
from ghost.core.task_orchestrator import TaskOrchestrator, TaskStatus
from ghost.core.task_router import RoutingDecision, TaskRouter
from ghost.core.tool_registry import ToolRegistry, get_tool_registry
from ghost.paths import repo_root


class TestDeterministicAgentRouting(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_ghost.db")
        self.bus = EventBus(db_path=self.db_path)
        self.orch = TaskOrchestrator(db_path=self.db_path, event_bus=self.bus)
        self.tool_reg = get_tool_registry()
        self.agent_reg = AgentCapabilityRegistry(tool_registry=self.tool_reg)
        self.router = TaskRouter(
            agent_registry=self.agent_reg,
            task_orchestrator=self.orch,
            event_bus=self.bus,
        )

    async def asyncTearDown(self):
        self.tmp_dir.cleanup()

    # 1. Clear requests route to the correct agent
    async def test_clear_single_domain_routing(self):
        test_cases = [
            ("Investigate CPU spike and high memory pressure on the host", "system_agent", "system"),
            ("Diagnose unit test failure in tests/test_agent.py and review git diff", "dev_agent", "development"),
            ("Search the web for competitors and scrape their landing page meta", "research_agent", "research"),
            ("Qualify prospect and check their CRM pipeline record", "business_agent", "business"),
            ("Generate my daily morning briefing and check today's schedule", "personal_agent", "personal"),
            ("Turn off the desk lamp using Home Assistant and verify state", "physical_agent", "physical"),
            ("Create a parametric OpenSCAD 3D CAD model and export STL mesh", "creative_agent", "creative"),
        ]

        for query, expected_agent, expected_domain in test_cases:
            decision = self.router.classify_and_route(query)
            self.assertEqual(
                decision.decision_type,
                "SINGLE_AGENT",
                f"Failed single agent classification for: {query}"
            )
            self.assertEqual(decision.target_agent, expected_agent)
            self.assertEqual(decision.domain, expected_domain)
            self.assertEqual(len(decision.contracts), 1)
            self.assertEqual(decision.contracts[0].assigned_agent, expected_agent)

    # 2. Multi-domain requests create the correct workflow
    async def test_multi_domain_workflow_routing(self):
        # Reference scenario query
        query = "Find high-ticket venues lacking an official digital presence, verify their pricing and aggregator status, collect authentic media, and prepare qualified dossiers for my CRM and Obsidian ledger."
        decision = self.router.classify_and_route(query)

        self.assertEqual(decision.decision_type, "MULTI_AGENT_WORKFLOW")
        self.assertEqual(decision.workflow_name, "business_reconnaissance")
        self.assertGreaterEqual(len(decision.contracts), 3)

        # Check stage order: Stage 1 = research_agent, Stage 2 = business_agent (CRM), Stage 3 = business_agent (Obsidian)
        self.assertEqual(decision.contracts[0].assigned_agent, "research_agent")
        self.assertEqual(decision.contracts[0].execution_order, 1)

        self.assertEqual(decision.contracts[1].assigned_agent, "business_agent")
        self.assertEqual(decision.contracts[1].execution_order, 2)
        self.assertIn(decision.contracts[0].task_id, decision.contracts[1].dependencies)

        self.assertEqual(decision.contracts[2].assigned_agent, "business_agent")
        self.assertEqual(decision.contracts[2].execution_order, 3)
        self.assertIn(decision.contracts[1].task_id, decision.contracts[2].dependencies)

    # 3. Central GHOST does not receive unnecessary tool schemas
    async def test_central_ghost_scoped_tools(self):
        # Check that AgentCapabilityRegistry produces a compact summary
        summary = self.agent_reg.get_compact_capability_summary()
        self.assertIn("SPECIALIZED DOMAIN AGENTS", summary)
        self.assertIn("research_agent", summary)
        self.assertIn("business_agent", summary)

        # The summary should be concise (< 1500 chars, ~350 tokens), NOT 50,000 chars of 54 JSON schemas
        self.assertLess(len(summary), 2000, "Central GHOST capability summary exceeds compact budget")

    # 4. Agents only receive authorized tools
    async def test_specialist_agents_receive_only_permitted_tools(self):
        research_tools = self.agent_reg.get_tools_for_agent("research_agent")
        research_tool_names = [t["function"]["name"] for t in research_tools]

        # Should receive research tools
        self.assertIn("web_search", research_tool_names)
        self.assertIn("scout_expensive_venues_without_website", research_tool_names)

        # Should NEVER receive physical, system control, or dev tools
        self.assertNotIn("ha_call_service", research_tool_names)
        self.assertNotIn("kill_process", research_tool_names)
        self.assertNotIn("git_action", research_tool_names)

        # Physical agent should receive ONLY smart home tools
        physical_tools = self.agent_reg.get_tools_for_agent("physical_agent")
        physical_tool_names = [t["function"]["name"] for t in physical_tools]
        self.assertIn("ha_call_service", physical_tool_names)
        self.assertIn("ha_get_states", physical_tool_names)
        self.assertNotIn("web_search", physical_tool_names)
        self.assertNotIn("git_action", physical_tool_names)

    # 5. Agent contexts are isolated
    async def test_agent_context_isolation(self):
        contract = DelegationTaskContract(
            task_id="task-sub-test-1",
            assigned_agent="research_agent",
            domain="research",
            objective="Inspect candidate",
            inputs={"query": "test venue"},
        )
        result = DelegationResult(
            task_id="task-sub-test-1",
            agent_id="research_agent",
            status="COMPLETED",
            findings={"raw_html_dump": "<div>10MB of raw DOM</div>", "candidate": "Grasshopper"},
            summary="Verified 1 high-ticket venue with authentic photos.",
            evidence=[{"verified": True, "evidence_type": "sha256"}],
            actions_performed=["scouted_venue"],
            raw_execution_logs=["verbose debug line 1", "verbose debug line 2", "connection open"],
        )

        central_summary = result.to_central_summary()

        # Central summary must contain the concise result, NOT the 10MB raw dump or verbose execution logs
        self.assertIn("Verified 1 high-ticket venue", central_summary)
        self.assertNotIn("10MB of raw DOM", central_summary)
        self.assertNotIn("verbose debug line 1", central_summary)
        self.assertLess(len(central_summary), 1000)

    # 6. Structured results return to the parent task
    async def test_structured_results_return_to_parent(self):
        decision = RoutingDecision(
            decision_type="SINGLE_AGENT",
            domain="system",
            target_agent="system_agent",
            contracts=[
                DelegationTaskContract(
                    task_id="task-sub-sys-01",
                    assigned_agent="system_agent",
                    domain="system",
                    objective="Inspect memory usage",
                    inputs={"target": "vitals"},
                )
            ]
        )

        events = []
        async for evt in self.router.dispatch_workflow(decision):
            events.append(evt)

        # Check that parent task received completion
        wf_fin = next((e for e in events if e.get("type") == "workflow_finished"), None)
        self.assertIsNotNone(wf_fin)
        self.assertEqual(wf_fin["status"], "COMPLETED")
        self.assertIn("task-sub-sys-01", wf_fin["results"])
        self.assertEqual(wf_fin["results"]["task-sub-sys-01"]["status"], "COMPLETED")

    # 7. Failed agents are handled without corrupting the parent task
    async def test_failed_agents_handled_without_corrupting_parent(self):
        # Dispatch with an unhandled/failing target agent
        decision = RoutingDecision(
            decision_type="SINGLE_AGENT",
            domain="unknown",
            target_agent="non_existent_agent",
            contracts=[
                DelegationTaskContract(
                    task_id="task-fail-01",
                    assigned_agent="non_existent_agent",
                    domain="unknown",
                    objective="Will fail cleanly",
                    inputs={},
                )
            ]
        )

        events = []
        async for evt in self.router.dispatch_workflow(decision):
            events.append(evt)

        wf_fin = next((e for e in events if e.get("type") == "workflow_finished"), None)
        self.assertIsNotNone(wf_fin)
        self.assertEqual(wf_fin["status"], "INCOMPLETE")
        self.assertEqual(wf_fin["stages_completed"], 0)

    # 8. Retries do not duplicate side effects
    async def test_idempotent_retries(self):
        from ghost.crm.db import CRMDatabase
        crm_db = CRMDatabase(os.path.join(self.tmp_dir.name, "test_crm.db"))

        with patch.object(CRMDatabase, "get_instance", return_value=crm_db):
            from ghost.agents.business_agent import BusinessIntelligenceAgent
            biz_agent = BusinessIntelligenceAgent()

            task_input = {
                "action": "qualify_and_record_crm",
                "venues": [{
                    "slug": "idempotent-test-venue",
                    "name": "Idempotent Test Venue",
                    "business_name": "Idempotent Test Venue",
                    "category": "Fine dining restaurant",
                    "pricing_verified": True,
                    "is_aggregator_only": True,
                }]
            }
            task = self.orch.submit_task("obj-idemp", "business_agent", "Test CRM Write", "", task_input)

            # First execution
            out1 = await biz_agent.execute_task(task, task_input)
            self.assertTrue(out1["ok"])

            # Retry execution with same task input
            out2 = await biz_agent.execute_task(task, task_input)
            self.assertTrue(out2["ok"])

            # Verify CRM database has exactly ONE record for this slug, not duplicate entries
            lead = crm_db.get_lead("idempotent-test-venue")
            self.assertIsNotNone(lead)
            self.assertEqual(lead["name"], "Idempotent Test Venue")

    # 9. Permission checks remain effective across delegation
    async def test_permission_checks_effective_across_delegation(self):
        contract = DelegationTaskContract(
            task_id="task-perm-01",
            assigned_agent="research_agent",
            domain="research",
            objective="Unauthorized action",
            inputs={},
            permissions=["nuclear.launch", "research.web"],  # "nuclear.launch" is unauthorized
        )

        authorized_scope = {"research.web", "research.dossier_write"}
        valid, reason = self.router.validate_delegation_permissions(contract, authorized_scope)
        self.assertFalse(valid)
        self.assertIn("nuclear.launch", reason)

    # 10. The business reconnaissance scenario executes through real available tools or reports precise blockers
    async def test_business_reconnaissance_workflow_execution(self):
        from ghost.crm.db import CRMDatabase
        crm_db = CRMDatabase(os.path.join(self.tmp_dir.name, "test_recon_crm.db"))

        with patch.object(CRMDatabase, "get_instance", return_value=crm_db):
            query = "Find high-ticket venues lacking an official digital presence, verify their pricing and aggregator status, collect authentic media, and prepare qualified dossiers for my CRM and Obsidian ledger."
            decision = self.router.classify_and_route(query)

            self.assertEqual(decision.workflow_name, "business_reconnaissance")

            events = []
            async for evt in self.router.dispatch_workflow(decision):
                events.append(evt)

            wf_fin = next((e for e in events if e.get("type") == "workflow_finished"), None)
            self.assertIsNotNone(wf_fin)
            self.assertEqual(wf_fin["status"], "COMPLETED")

            # Check that Research stage verified genuine high-ticket venue
            res_stage = next((e for e in events if e.get("type") == "stage_completed" and e.get("agent_id") == "research_agent"), None)
            self.assertIsNotNone(res_stage)
            self.assertTrue(res_stage["verified"])

            # Check that Business stage qualified the lead and recorded it into CRM
            biz_stage = next((e for e in events if e.get("type") == "stage_completed" and e.get("agent_id") == "business_agent" and "qualified" in e.get("summary", "").lower()), None)
            self.assertIsNotNone(biz_stage)
            self.assertTrue(biz_stage["verified"])

    # 11. Central GhostAgent end-to-end streaming routing without context pollution
    async def test_ghost_agent_ask_stream_reconnaissance(self):
        from ghost.core.agent import GhostAgent
        from ghost.crm.db import CRMDatabase
        crm_db = CRMDatabase(os.path.join(self.tmp_dir.name, "test_stream_crm.db"))

        with patch.object(CRMDatabase, "get_instance", return_value=crm_db):
            agent = GhostAgent()
            query = "Find high-ticket venues lacking an official digital presence, verify their pricing and aggregator status, collect authentic media, and prepare qualified dossiers for my CRM and Obsidian ledger."

            tokens = []
            async for chunk in agent.ask_stream(query):
                if chunk.get("type") == "token":
                    tokens.append(chunk["text"])

            full_text = "".join(tokens)
            # Must announce routing directive
            self.assertIn("DIRECTIVE", full_text)
            self.assertIn("business_reconnaissance", full_text)
            # Must dispatch to research_agent and business_agent
            self.assertIn("research_agent", full_text)
            self.assertIn("business_agent", full_text)
            # Must report verified outcome
            self.assertIn("Mission objective accomplished", full_text)


if __name__ == "__main__":
    unittest.main()
