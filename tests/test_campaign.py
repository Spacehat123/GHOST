"""Unit tests for Campaign Orchestrator (Part 2.3 & Acceptance)."""
import asyncio
import os
import tempfile
import unittest

from ghost.crm.db import CRMDatabase
from ghost.workers.campaign import CampaignOrchestrator


class TestCampaignOrchestrator(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_crm.db")
        self.crm = CRMDatabase(db_path=self.db_path)
        self.orch = CampaignOrchestrator(crm=self.crm)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_duplicate_or_synonymous_category_rejected(self):
        """Exact duplicates or synonymous categories (e.g. car detailing & auto detailing) are rejected with a clear error."""
        # 1. Exact duplicate
        with self.assertRaises(ValueError) as ctx:
            self.orch.create_campaign("London Test", "London", ["car detailing", "car detailing", "boutique gym"])
        self.assertIn("Duplicate or synonymous category", str(ctx.exception))

        # 2. Semantic synonym
        with self.assertRaises(ValueError) as ctx:
            self.orch.create_campaign("London Test 2", "London", ["car detailing", "auto detailing", "boutique gym"])
        self.assertIn("Duplicate or synonymous category", str(ctx.exception))

        # 3. Medical / Dental synonym
        with self.assertRaises(ValueError) as ctx:
            self.orch.create_campaign("London Test 3", "London", ["dentist", "dental clinic", "car detailing"])
        self.assertIn("Duplicate or synonymous category", str(ctx.exception))

    def test_suppressed_lead_never_reaches_qualified(self):
        """A suppressed contact is strictly filtered and never reaches qualified status."""
        self.crm.add_suppression(
            kind="domain",
            value="https://bad-lead.co.uk",
            reason="Legal Cease and Desist",
            source="manual"
        )

        campaign = self.orch.create_campaign(
            "Suppression Campaign",
            "London",
            ["car detailing"]
        )

        def mock_provider(city, category, target_count):
            return [
                {
                    "name": "Bad Lead Detailing Ltd",
                    "address": "1 Bad St, London W1",
                    "phone": "+44 20 7946 0001",
                    "website": "https://bad-lead.co.uk",
                    "rating": 5.0,
                    "review_count": 500,
                    "maps_cid": "cid_supp_1"
                },
                {
                    "name": "Legit Detailing Studio",
                    "address": "2 Good St, London W1",
                    "phone": "+44 20 7946 0002",
                    "website": "https://legitdetailing.co.uk",
                    "rating": 4.8,
                    "review_count": 50,
                    "maps_cid": "cid_good_1"
                }
            ]

        res = asyncio.run(
            self.orch.run_next_batch(
                campaign["id"],
                mock_provider=mock_provider
            )
        )

        self.assertEqual(res["qualified_count"], 1)
        qualified_slugs = [q["slug"] for q in res["qualified_leads"]]
        self.assertNotIn("bad-lead-detailing-ltd-london", qualified_slugs)
        self.assertIn("legit-detailing-studio-london", qualified_slugs)

    def test_end_to_end_3_batches_x_100_yields_3_x_33_qualified(self):
        """
        Acceptance test:
        1 Campaign with 3 distinct categories.
        Each batch harvests 100 leads -> qualifies top third (33).
        Result: exactly 33 qualified leads per batch (total 99 qualified).
        Nothing is built or sent.
        """
        categories = ["car detailing", "cosmetic dental clinic", "boutique gym"]
        campaign = self.orch.create_campaign(
            "London Growth Campaign",
            "London",
            categories,
            config={"harvest_per_batch": 100, "keep_fraction": 0.33, "min_total_score": 40.0}
        )
        self.assertEqual(len(campaign["batches"]), 3)

        # Provider generating 100 viable leads per category
        def mock_provider(city, category, target_count):
            results = []
            prefix = category.replace(" ", "-")
            for i in range(1, target_count + 1):
                # Gradient of ratings and reviews to create ranked distribution
                rating = 4.0 + (i % 10) * 0.1
                reviews = 10 + i * 2
                results.append({
                    "name": f"{category.title()} Studio {i}",
                    "address": f"{i} Market St, London EC1 {i % 10 + 1}AA",
                    "phone": f"+44 20 7946 {i:04d}",
                    "website": f"https://{prefix}-{i}.co.uk" if i % 4 != 0 else None,
                    "rating": rating,
                    "review_count": reviews,
                    "maps_cid": f"cid_{prefix}_{i}",
                    "google_category": category
                })
            return results

        # Run Batch 1
        res1 = asyncio.run(self.orch.run_next_batch(campaign["id"], mock_provider=mock_provider))
        self.assertEqual(res1["status"], "batch_completed")
        self.assertEqual(res1["category"], "car detailing")
        self.assertEqual(res1["harvested_count"], 100)
        self.assertEqual(res1["qualified_count"], 33)

        # Run Batch 2
        res2 = asyncio.run(self.orch.run_next_batch(campaign["id"], mock_provider=mock_provider))
        self.assertEqual(res2["status"], "batch_completed")
        self.assertEqual(res2["category"], "cosmetic dental clinic")
        self.assertEqual(res2["harvested_count"], 100)
        self.assertEqual(res2["qualified_count"], 33)

        # Run Batch 3
        res3 = asyncio.run(self.orch.run_next_batch(campaign["id"], mock_provider=mock_provider))
        self.assertEqual(res3["status"], "batch_completed")
        self.assertEqual(res3["category"], "boutique gym")
        self.assertEqual(res3["harvested_count"], 100)
        self.assertEqual(res3["qualified_count"], 33)

        # Re-running after all 3 batches complete: idempotent, returns campaign_complete
        res_after = asyncio.run(self.orch.run_next_batch(campaign["id"], mock_provider=mock_provider))
        self.assertEqual(res_after["status"], "campaign_complete")

        # Verify nothing has been built or sent:
        # Check all leads in DB: none should be 'building', 'built', 'approved', or 'sent'
        for b in campaign["batches"]:
            q_leads = self.crm.get_leads_by_batch(b["id"], status="qualified")
            self.assertEqual(len(q_leads), 33)
            for l in q_leads:
                self.assertNotIn(l["status"], ["building", "built", "sent"])

        # Check total qualified across campaign
        all_campaign_batches = self.crm.get_batches_for_campaign(campaign["id"])
        total_qualified = sum(b["qualified_count"] for b in all_campaign_batches)
        self.assertEqual(total_qualified, 99)


if __name__ == "__main__":
    unittest.main()
