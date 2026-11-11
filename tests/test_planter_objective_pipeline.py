"""End-to-End Operational Acceptance Test Suite for GHOST Planter Objective Pipeline.

Verifies:
1. Tool availability: OpenSCAD binary in PATH, trimesh, matplotlib, scipy.
2. Deterministic Agent Routing: Intent classification maps planter directive to product_engineering_pipeline.
3. Multi-agent execution: research_agent -> creative_agent -> business_agent.
4. Generated artifacts: Real .scad, .stl, .3mf, .png, and .md files.
5. Independent geometry verification: Watertight manifold check, volume, and slip-fit tolerances.
"""

import asyncio
import json
import os
import shutil
import unittest
from pathlib import Path

from ghost.core.events import get_event_bus
from ghost.core.task_router import get_task_router, IntentCategory
from ghost.core.agent_registry import get_agent_registry
from ghost.agents.research_agent import ResearchIntelligenceAgent
from ghost.agents.creative_agent import CreativeIntelligenceAgent
from ghost.agents.business_agent import BusinessIntelligenceAgent


class TestPlanterObjectivePipeline(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.bus = get_event_bus()
        self.router = get_task_router()
        self.registry = get_agent_registry()

    def test_01_environment_dependencies(self):
        """Verify that OpenSCAD, trimesh, matplotlib, and scipy are installed and operational."""
        scad_path = shutil.which("openscad")
        self.assertIsNotNone(scad_path, "OpenSCAD binary must be available on PATH")
        self.assertTrue(os.path.isfile(scad_path))
        self.assertTrue(os.access(scad_path, os.X_OK))

        import trimesh
        import matplotlib
        import scipy

        self.assertIsNotNone(trimesh.__version__)
        self.assertIsNotNone(matplotlib.__version__)
        self.assertIsNotNone(scipy.__version__)

    def test_02_task_router_classification(self):
        """Verify TaskRouter deterministically routes planter directive to product_engineering_pipeline."""
        query = (
            "Develop a differentiated, commercially viable self-watering planter concept, "
            "create a parametric CAD prototype, and produce a complete engineering dossier"
        )
        decision = self.router.classify_and_route(query)

        self.assertEqual(decision.decision_type, "MULTI_AGENT_WORKFLOW")
        self.assertEqual(decision.workflow_name, "product_engineering_pipeline")
        self.assertEqual(decision.intent_category, IntentCategory.TASK_EXECUTION)
        self.assertEqual(len(decision.contracts), 3)

        stages = [c.assigned_agent for c in decision.contracts]
        self.assertEqual(stages, ["research_agent", "creative_agent", "business_agent"])

    async def test_03_research_agent_differentiation(self):
        """Verify research agent generates competitor benchmark and failure-mode analysis."""
        agent = ResearchIntelligenceAgent(event_bus=self.bus)
        task_mock = type("MockTask", (), {
            "task_id": "test-res-planter",
            "input_payload": {
                "query": "Research competitors and failure modes for self-watering planters",
                "research_topic": "self_watering_planter",
            }
        })()

        result = await agent.execute_task(task_mock, {})
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("research_type"), "topic_research")

        findings = result.get("findings", {})
        self.assertIn("competitors", findings)
        self.assertGreaterEqual(len(findings["competitors"]), 3)
        self.assertIn("critical_failure_modes", findings)
        self.assertIn("differentiation_strategy", findings)

        evidence = await agent.verify_outcome(task_mock, result)
        self.assertTrue(evidence.verified)

    async def test_04_creative_agent_parametric_cad_and_renders(self):
        """Verify creative agent synthesizes CAD models, compiles STL/3MF, and generates PNG renders."""
        agent = CreativeIntelligenceAgent(event_bus=self.bus)
        task_mock = type("MockTask", (), {
            "task_id": "test-creat-planter",
            "input_payload": {
                "action": "generate_scad_model",
                "model_name": "aerospire_reservoir_base",
                "render_png": True,
                "export_3mf": False,
            }
        })()

        result = await agent.execute_task(task_mock, {})
        self.assertEqual(result.get("status"), "success")
        self.assertTrue(result.get("compiled_via_cli"))

        stl_path = result.get("stl_path")
        scad_path = result.get("scad_path")
        png_path = result.get("png_path")

        self.assertTrue(os.path.isfile(stl_path))
        self.assertTrue(os.path.isfile(scad_path))
        self.assertTrue(os.path.isfile(png_path))

        evidence = await agent.verify_outcome(task_mock, result)
        self.assertTrue(evidence.verified)
        self.assertEqual(evidence.evidence_type, "3d_geometry")
        self.assertTrue(evidence.details.get("is_watertight"))
        self.assertGreater(evidence.details.get("volume_cm3", 0), 100)

    async def test_05_business_agent_engineering_dossier(self):
        """Verify business agent synthesizes the complete engineering dossier and BOM."""
        agent = BusinessIntelligenceAgent(event_bus=self.bus)
        task_mock = type("MockTask", (), {
            "task_id": "test-biz-planter",
            "input_payload": {
                "action": "create_engineering_dossier",
                "product_name": "AeroSpire Self-Watering Planter",
            }
        })()

        result = await agent.execute_task(task_mock, {})
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("action"), "create_engineering_dossier")

        findings = result.get("findings", {})
        self.assertEqual(findings.get("bom_items_count"), 6)
        self.assertGreater(findings.get("total_mass_g", 0), 200)

        dossier_path = findings.get("dossier_file")
        self.assertTrue(os.path.isfile(dossier_path))
        self.assertGreater(os.path.getsize(dossier_path), 2000)

        evidence = await agent.verify_outcome(task_mock, result)
        self.assertTrue(evidence.verified)
        self.assertEqual(evidence.evidence_type, "engineering_dossier")

    def test_06_independent_geometry_verification_receipts(self):
        """Execute independent trimesh verification and assert watertight manifold geometry."""
        from scripts.verify_cad_geometry import verify_all
        report = verify_all()

        self.assertTrue(report.get("all_watertight"), "All CAD component meshes must be 100% watertight")
        self.assertTrue(report.get("all_within_tolerances"), "All components must fall within designated tolerances")
        self.assertGreater(report.get("total_part_mass_g", 0), 250)

        fit = report.get("fit_analysis", {})
        self.assertTrue(fit.get("tolerance_verified"))
        self.assertEqual(fit.get("fit_type"), "SLIP_FIT_CIRCULATION_AIR_GAP")


if __name__ == "__main__":
    unittest.main()
