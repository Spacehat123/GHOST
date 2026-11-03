"""Unit tests for Lead Qualifier (Part 2.2)."""
import os
import tempfile
import unittest

from ghost.crm.db import CRMDatabase
from ghost.workers.lead_qualifier import LeadQualifier


class TestLeadQualifier(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_crm.db")
        self.crm = CRMDatabase(db_path=self.db_path)
        self.qualifier = LeadQualifier(crm=self.crm)

        # Create a test campaign and batch
        self.campaign = self.crm.create_campaign("Test Campaign", "London")
        self.batch = self.crm.create_batch(self.campaign["id"], "car detailing", batch_id="batch_qual_1")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_fixture_perfect_site_rejected_due_to_low_friction(self):
        """A business with a fast, modern site, agency credits, and online booking has very low friction and is rejected."""
        lead = self.crm.add_or_update_lead({
            "batch_id": self.batch["id"],
            "slug": "bespoke-agency-site",
            "name": "Bespoke Auto Spa Ltd",
            "address": "1 Mayfair Pl, London W1J 8AJ",
            "phone": "+44 20 7946 0001",
            "website": "https://bespokeautospa.com",
            "rating": 4.9,
            "review_count": 120,
            "google_category": "Car detailing service",
            "status": "harvested",
            "audit_data": {
                "load_time_seconds": 1.2,
                "has_viewport": True,
                "has_tap_to_call": True,
                "has_booking_path": True,
                "has_agency_credit": True
            }
        })

        scores = self.qualifier.evaluate_lead(lead, "car detailing")
        # Revenue is high (30), Reachability is 10, Fit is ~20 (lost agency credit), but Friction is low (5.0)
        self.assertEqual(scores["pitch_type"], "low_friction")
        self.assertEqual(scores["score_friction"], 5.0)

    def test_fixture_no_site_with_great_reviews_is_qualified(self):
        """A business with no site, active social, and stellar reviews gets high friction + revenue points and qualifies."""
        lead = self.crm.add_or_update_lead({
            "batch_id": self.batch["id"],
            "slug": "high-end-no-site",
            "name": "Elite Ceramic Studio",
            "address": "45 King's Rd, London SW3 4ND",
            "phone": "+44 20 7946 0002",
            "website": None,
            "rating": 4.9,
            "review_count": 85,
            "google_category": "Car detailing service",
            "instagram": "@eliteceramic_london",
            "has_social": True,
            "status": "harvested"
        })

        scores = self.qualifier.evaluate_lead(lead, "car detailing")
        self.assertEqual(scores["pitch_type"], "no_site")
        self.assertEqual(scores["score_friction"], 30.0)  # 20 base + 10 social
        self.assertGreaterEqual(scores["score_total"], 70.0)

        # Qualify batch with this single lead
        res = self.qualifier.qualify_batch(self.batch["id"])
        self.assertEqual(res["qualified_count"], 1)
        updated = self.crm.get_lead("high-end-no-site")
        self.assertEqual(updated["status"], "qualified")

    def test_fixture_no_contact_path_is_disqualified(self):
        """A lead without any contact path (no phone, email, or social) must be rejected with 'no_contact_path'."""
        self.crm.add_or_update_lead({
            "batch_id": self.batch["id"],
            "slug": "unreachable-detailing",
            "name": "Phantom Detailing",
            "address": "Secret Lane, London",
            "phone": None,
            "email": None,
            "website": None,
            "rating": 5.0,
            "review_count": 200,
            "google_category": "Car detailing service",
            "status": "harvested"
        })

        res = self.qualifier.qualify_batch(self.batch["id"])
        self.assertEqual(res["qualified_count"], 0)
        self.assertEqual(res["rejected_count"], 1)
        self.assertEqual(res["rejection_reasons"].get("no_contact_path"), 1)

        lead = self.crm.get_lead("unreachable-detailing")
        self.assertEqual(lead["status"], "rejected")

    def test_fixture_unknown_entity_is_kept_and_flagged(self):
        """An entity whose legal structure cannot be determined is kept as 'unknown' without blocking qualification."""
        self.crm.add_or_update_lead({
            "batch_id": self.batch["id"],
            "slug": "unknown-structure",
            "name": "Apex Gloss",
            "address": "12 Fleet St, London EC4Y 1AA",
            "phone": "+44 20 7946 0003",
            "website": None,
            "rating": 4.8,
            "review_count": 50,
            "google_category": "Car detailing service",
            "status": "harvested"
        })

        res = self.qualifier.qualify_batch(self.batch["id"])
        self.assertEqual(res["qualified_count"], 1)
        lead = self.crm.get_lead("unknown-structure")
        self.assertEqual(lead["status"], "qualified")
        self.assertEqual(lead["entity_type"], "unknown")

    def test_floor_score_strictly_respected(self):
        """Leads below min_total_score (default 45) are never qualified, even if keep_fraction capacity remains."""
        # 3 leads, all low score (e.g. 1 review, 3.2 rating, low fit)
        for i in range(1, 4):
            self.crm.add_or_update_lead({
                "batch_id": self.batch["id"],
                "slug": f"low-score-lead-{i}",
                "name": f"Subpar Garage {i}",
                "address": None,
                "phone": "+44 20 7946 0010",
                "website": "https://fastsite.com",
                "rating": 3.2,
                "review_count": 2,
                "google_category": "Car detailing service",
                "status": "harvested",
                "audit_data": {
                    "load_time_seconds": 1.0,
                    "has_viewport": True,
                    "has_tap_to_call": True,
                    "has_booking_path": True
                }
            })

        res = self.qualifier.qualify_batch(self.batch["id"], campaign_config={"min_total_score": 45.0, "keep_fraction": 0.5})
        self.assertEqual(res["qualified_count"], 0)
        self.assertEqual(res["rejected_count"], 3)
        self.assertEqual(res["rejection_reasons"].get("low_score"), 3)

    def test_anti_bias_warning_triggered_when_single_reason_exceeds_60_percent(self):
        """When >60% of rejects are due to a single reason code, a bias warning is generated."""
        # Add 10 leads: 7 unreachable (no contact), 3 with low score
        for i in range(1, 8):
            self.crm.add_or_update_lead({
                "batch_id": self.batch["id"],
                "slug": f"unreachable-{i}",
                "name": f"Unreachable Studio {i}",
                "phone": None,
                "website": None,
                "rating": 4.5,
                "review_count": 20,
                "status": "harvested"
            })
        for i in range(8, 11):
            self.crm.add_or_update_lead({
                "batch_id": self.batch["id"],
                "slug": f"reachable-low-{i}",
                "name": f"Reachable Subpar {i}",
                "phone": "+44 20 7946 0099",
                "website": "https://ok.com",
                "rating": 3.0,
                "review_count": 1,
                "status": "harvested"
            })

        res = self.qualifier.qualify_batch(self.batch["id"])
        self.assertIsNotNone(res.get("bias_warning"))
        self.assertIn(">60%", res["bias_warning"])
        self.assertIn("no_contact_path", res["bias_warning"])


if __name__ == "__main__":
    unittest.main()
