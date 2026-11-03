"""Outbound Copy & Persona Firewall Purity Tests."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from ghost.workers.demo_state_machine import AutonomousDemoStateMachine, sanitize_outbound


class TestOutboundPurity(unittest.TestCase):

    def test_sanitize_outbound_rejects_banned_terms(self):
        """Rejects all internal persona terms, roleplay words, and local file paths."""
        banned_examples = [
            "Regards, Ghost • Chief of Staff",
            "Task Force 141 Web Agency",
            "Ghost Deep-Strike Intelligence Engine",
            "Under Commander orders",
            "Here is the demo: file:///home/pranc/projects/ghost/demos/index.html",
            "Stored at /home/pranc/file.txt",
            "Yes sir, right away",
            "Thanks mate",
            "All done boss",
            "Standard $1,000 engagement fee"
        ]
        for example in banned_examples:
            with self.assertRaises(ValueError, msg=f"Should have rejected '{example}'"):
                sanitize_outbound(example)

    def test_sanitize_outbound_accepts_clean_copy(self):
        """Accepts professional, clean client-facing copy."""
        clean_text = "Hi Topaz Detailing team, check out this interactive mobile preview: https://topaz-concept.vercel.app\n\nBest regards,\nPranav"
        res = sanitize_outbound(clean_text)
        self.assertEqual(res, clean_text)

    def test_phase5_stops_without_hosted_url(self):
        """When no hosted URL is available, Phase 5 halts at status 'needs_deploy' with no outreach."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="Test Garage",
                    location="London, UK",
                    slug="test-garage"
                )
                sm.brand_data = {
                    "business_name": "Test Garage",
                    "location": "London, UK",
                    "domain": "home_trade_services",
                    "archetype_id": 5,
                    "archetype_name": "Clean Service & Modern Precision"
                }

                # Mock Vercel deployer returning no token / no URL
                with patch("ghost.workers.deploy.VercelDeployer.deploy_demo", new_callable=AsyncMock) as m_deploy:
                    m_deploy.return_value = {"status": "config_required", "url": None}

                    res = asyncio.run(sm.run_phase5_preview_handover())
                    self.assertEqual(res["status"], "needs_deploy")
                    self.assertNotIn("pitch_draft", res)

    def test_phase5_generates_pure_outreach_when_hosted(self):
        """When hosted URL exists, Phase 5 generates outreach authored personally by Pranav with zero persona words."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="Topaz Detailing London",
                    location="London, UK",
                    slug="topaz-detailing-london"
                )
                sm.brand_data = {
                    "business_name": "Topaz Detailing at The Peninsula London",
                    "location": "London, UK",
                    "phone": "+44 20 7946 0912",
                    "email": None,
                    "domain": "home_trade_services",
                    "archetype_id": 5,
                    "archetype_name": "Clean Service & Modern Precision"
                }

                # Mock Vercel deployer returning live hosted URL
                with patch("ghost.workers.deploy.VercelDeployer.deploy_demo", new_callable=AsyncMock) as m_deploy, \
                     patch("ghost.crm.db.CRMDatabase.get_instance"):
                    m_deploy.return_value = {"status": "success", "url": "https://topaz-concept.vercel.app"}

                    res = asyncio.run(sm.run_phase5_preview_handover())
                    self.assertEqual(res["status"], "success")
                    self.assertEqual(res["live_url"], "https://topaz-concept.vercel.app")

                    pitch = res["pitch_draft"]
                    # Must be signed as Pranav
                    self.assertIn("Pranav", pitch["body"])
                    self.assertIn("Pranav", pitch["whatsapp_pitch"])
                    # Zero persona words
                    for b in ["Ghost", "Task Force 141", "Chief of Staff", "file://", "/home/"]:
                        self.assertNotIn(b, pitch["body"])
                        self.assertNotIn(b, pitch["whatsapp_pitch"])


if __name__ == "__main__":
    unittest.main()
