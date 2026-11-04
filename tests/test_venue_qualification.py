"""Unit and Regression Tests for Strict Lead Qualification and Verification Pipeline.
Enforces non-negotiable objective:
Find expensive diners and restaurants that genuinely do not have an official website.
Guarantees zero false positives.
"""
import os
import tempfile
import unittest
import json

from ghost.workers.venue_qualification import (
    WebsiteVerifier,
    PriceVerifier,
    VenueQualificationPipeline,
    is_aggregator_or_social,
    extract_root_domain
)
from ghost.core.validator import ToolArgumentValidator


class TestVenueQualification(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        # Set up a deterministic mock prober for testing without external network
        def mock_prober(url: str, business_name: str) -> bool:
            u = url.lower()
            if "grasshoppers.work" in u:
                return False  # Coworking space (not dining)
            if "farmlore.in" in u:
                return True   # Fine dining restaurant
            if "lupa.co.in" in u:
                return True   # European dining restaurant
            if "dining" in u or "restaurant" in u or "culinary" in u:
                return True
            return False

        WebsiteVerifier._custom_prober = mock_prober

    def tearDown(self):
        WebsiteVerifier._custom_prober = None
        self.tmpdir.cleanup()

    def test_aggregator_domains_recognized(self):
        """Aggregators, directories, and social platforms must never be considered official brand websites."""
        aggregators = [
            "https://www.zomato.com/bangalore/grasshopper-bannerghatta-road",
            "https://www.swiggy.com/restaurants/grasshopper",
            "https://www.tripadvisor.in/Restaurant_Review-g297628-d1094034-Reviews-Grasshopper-Bengaluru_Bangalore_District_Karnataka.html",
            "https://www.eazydiner.com/bengaluru/grasshopper",
            "https://www.instagram.com/grasshopper_restaurant",
            "https://www.facebook.com/grasshopperbangalore",
            "https://lbb.in/bangalore/grasshopper-restaurant",
            "https://www.justdial.com/Bangalore/Grasshopper"
        ]
        for url in aggregators:
            self.assertTrue(is_aggregator_or_social(url), f"Failed to identify aggregator: {url}")

        non_aggregators = [
            "https://www.farmlore.in",
            "https://lupa.co.in",
            "https://bespokeautospa.com"
        ]
        for url in non_aggregators:
            self.assertFalse(is_aggregator_or_social(url), f"Incorrectly marked as aggregator: {url}")

    def test_grasshopper_qualifies_zero_false_positives(self):
        """
        Grasshopper:
        - Fine dining European degustation
        - Price: ₹7,000 for two (₹3,500/head, >= ₹2,500 floor)
        - Web: No official standalone website (only directories, Instagram, and unrelated grasshoppers.work coworking)
        - Must be QUALIFIED.
        """
        venue_data = {
            "name": "Grasshopper",
            "founders": "Sonali Sattar & Himanshu Dimri",
            "category": "Fine Dining / Farm-to-Table European Degustation",
            "address": "45, Kalena Agrahara, Bannerghatta Road, Bengaluru, Karnataka 560076",
            "setting": "Open-air rustic farm sanctuary surrounded by foliage and personalized table settings.",
            "access_model": "Strictly by prior reservation only (intimate limited seating).",
            "phone": "+91 80 2658 6378",
            "instagram": "https://www.instagram.com/grasshopper_restaurant"
        }

        search_results = [
            {"title": "Grasshopper - Bannerghatta Road - Zomato", "url": "https://www.zomato.com/bangalore/grasshopper-bannerghatta-road", "snippet": "₹7,000 for two people (approx.) European, Continental, Desserts"},
            {"title": "GRASSHOPPER, Bengaluru - Bannerghatta Road - Tripadvisor", "url": "https://www.tripadvisor.in/Restaurant_Review-g297628-d1094034-Reviews-Grasshopper-Bengaluru_Bangalore_District_Karnataka.html", "snippet": "Seven course tasting menu at Grasshopper in an open air setting"},
            {"title": "Grasshopper (@grasshopper_restaurant) • Instagram photos and videos", "url": "https://www.instagram.com/grasshopper_restaurant", "snippet": "Farm-to-table dining experience by Sonali Sattar and Himanshu Dimri"},
            {"title": "Grasshoppers Coworking Space", "url": "https://grasshoppers.work", "snippet": "Flexible shared desk space and private offices in HSR Layout"}
        ]

        pricing_snippets = [
            "₹7,000 for two people (approx.)",
            "Seven-course degustation menu priced at ₹3,500 per guest by prior reservation"
        ]

        record = VenueQualificationPipeline.qualify_venue(
            venue_data=venue_data,
            location="Bengaluru",
            search_results=search_results,
            pricing_snippets=pricing_snippets
        )

        self.assertEqual(record["status"], "QUALIFIED")
        self.assertIsNone(record["rejection_reason"])
        self.assertEqual(record["website_verification"]["verdict"], "NO_OFFICIAL_WEBSITE")
        self.assertFalse(record["website_verification"]["has_official_website"])
        self.assertEqual(record["price_verification"]["verdict"], "CONFIRMED_EXPENSIVE")
        self.assertTrue(record["price_verification"]["is_expensive"])
        self.assertEqual(record["price_verification"]["cost_per_person"], 3500)

    def test_farmlore_rejected_due_to_existing_official_website(self):
        """
        Farmlore has an official website (https://www.farmlore.in/).
        Must be strictly REJECTED (Zero false positives).
        """
        venue_data = {
            "name": "Farmlore",
            "category": "Farm-to-Table Fine Dining",
            "address": "Sathanur Village, Bengaluru"
        }

        search_results = [
            {"title": "Farmlore | Fine Dining Experience Bengaluru", "url": "https://www.farmlore.in/", "snippet": "A 10-course seasonal degustation celebrating indigenous Karnataka produce."},
            {"title": "Farmlore - Zomato", "url": "https://www.zomato.com/bangalore/farmlore", "snippet": "₹6,000 for two"}
        ]

        pricing_snippets = ["₹6,000 for two", "₹3,000 per person"]

        record = VenueQualificationPipeline.qualify_venue(
            venue_data=venue_data,
            location="Bengaluru",
            search_results=search_results,
            pricing_snippets=pricing_snippets
        )

        self.assertEqual(record["status"], "REJECTED")
        self.assertIn("Official website exists", record["rejection_reason"])
        self.assertEqual(record["website_verification"]["verdict"], "OFFICIAL_WEBSITE_CONFIRMED")
        self.assertTrue(record["website_verification"]["has_official_website"])
        self.assertEqual(record["website_verification"]["official_website_url"], "https://www.farmlore.in/")

    def test_lupa_rejected_due_to_existing_official_website(self):
        """
        Lupa has an official website (https://lupa.co.in/).
        Must be strictly REJECTED (Zero false positives).
        """
        venue_data = {
            "name": "Lupa",
            "website": "https://lupa.co.in/",
            "category": "European Dining & Wine"
        }

        search_results = [
            {"title": "Lupa Bengaluru | Chef Manu Chandra", "url": "https://lupa.co.in/", "snippet": "European dining, cocktail bar and wine cellar by Chef Manu Chandra."}
        ]

        pricing_snippets = ["₹4,500 for two"]

        record = VenueQualificationPipeline.qualify_venue(
            venue_data=venue_data,
            location="Bengaluru",
            search_results=search_results,
            pricing_snippets=pricing_snippets
        )

        self.assertEqual(record["status"], "REJECTED")
        self.assertIn("Official website exists", record["rejection_reason"])
        self.assertEqual(record["website_verification"]["verdict"], "OFFICIAL_WEBSITE_CONFIRMED")

    def test_budget_restaurant_without_website_rejected(self):
        """
        A diner without an official website but with budget pricing (< ₹2,500/head)
        must be strictly REJECTED as not high-ticket.
        """
        venue_data = {
            "name": "Sri Krishna Tiffin Room",
            "category": "South Indian Vegetarian",
            "address": "Malleshwaram, Bengaluru"
        }

        search_results = [
            {"title": "Sri Krishna Tiffin Room - Zomato", "url": "https://www.zomato.com/bangalore/sktr", "snippet": "Quick service tiffin and filter coffee"}
        ]

        pricing_snippets = ["₹150 for two", "Average ₹75 per person"]

        record = VenueQualificationPipeline.qualify_venue(
            venue_data=venue_data,
            location="Bengaluru",
            search_results=search_results,
            pricing_snippets=pricing_snippets
        )

        self.assertEqual(record["status"], "REJECTED")
        self.assertIn("Not high-ticket", record["rejection_reason"])
        self.assertEqual(record["price_verification"]["verdict"], "BELOW_THRESHOLD")

    def test_inconclusive_marked_unverified(self):
        """Venues with missing or inconclusive search data are marked UNVERIFIED, never falsely qualified."""
        venue_data = {"name": "Obscure Unknown Bistro"}
        record = VenueQualificationPipeline.qualify_venue(
            venue_data=venue_data,
            location="Bengaluru",
            search_results=[],
            pricing_snippets=[]
        )
        self.assertEqual(record["status"], "UNVERIFIED")
        self.assertIn("Verification incomplete", record["rejection_reason"])

    def test_save_dossier_to_disk(self):
        """Verifies that qualified lead dossiers are properly persisted with complete markdown formatting."""
        venue_data = {
            "name": "Grasshopper",
            "founders": "Sonali Sattar & Himanshu Dimri",
            "category": "Fine Dining / Farm-to-Table European Degustation",
            "address": "45, Kalena Agrahara, Bannerghatta Road, Bengaluru, Karnataka 560076",
            "setting": "Open-air rustic farm sanctuary surrounded by foliage and personalized table settings.",
            "access_model": "Strictly by prior reservation only (intimate limited seating).",
            "phone": "+91 80 2658 6378",
            "instagram": "https://www.instagram.com/grasshopper_restaurant"
        }
        search_results = [
            {"title": "Grasshopper Zomato", "url": "https://www.zomato.com/bangalore/grasshopper", "snippet": "₹7,000 for two"}
        ]
        pricing_snippets = ["₹7,000 for two"]

        record = VenueQualificationPipeline.qualify_venue(
            venue_data=venue_data,
            search_results=search_results,
            pricing_snippets=pricing_snippets
        )

        saved_path = VenueQualificationPipeline.save_dossier_to_disk(record, base_dir=self.tmpdir.name)
        self.assertTrue(os.path.exists(saved_path))
        self.assertTrue(saved_path.endswith("intel.md"))

        with open(saved_path, "r", encoding="utf-8") as f:
            md_content = f.read()

        self.assertIn("# Reconnaissance Dossier: Grasshopper", md_content)
        self.assertIn("**Status**: `QUALIFIED`", md_content)
        self.assertIn("Sonali Sattar & Himanshu Dimri", md_content)
        self.assertIn("45, Kalena Agrahara, Bannerghatta Road", md_content)
        self.assertIn("## Website Verification Proof", md_content)
        self.assertIn("## Price Verification Proof", md_content)

        # Check evidence.json was also saved
        json_path = os.path.join(os.path.dirname(saved_path), "evidence.json")
        self.assertTrue(os.path.exists(json_path))

    def test_tool_argument_validation(self):
        """Verifies schema validation for qualify_venue_lead and scout_expensive_venues_without_website."""
        # qualify_venue_lead requires 'name'
        valid, parsed, err = ToolArgumentValidator.validate("qualify_venue_lead", '{"name": "Grasshopper", "location": "Bengaluru"}')
        self.assertTrue(valid)
        self.assertEqual(parsed["name"], "Grasshopper")

        # qualify_venue_lead missing 'name' fails
        valid, parsed, err = ToolArgumentValidator.validate("qualify_venue_lead", '{"location": "Bengaluru"}')
        self.assertFalse(valid)
        self.assertIn("Missing required parameter 'name'", err)

        # scout_expensive_venues_without_website with valid params
        valid, parsed, err = ToolArgumentValidator.validate("scout_expensive_venues_without_website", '{"target_count": 3, "location": "Bengaluru"}')
        self.assertTrue(valid)
        self.assertEqual(parsed["target_count"], 3)


if __name__ == "__main__":
    unittest.main()
