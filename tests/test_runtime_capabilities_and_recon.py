"""Verification Suite for GHOST Runtime Capability Restoration & Real Venue Reconnaissance.

Verifies:
1. Central GHOST dynamic tool availability (CORE_TOOLS, GHOST_TOOLS, delegation tools).
2. OmniRouteBrain function calling tool dispatch (delegate_task, execute_workflow, query_agent_registry).
3. End-to-end real high-ticket venue reconnaissance & qualification workflow.
4. Physical disk and SQLite CRM records, SHA-256 provenance hashes, and Obsidian sync notes.
"""

import asyncio
import hashlib
import json
import os
import shutil
import unittest
from pathlib import Path

from ghost.core.brain import CORE_TOOLS, GHOST_TOOLS, get_ghost_tools, OmniRouteBrain
from ghost.core.agent_registry import get_agent_registry
from ghost.core.task_router import get_task_router, IntentCategory
from ghost.crm.db import CRMDatabase
from ghost.paths import repo_root


class TestRuntimeCapabilitiesAndRecon(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repo = repo_root()
        self.crm_db_path = os.path.join(self.repo, "data", "crm.db")
        self.obsidian_dir = os.path.expanduser("~/Obsidian Vault/Ghost/Clients")
        self.dossiers_dir = os.path.join(self.repo, "dossiers")

    def test_01_tool_exposure_and_delegation_presence(self):
        """Ensures delegation and orchestration tools are always available in CORE and GHOST suites."""
        core_names = {t["function"]["name"] for t in CORE_TOOLS}
        ghost_names = {t["function"]["name"] for t in GHOST_TOOLS}

        # Core tools must contain orchestration capabilities
        self.assertIn("delegate_task", core_names)
        self.assertIn("execute_workflow", core_names)
        self.assertIn("query_agent_registry", core_names)
        self.assertIn("check_task_status", core_names)

        # Full suite must contain specialized agency tools
        self.assertIn("crm_get_client", ghost_names)
        self.assertIn("crm_update_client", ghost_names)
        self.assertIn("crm_list_pipeline", ghost_names)
        self.assertIn("qualify_venue_lead", ghost_names)
        self.assertIn("scad_render_model", ghost_names)
        self.assertIn("ha_get_states", ghost_names)

        # Prompt inquiring about tool readiness must return full suite
        inquiry_tools = get_ghost_tools(user_text="if you have the tools now. finish it. otherwise stop and tell me")
        inquiry_names = {t["function"]["name"] for t in inquiry_tools}
        self.assertIn("delegate_task", inquiry_names)
        self.assertIn("crm_get_client", inquiry_names)
        self.assertIn("scad_render_model", inquiry_names)
        self.assertGreaterEqual(len(inquiry_tools), 50)

    async def test_02_central_tool_execution_dispatch(self):
        """Verifies Central GHOST executes delegate_task, execute_workflow, and query_agent_registry."""
        # 1. Query agent registry
        res_registry = await OmniRouteBrain.execute_tool_call("query_agent_registry", {})
        self.assertIn(res_registry.get("status"), ("ok", "success"))
        agents = res_registry.get("result", {}).get("agents", [])
        self.assertGreaterEqual(len(agents), 7)
        agent_ids = {a["agent_id"] for a in agents}
        self.assertIn("research_agent", agent_ids)
        self.assertIn("business_agent", agent_ids)
        self.assertIn("creative_agent", agent_ids)

        # 2. Check task status for non-existent task fails gracefully
        res_status = await OmniRouteBrain.execute_tool_call("check_task_status", {"task_id": "non_existent_123"})
        self.assertIn(res_status.get("status"), ("ok", "success"))
        self.assertFalse(res_status.get("result", {}).get("found", True))

    async def test_03_end_to_end_venue_recon_and_crm_obsidian_sync(self):
        """Executes full business reconnaissance workflow and verifies real disk artifacts."""
        router = get_task_router()
        decision = router.classify_and_route(
            "find high-ticket venues without website in Bengaluru and record in crm and obsidian"
        )
        self.assertEqual(decision.decision_type, "MULTI_AGENT_WORKFLOW")
        self.assertEqual(decision.workflow_name, "business_reconnaissance")
        self.assertEqual(len(decision.contracts), 3)

        events = []
        async for evt in router.dispatch_workflow(decision):
            events.append(evt)

        finished_events = [e for e in events if e.get("type") == "workflow_finished"]
        self.assertTrue(finished_events, "Workflow did not finish")
        self.assertEqual(finished_events[0].get("status"), "COMPLETED")

        # 1. Verify Research Agent Dossier Artifacts on Disk
        # Candidate venue is grasshopper or salt-stg from verified scout data
        dossier_grasshopper = os.path.join(self.dossiers_dir, "grasshopper")
        facts_path = os.path.join(dossier_grasshopper, "facts.json")
        sources_path = os.path.join(dossier_grasshopper, "SOURCES.json")

        self.assertTrue(os.path.exists(facts_path), f"Missing {facts_path}")
        self.assertTrue(os.path.exists(sources_path), f"Missing {sources_path}")

        with open(facts_path, "r", encoding="utf-8") as f:
            facts = json.load(f)
        self.assertEqual(facts.get("slug"), "grasshopper")
        self.assertFalse(facts.get("has_official_website"))
        self.assertTrue(facts.get("is_aggregator_only"))
        self.assertTrue(facts.get("pricing_verified"))
        self.assertGreaterEqual(len(facts.get("verified_photos", [])), 1)

        # Verify SHA-256 photo hash validity
        for p in facts.get("verified_photos", []):
            self.assertTrue(p.get("sha256"))
            self.assertEqual(len(p["sha256"]), 64)

        # 2. Verify SQLite CRM Database Entry
        crm = CRMDatabase.get_instance()
        lead = crm.get_lead("grasshopper")
        self.assertIsNotNone(lead, "Lead 'grasshopper' not found in CRMDatabase")
        self.assertEqual(lead.get("status"), "qualified")
        self.assertGreaterEqual(lead.get("score_total", 0), 80)

        # 3. Verify Obsidian Vault Markdown Sync Note
        obs_file = os.path.join(self.obsidian_dir, "grasshopper.md")
        self.assertTrue(os.path.exists(obs_file), f"Obsidian file missing: {obs_file}")
        with open(obs_file, "r", encoding="utf-8") as f:
            obs_content = f.read()
        self.assertIn("Grasshopper — Client Dossier", obs_content)
        self.assertIn("QUALIFIED LEAD", obs_content)
        self.assertIn("Research & Provenance Evidence", obs_content)

    def test_04_capability_discovery_query_reporting(self):
        """Verifies capability query categorizes statuses: READY, UNCONFIGURED, UNAVAILABLE."""
        router = get_task_router()
        decision = router.classify_and_route("which tools of yours work/dont work?")
        self.assertEqual(decision.intent_category, IntentCategory.CAPABILITY_QUERY)
        self.assertIsNotNone(decision.capability_explanation)

        expl = decision.capability_explanation
        domains = {d["domain"]: d for d in expl.get("domains", [])}

        # Check research domain
        self.assertIn("research", domains)
        res_tools = {t["tool_id"]: t for t in domains["research"]["tools"]}
        self.assertIn("web_search", res_tools)
        self.assertEqual(res_tools["web_search"]["status"], "READY")

        # Check creative domain (OpenSCAD is installed)
        self.assertIn("creative", domains)
        cad_tools = {t["tool_id"]: t for t in domains["creative"]["tools"]}
        self.assertIn("scad_render_model", cad_tools)
        self.assertEqual(cad_tools["scad_render_model"]["status"], "READY")

        # Check physical domain (HomeAssistant token is not set in test environment)
        self.assertIn("physical", domains)
        phys_tools = {t["tool_id"]: t for t in domains["physical"]["tools"]}
        self.assertIn("ha_get_states", phys_tools)
        self.assertEqual(phys_tools["ha_get_states"]["status"], "UNCONFIGURED")


if __name__ == "__main__":
    unittest.main()
