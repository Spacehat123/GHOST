"""Unit tests for Lead Hunter (Part 2.1)."""
import asyncio
import os
import tempfile
import unittest

from ghost.crm.db import CRMDatabase
from ghost.workers.lead_hunter import LeadHunter, MapsCaptchaException, normalize_postcode, slugify


class TestLeadHunter(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_crm.db")
        self.crm = CRMDatabase(db_path=self.db_path)
        self.hunter = LeadHunter(crm=self.crm)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_postcode_normalization(self):
        """Extracts and cleans UK and international postcodes."""
        self.assertEqual(normalize_postcode("The Peninsula London, 1 Grosvenor Pl, London SW1X 7HJ, UK"), "SW1X7HJ")
        self.assertEqual(normalize_postcode("221B Baker St, London NW1 6XE"), "NW16XE")
        self.assertEqual(normalize_postcode("123 Main Street, London"), "LONDON")
        self.assertIsNone(normalize_postcode(None))

    def test_slugify(self):
        """Verifies deterministic slugification."""
        self.assertEqual(slugify("Topaz Detailing & PPF!"), "topaz-detailing-ppf")

    def test_harvest_100_with_duplicates_yields_unique(self):
        """100 results with 7 duplicates -> exactly 93 unique leads stored."""
        def mock_provider(city, category, target_count):
            results = []
            for i in range(1, 94):
                results.append({
                    "name": f"Studio {i}",
                    "address": f"{i} High St, London SW1A {i % 9 + 1}AA",
                    "phone": f"+44 20 7946 {i:04d}",
                    "website": f"https://studio{i}.co.uk",
                    "rating": 4.8,
                    "review_count": 42,
                    "maps_cid": f"cid_unique_{i}",
                    "google_category": "Car detailing service"
                })
            # Add 7 intentional duplicates (same maps_cid and same name/postcode)
            for i in range(1, 8):
                results.append({
                    "name": f"Studio {i}",
                    "address": f"{i} High St, London SW1A {i % 9 + 1}AA",
                    "phone": f"+44 20 7946 {i:04d}",
                    "website": f"https://studio{i}.co.uk",
                    "rating": 4.8,
                    "review_count": 42,
                    "maps_cid": f"cid_unique_{i}",
                    "google_category": "Car detailing service"
                })
            return results

        harvested = asyncio.run(
            self.hunter.harvest(
                city="London",
                category="car detailing",
                target_count=100,
                batch_id="batch_test_1",
                mock_provider=mock_provider
            )
        )
        self.assertEqual(len(harvested), 93)
        # Verify in CRM database
        db_leads = self.crm.get_leads_by_batch("batch_test_1")
        self.assertEqual(len(db_leads), 93)

    def test_closed_business_is_skipped(self):
        """Permanently closed business must be skipped during harvest."""
        def mock_provider(city, category, target_count):
            return [
                {
                    "name": "Defunct Motors Ltd",
                    "address": "1 폐업 St, London EC1A 1AA",
                    "phone": "+44 20 7946 0001",
                    "website": "https://defunct.co.uk",
                    "rating": 4.1,
                    "review_count": 15,
                    "maps_cid": "cid_closed_1",
                    "is_closed": True
                },
                {
                    "name": "Active Detailers",
                    "address": "2 High St, London EC1A 2BB",
                    "phone": "+44 20 7946 0002",
                    "website": "https://activedetailers.co.uk",
                    "rating": 4.9,
                    "review_count": 30,
                    "maps_cid": "cid_active_2",
                    "is_closed": False
                }
            ]

        harvested = asyncio.run(
            self.hunter.harvest(
                city="London",
                category="car detailing",
                target_count=10,
                batch_id="batch_test_closed",
                mock_provider=mock_provider
            )
        )
        self.assertEqual(len(harvested), 1)
        self.assertEqual(harvested[0]["name"], "Active Detailers")

    def test_captcha_triggers_clean_stop(self):
        """Captcha or consent wall triggers MapsCaptchaException with safety message."""
        def mock_provider(city, category, target_count):
            return [
                {"name": "Some Business", "captcha_detected": True}
            ]

        with self.assertRaises(MapsCaptchaException) as ctx:
            asyncio.run(
                self.hunter.harvest(
                    city="London",
                    category="car detailing",
                    target_count=10,
                    batch_id="batch_captcha",
                    mock_provider=mock_provider
                )
            )
        self.assertIn("captcha or consent wall", str(ctx.exception).lower())

    def test_chains_are_skipped(self):
        """Known corporate chains and multi-location franchises are skipped."""
        def mock_provider(city, category, target_count):
            return [
                {
                    "name": "Kwik Fit London Centre",
                    "address": "1 Oxford St, London",
                    "phone": "+44 20 7946 1111",
                    "maps_cid": "cid_kwik_1"
                },
                {
                    "name": "PureGym Piccadilly",
                    "address": "2 Piccadilly, London",
                    "phone": "+44 20 7946 2222",
                    "maps_cid": "cid_puregym_1"
                },
                {
                    "name": "Mayfair Independent Detailing",
                    "address": "3 Grosvenor St, London W1K 4QA",
                    "phone": "+44 20 7946 3333",
                    "maps_cid": "cid_independent_1"
                }
            ]

        harvested = asyncio.run(
            self.hunter.harvest(
                city="London",
                category="car detailing",
                target_count=10,
                batch_id="batch_chains",
                config={"exclude_chains": True},
                mock_provider=mock_provider
            )
        )
        self.assertEqual(len(harvested), 1)
        self.assertEqual(harvested[0]["name"], "Mayfair Independent Detailing")

    def test_suppressed_lead_is_skipped(self):
        """A lead matching the suppression list is skipped before storing."""
        self.crm.add_suppression(
            kind="phone",
            value="+44 20 7946 9999",
            reason="Requested DNC",
            source="manual"
        )

        def mock_provider(city, category, target_count):
            return [
                {
                    "name": "Do Not Contact Me Ltd",
                    "address": "9 Silent Way, London SW1",
                    "phone": "+44 20 7946 9999",
                    "maps_cid": "cid_suppressed_1"
                },
                {
                    "name": "Welcome Prospect Studio",
                    "address": "10 High St, London SW1",
                    "phone": "+44 20 7946 8888",
                    "maps_cid": "cid_welcome_1"
                }
            ]

        harvested = asyncio.run(
            self.hunter.harvest(
                city="London",
                category="car detailing",
                target_count=10,
                batch_id="batch_suppression",
                mock_provider=mock_provider
            )
        )
        self.assertEqual(len(harvested), 1)
        self.assertEqual(harvested[0]["name"], "Welcome Prospect Studio")

    def test_never_invents_missing_data(self):
        """Missing attributes remain None, never replaced with synthetic defaults."""
        def mock_provider(city, category, target_count):
            return [
                {
                    "name": "Barebones Workshop",
                    "address": None,
                    "phone": None,
                    "website": None,
                    "rating": None,
                    "review_count": None,
                    "maps_cid": "cid_bare_1"
                }
            ]

        harvested = asyncio.run(
            self.hunter.harvest(
                city="London",
                category="car detailing",
                target_count=1,
                batch_id="batch_bare",
                mock_provider=mock_provider
            )
        )
        self.assertEqual(len(harvested), 1)
        lead = harvested[0]
        self.assertIsNone(lead["address"])
        self.assertIsNone(lead["phone"])
        self.assertIsNone(lead["website"])
        self.assertIsNone(lead["rating"])
        self.assertIsNone(lead["review_count"])


if __name__ == "__main__":
    unittest.main()
