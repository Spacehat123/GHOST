"""Unit tests for fail-closed classification pause, CONFIRM.json, and resume() in Ghost Demo State Machine."""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from ghost.workers.demo_state_machine import AutonomousDemoStateMachine


class TestStateMachinePause(unittest.TestCase):

    def test_unknown_business_stops_after_phase1_and_writes_confirm_json(self):
        """When classification fails closed with needs_confirmation=True, execute_all_phases()
        must halt with status 'needs_confirmation', write CONFIRM.json with top 3 candidates,
        and run zero downstream phases."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="Quantum Enigma Laboratory",
                    location="London, UK",
                    slug="quantum-enigma"
                )

                mock_scout = {
                    "status": "scouted",
                    "business_name": "Quantum Enigma Laboratory",
                    "category": None,
                    "categories": [],
                    "rating": None,
                    "review_count": None,
                    "phone": None,
                    "address": "London, UK",
                    "website": None,
                    "hours": None,
                    "photos": [],
                    "reviews": []
                }

                with patch("ghost.workers.recon_scout.ReconScout.scout_business", new_callable=AsyncMock) as m_scout, \
                     patch("ghost.workers.audit_engine.PerformanceAuditor.audit_url", new_callable=AsyncMock) as m_audit, \
                     patch("ghost.mcp.bridge.WebSearchBridge.search", new_callable=AsyncMock) as m_search, \
                     patch("ghost.workers.deploy.VercelDeployer.deploy_demo", new_callable=AsyncMock) as m_deploy:

                    m_scout.return_value = mock_scout
                    m_audit.return_value = None
                    m_search.return_value = {"results": []}

                    result = asyncio.run(sm.execute_all_phases())

                    # Assert no deploy call happens before confirmation
                    m_deploy.assert_not_called()

                self.assertEqual(result["status"], "needs_confirmation")
                self.assertEqual(result["suggested_domain"], "general_services")
                self.assertTrue(sm.needs_confirmation)

                # Assert CONFIRM.json written
                confirm_path = os.path.join(tmpdir, "quantum-enigma", "CONFIRM.json")
                self.assertTrue(os.path.exists(confirm_path), "CONFIRM.json must be written upon pause")
                with open(confirm_path, "r", encoding="utf-8") as f:
                    confirm_data = json.load(f)

                self.assertEqual(confirm_data["slug"], "quantum-enigma")
                self.assertEqual(confirm_data["name"], "Quantum Enigma Laboratory")
                self.assertEqual(confirm_data["suggested_domain"], "general_services")
                self.assertIn("candidates", confirm_data)
                self.assertLessEqual(len(confirm_data["candidates"]), 3)

                # Assert downstream artifacts were NOT created
                self.assertFalse(os.path.exists(sm.html_path), "index.html must not be created when paused!")
                self.assertFalse(os.path.exists(sm.brief_path), "DESIGN_BRIEF.md must not be created when paused!")

    def test_resume_with_domain_completes_build(self):
        """AutonomousDemoStateMachine.resume(slug, domain) must load state, remove CONFIRM.json,
        and run phases 2 through 5 to completion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="Aether Health",
                    location="London, UK",
                    slug="aether-health"
                )

                mock_scout = {
                    "status": "scouted",
                    "business_name": "Aether Health",
                    "category": None,
                    "categories": [],
                    "rating": None,
                    "review_count": None,
                    "phone": None,
                    "address": "London, UK",
                    "website": None,
                    "hours": None,
                    "photos": [],
                    "reviews": []
                }

                with patch("ghost.workers.recon_scout.ReconScout.scout_business", new_callable=AsyncMock) as m_scout, \
                     patch("ghost.workers.audit_engine.PerformanceAuditor.audit_url", new_callable=AsyncMock) as m_audit, \
                     patch("ghost.mcp.bridge.WebSearchBridge.search", new_callable=AsyncMock) as m_search:

                    m_scout.return_value = mock_scout
                    m_audit.return_value = None
                    m_search.return_value = {"results": []}

                    # Initial run pauses
                    initial_result = asyncio.run(sm.execute_all_phases())
                    self.assertEqual(initial_result["status"], "needs_confirmation")

                confirm_path = os.path.join(tmpdir, "aether-health", "CONFIRM.json")
                self.assertTrue(os.path.exists(confirm_path))

                # Operator resumes with confirmed domain
                with patch("ghost.workers.deploy.VercelDeployer.deploy_demo", new_callable=AsyncMock) as m_deploy:
                    m_deploy.return_value = {
                        "status": "config_required",
                        "url": None,
                        "internal_preview_path": f"file://{tmpdir}/aether-health/index.html"
                    }

                    resume_result = asyncio.run(AutonomousDemoStateMachine.resume("aether-health", "dental_medical"))

                # CONFIRM.json must be removed upon resumption
                self.assertFalse(os.path.exists(confirm_path), "CONFIRM.json must be deleted after resume()")

                # Downstream artifacts must now exist
                html_path = os.path.join(tmpdir, "aether-health", "index.html")
                brief_path = os.path.join(tmpdir, "aether-health", "DESIGN_BRIEF.md")
                self.assertTrue(os.path.exists(html_path))
                self.assertTrue(os.path.exists(brief_path))

                with open(brief_path, "r", encoding="utf-8") as f:
                    brief_text = f.read()
                self.assertIn("dental_medical", brief_text)

                self.assertIn(resume_result["status"], ["needs_deploy", "completed", "deployed"])

    def test_direct_call_to_phase2_raises_runtime_error_when_unconfirmed(self):
        """Calling run_phase2_design_brief directly while needs_confirmation is True must raise RuntimeError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="Aether Dynamic Corp",
                    location="London, UK",
                    slug="aether-dynamic"
                )
                sm.domain = "general_services"
                sm.confidence = 0.20
                sm.needs_confirmation = True

                with self.assertRaises(RuntimeError) as ctx:
                    asyncio.run(sm.run_phase2_design_brief())

                self.assertIn("requires operator confirmation", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
