"""
Comprehensive Test Suite: Input Integrity, Intent Categorization & Anti-Contamination
=====================================================================================
Covers Sections 6 & 7 of GHOST Deterministic Routing & Context Isolation:
1. Exact regression test reproducing the original failure:
   "Tell me which tools of yours work/dont work for doing the following ta’..."
   - Must NOT dispatch business reconnaissance workflow.
   - Must be classified as TASK_CLARIFICATION or CAPABILITY_QUERY.
   - Must not run unrelated venue research or claim false completion.
2. Short requests.
3. Long requests (> 1000 characters).
4. Multi-paragraph requests.
5. Requests with multiple requirements across domains.
6. Requests containing quoted examples.
7. Requests containing code blocks.
8. Requests with unusual punctuation and non-ASCII characters.
9. Capability discovery queries (CAPABILITY_QUERY).
10. Task planning queries without execution (TASK_PLANNING).
11. Multi-agent execution directives (TASK_EXECUTION).
12. Prompt context budgeting and paragraph-level segmentation.
13. Anti-contamination and relevance verification:
    - Research Agent does not produce Grasshopper for general competitor research.
    - Business Agent does not scavenge stale dossiers from filesystem.
    - Orchestrator rejects mismatched output with INVALID_DISPATCH / FAILED.
"""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from ghost.agents.business_agent import BusinessIntelligenceAgent
from ghost.agents.research_agent import ResearchIntelligenceAgent
from ghost.core.agent import GhostAgent
from ghost.core.agent_registry import AgentCapabilityRegistry
from ghost.core.delegation_contract import DelegationTaskContract
from ghost.core.events import EventBus
from ghost.core.input_integrity import (
    InputPayload,
    budget_prompt_tokens,
    estimate_tokens,
    validate_input_integrity,
)
from ghost.core.task_orchestrator import Task, TaskOrchestrator, TaskStatus
from ghost.core.task_router import IntentCategory, RoutingDecision, TaskRouter
from ghost.crm.db import CRMDatabase


