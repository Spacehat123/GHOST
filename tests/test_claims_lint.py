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
        """Topaz Detailing demo must contain zero restaurant words, zero fake scores, and zero rupee symbols."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="Topaz Detailing London",
                    location="The Peninsula London, 1 Grosvenor Pl, London SW1X 7HJ, UK",
                    slug="topaz-detailing-london"
                )

                # Mock scout return: automotive detailing with 1 real review
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
                    ]
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
                self.assertNotIn("Verified Establishment", html)
                self.assertNotIn("Verified Impressions", html)
                self.assertNotIn("Verified Client", html)
                self.assertIn("What customers say", html)

                # 4. Unverified curated services must be marked as sample concept architecture
                self.assertIn("Sample Concept Architecture • Not Scraped from Listing", html)
                self.assertIn("[Sample Scope Placeholder]", html)

                ledger_path = os.path.join(sm.out_dir, "SOURCES.json")
                self.assertTrue(os.path.exists(ledger_path))
                import json
                with open(ledger_path, "r", encoding="utf-8") as f:
                    ledger_data = json.load(f)
                self.assertFalse(ledger_data["services"]["verified"])
                self.assertIn("Sample Concept Architecture", ledger_data["services"]["notes"])

                # 5. Single review must NOT be duplicated into an infinite marquee loop
                # With 1 review, static quote card layout is used
                self.assertNotIn("marquee-track", html, "Marquee track should not be rendered for a single review!")
                self.assertEqual(html.count("Exceptional ceramic coating on my Tesla"), 1)

                # 6. Business title split: primary title clamped
                self.assertIn("Topaz Detailing", html)

    def test_demo_builder_fallback_purged_of_hospitality(self):
        """DemoBuilderWorker._generate_html fallback must use domain copy and contain zero hospitality terms."""
        from ghost.workers.demo_builder import DemoBuilderWorker, ARCHETYPE_STYLES
        html = DemoBuilderWorker._generate_html(
            business_name="Apex Motor Works",
            slug="apex-motor-works",
            tagline="Precision engineering in London.",
            style=ARCHETYPE_STYLES[5],
            archetype_id=5,
            items=[{"title": "Brake Inspection", "price": "£120", "desc": "Full caliper overhaul.", "category": "Safety"}],
            contact={"address": "London, UK"}
        )
        self.assertNotIn("Bespoke Dining", html)
        self.assertNotIn("Sommelier", html)
        self.assertNotIn("Cellar Vintage Reserve", html)
        self.assertNotIn("98.5 Points", html)
        self.assertNotIn("Reserve Tasting Experience", html)
        self.assertNotIn("Seasonal Harvest", html)
        self.assertIn("Apex Motor Works", html)
        self.assertIn("Brake Inspection", html)


if __name__ == "__main__":
    unittest.main()
