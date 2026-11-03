"""Claims & Domain Purity Linter for Generated Demos."""
import asyncio
import os
import re
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from ghost.workers.demo_state_machine import AutonomousDemoStateMachine


class TestClaimsLint(unittest.TestCase):

    def test_topaz_detailing_demo_contains_zero_food_or_fake_metrics(self):
        """Topaz Detailing demo must contain zero restaurant words, zero fake scores, zero rupee symbols,
        no 'Verified' stamps, and no invented services by default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="Topaz Detailing London",
                    location="The Peninsula London, 1 Grosvenor Pl, London SW1X 7HJ, UK",
                    slug="topaz-detailing-london"
                )

                # Mock scout return: automotive detailing with 1 real review and NO scraped services
                mock_scout = {
                    "status": "scouted",
                    "business_name": "Topaz Detailing at The Peninsula London",
                    "category": "Car detailing service",
                    "categories": ["Car detailing service", "Car wash"],
                    "rating": "4.9",
                    "review_count": "14",
                    "phone": "+44 20 7946 0912",
                    "address": "1 Grosvenor Pl, London SW1X 7HJ, UK",
                    "website": "https://topazdetailing.com",
                    "hours": "Monday - Saturday 08:30 - 18:00",
                    "photos": [
                        {"url": "https://lh5.googleusercontent.com/p/AF1QipNW1234=w1600-h1200-k-no", "source": "google_maps_cdn"},
                        {"url": "https://lh5.googleusercontent.com/p/AF1QipNW5678=w1600-h1200-k-no", "source": "google_maps_cdn"},
                        {"url": "https://lh5.googleusercontent.com/p/AF1QipNW9012=w1600-h1200-k-no", "source": "google_maps_cdn"},
                    ],
                    "reviews": [
                        {
                            "author": "Marcus H.",
                            "stars": "5.0★",
                            "text": "Exceptional ceramic coating on my Tesla. Flawless surface finish and pristine handover."
                        }
                    ],
                    "services": []
                }

                with patch("ghost.workers.recon_scout.ReconScout.scout_business", new_callable=AsyncMock) as m_scout, \
                     patch("ghost.workers.audit_engine.PerformanceAuditor.audit_url", new_callable=AsyncMock) as m_audit, \
                     patch("ghost.mcp.bridge.WebSearchBridge.search", new_callable=AsyncMock) as m_search:

                    m_scout.return_value = mock_scout
                    m_audit.return_value = None
                    m_search.return_value = {"results": []}

                    asyncio.run(sm.run_phase1_recon())
                    asyncio.run(sm.run_phase2_design_brief())
                    html = asyncio.run(sm.run_phase3_build_sections())

                # 1. Zero hospitality / restaurant keywords
                banned_restaurant_terms = [
                    r"\bwagyu\b",
                    r"\bsommelier\b",
                    r"\bdegustation\b",
                    r"\bcellar\b",
                    r"\btasting table\b",
                    r"\bculinary\b",
                    r"\bgastronomic\b",
                    r"\btartare\b",
                    r"\bagnolotti\b"
                ]
                for term in banned_restaurant_terms:
                    self.assertIsNone(
                        re.search(term, html, flags=re.IGNORECASE),
                        f"Found banned hospitality term '{term}' in automotive detailing demo!"
                    )

                # 2. Currency purity: Never inject ₹ (rupees) for a London UK business
                self.assertNotIn("₹", html, "Found rupee symbol '₹' in a London demo!")

                # 3. Zero fake performance delta cards or fake scores
                self.assertNotIn("Score: 99/100", html)
                self.assertNotIn("0.38s", html)
                self.assertNotIn("4K curated CDN media", html)
                self.assertNotIn("reservation sync", html)

                # 4. Remove fake 'Verified': Banned from generated HTML
                self.assertIsNone(
                    re.search(r"\bVerified\b", html),
                    "Found banned term 'Verified' in generated HTML output!"
                )
                self.assertIn("What customers say", html)

                # 5. No invented services: when show_sample_services=False (default), omit services section
                self.assertNotIn('id="services"', html, "Services section must be omitted when no services scraped and show_sample_services=False")

                # Ledger records verification state accurately
                ledger_path = os.path.join(sm.out_dir, "SOURCES.json")
                self.assertTrue(os.path.exists(ledger_path))
                import json
                with open(ledger_path, "r", encoding="utf-8") as f:
                    ledger_data = json.load(f)
                self.assertFalse(ledger_data["services"]["verified"])

                # 6. Single review must NOT be duplicated into an infinite marquee loop
                self.assertNotIn("marquee-track", html, "Marquee track should not be rendered for a single review!")
                self.assertEqual(html.count("Exceptional ceramic coating on my Tesla"), 1)

                # 7. Business title split: primary title clamped
                self.assertIn("Topaz Detailing", html)

    def test_sample_services_block_when_switch_enabled(self):
        """When demo.show_sample_services is True and no services were scraped, render clearly labelled
        sample block titled 'Example service layout (replace with your real services)' with NO descriptions or prices."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="Topaz Detailing London",
                    location="London, UK",
                    slug="topaz-detailing-sample"
                )
                sm.show_sample_services = True

                mock_scout = {
                    "status": "scouted",
                    "business_name": "Topaz Detailing London",
                    "category": "Car detailing service",
                    "categories": ["Car detailing service"],
                    "rating": "4.9",
                    "review_count": "10",
                    "phone": "+44 20 7946 0912",
                    "address": "London, UK",
                    "website": "https://topazdetailing.com",
                    "hours": None,
                    "photos": [],
                    "reviews": [],
                    "services": []
                }

                with patch("ghost.workers.recon_scout.ReconScout.scout_business", new_callable=AsyncMock) as m_scout, \
                     patch("ghost.workers.audit_engine.PerformanceAuditor.audit_url", new_callable=AsyncMock) as m_audit, \
                     patch("ghost.mcp.bridge.WebSearchBridge.search", new_callable=AsyncMock) as m_search:

                    m_scout.return_value = mock_scout
                    m_audit.return_value = None
                    m_search.return_value = {"results": []}

                    asyncio.run(sm.run_phase1_recon())
                    asyncio.run(sm.run_phase2_design_brief())
                    html = asyncio.run(sm.run_phase3_build_sections())

                self.assertIn("Example service layout (replace with your real services)", html)
                # NO prices or old descriptions
                self.assertNotIn("Microscopic machine polishing", html)
                self.assertNotIn("Hydrophobic nano-ceramic bond", html)
                self.assertNotIn("Steam purification", html)
                self.assertNotIn("Self-healing thermoplastic", html)
                self.assertNotIn("[Inquire for quotation]", html)
                self.assertNotIn("£", html)
                self.assertNotIn("$", html)

    def test_no_old_service_descriptions_for_empty_services(self):
        """For a lead with services=[], no service description strings from the old curated assets appear in HTML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="The Royal Crown",
                    location="London, UK",
                    slug="royal-crown"
                )
                sm.show_sample_services = True

                mock_scout = {
                    "status": "scouted",
                    "business_name": "The Royal Crown Pub",
                    "category": "Pub",
                    "categories": ["Pub", "Bar"],
                    "rating": "4.5",
                    "review_count": "50",
                    "phone": None,
                    "address": "London, UK",
                    "website": None,
                    "hours": None,
                    "photos": [],
                    "reviews": [],
                    "services": []
                }

                with patch("ghost.workers.recon_scout.ReconScout.scout_business", new_callable=AsyncMock) as m_scout, \
                     patch("ghost.workers.audit_engine.PerformanceAuditor.audit_url", new_callable=AsyncMock) as m_audit, \
                     patch("ghost.mcp.bridge.WebSearchBridge.search", new_callable=AsyncMock) as m_search:

                    m_scout.return_value = mock_scout
                    m_audit.return_value = None
                    m_search.return_value = {"results": []}

                    asyncio.run(sm.run_phase1_recon())
                    asyncio.run(sm.run_phase2_design_brief())
                    html = asyncio.run(sm.run_phase3_build_sections())

                banned_old_strings = [
                    "Multi-course seasonal progression",
                    "Curated lunch offerings prepared fresh daily",
                    "Small-batch botanical infusions",
                    "Microscopic machine polishing",
                    "Digital diagnostic imaging",
                    "Architectural hair styling",
                    "Initial movement screen"
                ]
                for s in banned_old_strings:
                    self.assertNotIn(s, html)

    def test_demo_builder_error_handling_when_state_machine_raises(self):
        """When state machine raises an exception, build_demo returns status='error' and writes no HTML."""
        from ghost.workers.demo_builder import DemoBuilderWorker

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_builder.DEMOS_DIR", tmpdir):
                with patch("ghost.workers.demo_state_machine.AutonomousDemoStateMachine.execute_all_phases", new_callable=AsyncMock) as m_exec:
                    m_exec.side_effect = RuntimeError("Catastrophic downstream failure")

                    result = asyncio.run(DemoBuilderWorker.build_demo(
                        business_name="Faulty Enterprises",
                        contact_info={"address": "London, UK"}
                    ))

                self.assertEqual(result["status"], "error")
                self.assertIn("Catastrophic downstream failure", result["error"])
                html_path = os.path.join(tmpdir, "faulty-enterprises", "index.html")
                self.assertFalse(os.path.exists(html_path), "No HTML file should be written when build throws!")


if __name__ == "__main__":
    unittest.main()