class TestInputIntegrityAndRouting(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_integrity.db")
        self.bus = EventBus(db_path=self.db_path)
        self.agent_reg = AgentCapabilityRegistry()
        self.orchestrator = TaskOrchestrator(db_path=self.db_path, event_bus=self.bus)
        self.router = TaskRouter(
            agent_registry=self.agent_reg,
            task_orchestrator=self.orchestrator,
            event_bus=self.bus,
        )

    async def asyncTearDown(self):
        self.tmp_dir.cleanup()

    # =========================================================================
    # 1. Exact Regression Test Reproducing the Original Failure
    # =========================================================================
    async def test_reproduce_original_failure_regression(self):
        """
        Original failure: User submitted:
        'Tell me which tools of yours work/dont work for doing the following ta’...'
        GHOST dispatched truncated text to Research Agent, ran venue recon,
        and returned a false success claiming 1 venue was verified.

        Expected Fix:
        - Must detect severed token / incomplete prompt -> TASK_CLARIFICATION or CAPABILITY_QUERY.
        - Must NOT dispatch business_reconnaissance.
        - Must NOT produce Grasshopper or write to CRM.
        - Zero false task completions.
        """
        prompt = "Tell me which tools of yours work/dont work for doing the following ta’..."
        decision = self.router.classify_and_route(prompt)

        # 1. Must be classified as TASK_CLARIFICATION (due to severed token) or CAPABILITY_QUERY
        self.assertIn(
            decision.intent_category,
            (IntentCategory.TASK_CLARIFICATION, IntentCategory.CAPABILITY_QUERY),
            f"Expected clarification or capability query, got {decision.intent_category}",
        )
        self.assertNotEqual(decision.decision_type, "MULTI_AGENT_WORKFLOW")
        self.assertNotEqual(decision.workflow_name, "business_reconnaissance")
        self.assertEqual(len(decision.contracts), 0, "No contracts must be dispatched!")

        # 2. Test GhostAgent ask_stream behavior: must NOT run research_agent or venue reconnaissance
        agent = GhostAgent()
        tokens = []
        async for chunk in agent.ask_stream(prompt):
            if chunk.get("type") == "token":
                tokens.append(chunk["text"])

        output_text = "".join(tokens)
        self.assertNotIn("Grasshopper", output_text)
        self.assertNotIn("business_reconnaissance", output_text)
        self.assertIn("CLARIFICATION REQUIRED", output_text)

        # 3. Verify zero tasks in orchestrator
        tasks = self.orchestrator.list_tasks()
        self.assertEqual(len(tasks), 0, "No execution tasks should be submitted to TaskOrchestrator")

    # =========================================================================
    # 2. Input Integrity: Short, Long, Multi-paragraph, and Budgeting
    # =========================================================================
    def test_input_preservation_and_token_budgeting(self):
        # Short request
        short = "What is the status of the CPU?"
        payload_short = validate_input_integrity(short)
        self.assertFalse(payload_short.is_incomplete)
        self.assertEqual(payload_short.raw_text, short)

        # Long request (> 1000 characters)
        long_req = (
            "We have an architectural review across our multi-service cluster. "
            "Requirement A: Check host vitals and ensure memory usage is sub-80%. "
            "Requirement B: Run unit test suites for all backend modules and report diffs. "
            "Requirement C: Inspect smart home desk sensors via Home Assistant. "
            + ("Additional constraints and parameters for the deployment pipeline. " * 30)
        )
        self.assertGreater(len(long_req), 1000)
        payload_long = validate_input_integrity(long_req)
        self.assertEqual(payload_long.raw_text, long_req)
        self.assertEqual(payload_long.length_chars, len(long_req))
        self.assertFalse(payload_long.is_incomplete)

        # Multi-paragraph request
        multi_para = (
            "Paragraph 1: Initial context and background for the task.\n\n"
            "Paragraph 2: Second section detailing explicit constraints.\n\n"
            "Paragraph 3: Final directive for execution."
        )
        payload_multi = validate_input_integrity(multi_para)
        self.assertEqual(payload_multi.raw_text, multi_para)
        self.assertFalse(payload_multi.is_incomplete)

        # Token budgeting and segmentation when exceeding limit
        huge_text = "\n\n".join([f"Paragraph {i}: Detailed instructions " + ("text " * 100) for i in range(25)])
        segmented, was_seg, log_msg = budget_prompt_tokens(huge_text, max_budget=1000, reserve_tokens=200)
        self.assertTrue(was_seg)
        self.assertIn("GHOST Context Budgeting", segmented)
        self.assertLess(estimate_tokens(segmented), 1000)

    # =========================================================================
    # 3. Unusual Punctuation, Code Blocks, and Quoted Text Isolation
    # =========================================================================
    def test_quoted_examples_and_code_blocks_do_not_trigger_execution(self):
        """
        User quotes a workflow command inside an inquiry:
        'I heard someone mention "Find high-ticket venues and add to CRM", but which of your tools actually do that?'
        Must be classified as CAPABILITY_QUERY, NOT execute business_reconnaissance.
        """
        quoted_query = 'I heard someone mention "Find high-ticket venues lacking official digital presence and add to CRM", but which of your tools actually do that?'
        decision = self.router.classify_and_route(quoted_query)

        self.assertEqual(decision.intent_category, IntentCategory.CAPABILITY_QUERY)
        self.assertEqual(len(decision.contracts), 0)

        # Request containing python code block
        code_query = (
            "Here is my test script:\n"
            "```python\n"
            "def test_venues():\n"
            "    find_high_ticket_venues(crm='obsidian')\n"
            "```\n"
            "Tell me what tools you have to run this code."
        )
        decision_code = self.router.classify_and_route(code_query)
        self.assertEqual(decision_code.intent_category, IntentCategory.CAPABILITY_QUERY)
        self.assertEqual(len(decision_code.contracts), 0)

        # Unusual punctuation and non-ASCII characters
        unicode_query = "What tools can check vitals & hardware — e.g. CPU/RAM? (temperature: 45°C, naïve check)"
        payload_uni = validate_input_integrity(unicode_query)
        self.assertFalse(payload_uni.is_incomplete)
        self.assertEqual(payload_uni.raw_text, unicode_query)

    # =========================================================================
    # 4. Capability Discovery Queries (CAPABILITY_QUERY)
    # =========================================================================
    def test_capability_discovery_queries(self):
        queries = [
            "Which of your tools can research competitors and generate CAD models?",
            "What tools do you have for smart home automation?",
            "Tell me which tools of yours work or dont work for git repository inspection.",
            "What can you do with 3D CAD modeling and OpenSCAD?",
            "List your available specialized tools and their operational status.",
        ]

        for q in queries:
            decision = self.router.classify_and_route(q)
            self.assertEqual(
                decision.intent_category,
                IntentCategory.CAPABILITY_QUERY,
                f"Failed capability classification for: {q}",
            )
            self.assertEqual(len(decision.contracts), 0, "Capability query must never create execution contracts")
            self.assertIsNotNone(decision.capability_explanation)
            self.assertGreater(decision.capability_explanation.get("total_relevant_agents", 0), 0)

    # =========================================================================
    # 5. Task Planning Queries (TASK_PLANNING)
    # =========================================================================
    def test_task_planning_without_execution(self):
        queries = [
            "Give me a step-by-step plan to research competitors and render a 3D CAD model without executing it.",
            "How would you accomplish a full hardware diagnostic and zombie cleanup? Draft a plan only.",
            "Outline the steps for setting up a focus session and smart lighting scene.",
        ]

        for q in queries:
            decision = self.router.classify_and_route(q)
            self.assertEqual(
                decision.intent_category,
                IntentCategory.TASK_PLANNING,
                f"Failed planning classification for: {q}",
            )
            self.assertEqual(len(decision.contracts), 0, "Planning query must never create execution contracts")
            self.assertIsNotNone(decision.execution_plan)
            self.assertGreater(decision.execution_plan.get("total_stages", 0), 0)

    # =========================================================================
    # 6. Explicit Execution Directives (TASK_EXECUTION)
    # =========================================================================
    def test_imperative_execution_directives(self):
        test_cases = [
            (
                "Find high-ticket venues lacking an official digital presence, verify their pricing and aggregator status, collect authentic media, and prepare qualified dossiers for my CRM and Obsidian ledger.",
                "MULTI_AGENT_WORKFLOW",
                "business_reconnaissance",
            ),
            (
                "Investigate test failure in tests/test_agent.py and check system vitals for leaks",
                "MULTI_AGENT_WORKFLOW",
                "dev_incident_investigation",
            ),
            (
                "Search the web for competitor pricing models and scrape meta tags",
                "SINGLE_AGENT",
                "research_agent",
            ),
            (
                "Turn off the desk lamp using Home Assistant and verify state",
                "SINGLE_AGENT",
                "physical_agent",
            ),
        ]

        for query, expected_type, expected_target in test_cases:
            decision = self.router.classify_and_route(query)
            self.assertEqual(decision.intent_category, IntentCategory.TASK_EXECUTION)
            self.assertEqual(decision.decision_type, expected_type)
            if expected_type == "MULTI_AGENT_WORKFLOW":
                self.assertEqual(decision.workflow_name, expected_target)
            else:
                self.assertEqual(decision.target_agent, expected_target)
            self.assertGreater(len(decision.contracts), 0)
            # Verify authoritative user request is attached to contract
            self.assertEqual(decision.contracts[0].raw_user_request, query)

    # =========================================================================
    # 7. Anti-Contamination: Zero Mismatched Cached Files or False Completions
    # =========================================================================
    async def test_anti_contamination_research_agent(self):
        """
        Research Agent must NOT return Grasshopper when asked for general competitor research.
        """
        agent = ResearchIntelligenceAgent(event_bus=self.bus)
        task = Task(
            task_id="task_test_research_comp",
            objective_id="obj_comp",
            assigned_agent="research_agent",
            title="Competitor research on autonomous delivery robots",
            description="Gather competitor pricing and features",
            status=TaskStatus.RUNNING,
            input_payload={"query": "autonomous delivery robots competitors", "verify_aggregators": False},
        )

        output = await agent.execute_task(task, {})
        self.assertEqual(output.get("research_type"), "topic_research")
        self.assertNotIn("Grasshopper", str(output))
        self.assertEqual(output["findings"]["topic"], "autonomous delivery robots competitors")

        # Independent verification must verify topic, not venue
        evidence = await agent.verify_outcome(task, output)
        self.assertTrue(evidence.verified)
        self.assertEqual(evidence.evidence_type, "topic_research_provenance")

    async def test_anti_contamination_business_agent_no_stale_scavenging(self):
        """
        Business Agent must NOT scavenge stale dossiers on disk when task has zero input leads.
        """
        agent = BusinessIntelligenceAgent(event_bus=self.bus)
        task = Task(
            task_id="task_test_biz_empty",
            objective_id="obj_biz",
            assigned_agent="business_agent",
            title="Qualify prospect without leads provided",
            description="Qualify prospect",
            status=TaskStatus.RUNNING,
            input_payload={"action": "qualify_and_record_crm"},  # venues omitted!
        )

        output = await agent.execute_task(task, {})
        self.assertFalse(output["ok"])
        self.assertTrue(output.get("blocked"))
        self.assertIn("No candidate leads provided", output.get("failure_details", ""))

        evidence = await agent.verify_outcome(task, output)
        self.assertFalse(evidence.verified)

    async def test_orchestrator_rejects_mismatched_output(self):
        """
        Orchestrator rejects stage outputs where output does not match contract objective.
        """
        contract = DelegationTaskContract(
            task_id="task_stage_dev",
            assigned_agent="dev_agent",
            domain="development",
            objective="Diagnose test failures in test_core.py",
            inputs={"query": "test failures"},
        )

        # Simulated contaminated output returning venues
        fake_contaminated_output = {
            "ok": True,
            "venues": [{"name": "Grasshopper", "slug": "grasshopper"}],
        }
        fake_evidence = MagicMock()
        fake_evidence.verified = True
        fake_evidence.artifacts = []
        fake_evidence.details = {}

        valid, err = self.router._validate_stage_receipt(contract, fake_contaminated_output, fake_evidence, time.time())
        self.assertFalse(valid)
        self.assertIn("INVALID_DISPATCH", err)


if __name__ == "__main__":
    unittest.main()
