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

    def test_sanitize_outbound_allows_internal_templates_and_metadata_with_ghost(self):
        """Internal template references, serverless comments, and local repo paths do not trigger false positives."""
        # 1. PITCH_KIT markdown document with local file paths in preview assets section
        internal_doc = (
            "# PITCH KIT: Grasshopper\n\n"
            "## 1. Concrete Gap Identified\n"
            "- **Target:** Grasshopper (Bengaluru)\n"
            "- **Hosted Concept URL:** https://grasshopper-concept.vercel.app\n\n"
            "## 2. Preview Assets\n"
            "- **Live Preview URL:** https://grasshopper-concept.vercel.app\n"
            "- **Mobile Screenshot:** /home/pranc/projects/ghost/demos/grasshopper/mobile_390x844.jpg\n"
            "- **Desktop Screenshot:** /home/pranc/projects/ghost/demos/grasshopper/desktop_1440x900.jpg\n\n"
            "## 3. Direct Outreach Scripts\n"
            "### WhatsApp:\n"
            "```text\n"
            "Hi Grasshopper team, noticed your mobile visitors don't have an instant booking flow.\n\n"
            "Put together an interactive preview: https://grasshopper-concept.vercel.app\n\n"
            "— Pranav\n"
            "```\n"
        )
        res_doc = sanitize_outbound(internal_doc)
        self.assertEqual(res_doc, internal_doc)

        # 2. Serverless function / internal template with comments and env variables
        serverless_code = (
            "// Serverless Contact Form Endpoint for Vercel\n"
            "// Generated by Ghost for grasshopper\n"
            "const webhookUrl = process.env.GHOST_CRM_WEBHOOK_URL;\n"
            "module.exports = async (req, res) => {\n"
            "  return res.status(200).json({ ok: true });\n"
            "};\n"
        )
        res_code = sanitize_outbound(serverless_code)
        self.assertEqual(res_code, serverless_code)

        # 3. HTML template with internal comment
        html_template = (
            "<!-- Generated by Ghost demo state machine -->\n"
            "<div class=\"service-card\">\n"
            "  <h3>Degustation Dinner</h3>\n"
            "  <p>Seven-course farm-to-table culinary experience in our courtyard.</p>\n"
            "</div>\n"
        )
        res_html = sanitize_outbound(html_template)
        self.assertEqual(res_html, html_template)

    def test_sanitize_outbound_strictly_blocks_persona_in_document_outreach_blocks(self):
        """Even inside documents, prohibited persona terms in actual outreach blocks are strictly caught."""
        bad_doc = (
            "# PITCH KIT: Grasshopper\n\n"
            "## 3. Direct Outreach Scripts\n"
            "### WhatsApp:\n"
            "```text\n"
            "Hi Grasshopper team, this is Ghost from Task Force 141 sending you a concept.\n"
            "```\n"
        )
        with self.assertRaises(ValueError) as ctx:
            sanitize_outbound(bad_doc)
        self.assertIn("Persona firewall violation", str(ctx.exception))

    def test_phase5_writes_clean_asset_basenames_in_pitch_kit(self):
        """Phase 5 writes relative asset basenames rather than raw host file paths into PITCH_KIT.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="Grasshopper",
                    location="Bengaluru",
                    slug="grasshopper"
                )
                sm.brand_data = {
                    "business_name": "Grasshopper",
                    "location": "Bengaluru",
                    "phone": "080501 65130",
                    "domain": "restaurants_cafes",
                    "archetype_id": 1,
                    "archetype_name": "Michelin & Fine Dining Editorial"
                }
                sm.audit_results = {
                    "screenshots": {
                        "mobile": "/home/pranc/projects/ghost/demos/grasshopper/mobile_390x844.jpg",
                        "desktop": "/home/pranc/projects/ghost/demos/grasshopper/desktop_1440x900.jpg"
                    }
                }

                with patch("ghost.workers.deploy.VercelDeployer.deploy_demo", new_callable=AsyncMock) as m_deploy, \
                     patch("ghost.crm.db.CRMDatabase.get_instance"):
                    m_deploy.return_value = {
                        "status": "success",
                        "url": "https://grasshopper-concept.vercel.app"
                    }

                    res = asyncio.run(sm.run_phase5_preview_handover())
                    self.assertEqual(res["status"], "success")

                    pitch_kit_path = res["pitch_kit_path"]
                    self.assertTrue(os.path.exists(pitch_kit_path))
                    with open(pitch_kit_path, "r", encoding="utf-8") as f:
                        content = f.read()

                    # Must have relative asset basenames, zero /home/ or /projects/ghost/
                    self.assertIn("- **Mobile Screenshot:** mobile_390x844.jpg", content)
                    self.assertIn("- **Desktop Screenshot:** desktop_1440x900.jpg", content)
                    self.assertNotIn("/home/", content)
                    self.assertNotIn("/projects/ghost/", content)


if __name__ == "__main__":
    unittest.main()
