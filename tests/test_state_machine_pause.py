"""Unit tests for fail-closed classification pause and authorization gating in Ghost Demo State Machine."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from ghost.workers.demo_state_machine import AutonomousDemoStateMachine


class TestStateMachinePause(unittest.TestCase):

    def test_unconfirmed_classification_halts_pipeline_on_deny(self):
        """When classification fails closed to general_services with needs_confirmation=True,
        execute_all_phases() must request authorization and halt with status 'needs_confirmation' if denied."""
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
                     patch("ghost.workers.demo_state_machine.request_tactical_authorization", new_callable=AsyncMock) as m_auth:

                    m_scout.return_value = mock_scout
                    m_audit.return_value = None
                    m_search.return_value = {"results": []}
                    m_auth.return_value = False  # Operator does not authorize unconfirmed build

                    result = asyncio.run(sm.execute_all_phases())

                self.assertEqual(result["status"], "needs_confirmation")
                self.assertEqual(result["domain"], "general_services")
                self.assertTrue(sm.needs_confirmation)
                self.assertFalse(os.path.exists(sm.html_path), "index.html must not be created when paused!")
                self.assertFalse(os.path.exists(sm.brief_path), "DESIGN_BRIEF.md must not be created when paused!")

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

    def test_confirm_domain_clears_pause_and_allows_build(self):
        """Operator explicitly confirming domain via confirm_domain() clears needs_confirmation and allows build."""
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

                    # Run Phase 1 -> ends with needs_confirmation=True
                    asyncio.run(sm.run_phase1_recon())

                self.assertTrue(sm.needs_confirmation)

                # Operator confirms domain
                sm.confirm_domain("dental_medical")
                self.assertFalse(sm.needs_confirmation)
                self.assertEqual(sm.domain, "dental_medical")
                self.assertEqual(sm.archetype_id, 5)

                # Now phase 2 and 3 can run cleanly without error
                brief = asyncio.run(sm.run_phase2_design_brief())
                self.assertIn("dental_medical", brief)
                html = asyncio.run(sm.run_phase3_build_sections())
                self.assertIn("Clinical Treatments", html)


if __name__ == "__main__":
    unittest.main()
