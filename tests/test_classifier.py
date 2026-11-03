"""Unit tests for Ghost business domain classifier with 60+ table-driven cases."""
import unittest
from ghost.workers.demo_state_machine import classify_business, detect_domain_profile


class TestBusinessClassifier(unittest.TestCase):

    def test_topaz_detailing_google_category(self):
        """Topaz Detailing with Google category 'Car detailing service' maps to home_trade_services."""
        domain, conf, needs_conf, candidates = classify_business("Car detailing service", "Topaz Detailing")
        self.assertEqual(domain, "home_trade_services")
        self.assertGreaterEqual(conf, 0.90)
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["domain"], "home_trade_services")

    def test_word_boundaries_barber_does_not_match_bar(self):
        """'Barber' must match salons_spas, never bar / restaurants_cafes."""
        domain, conf, needs_conf, candidates = classify_business(None, "Gentleman Barber Lounge")
        self.assertEqual(domain, "salons_spas")
        self.assertFalse(needs_conf)
        self.assertNotEqual(domain, "restaurants_cafes")

    def test_word_boundaries_public_does_not_match_pub(self):
        """'Public' must not match pub / restaurants_cafes."""
        domain, conf, needs_conf, candidates = classify_business(None, "Public Interest Law Associates")
        self.assertEqual(domain, "legal_accounting")
        self.assertFalse(needs_conf)
        self.assertNotEqual(domain, "restaurants_cafes")

    def test_dental_clinic(self):
        """Dr. Smile Dental maps to dental_medical."""
        domain, conf, needs_conf, candidates = classify_business(None, "Dr. Smile Dental Clinic")
        self.assertEqual(domain, "dental_medical")
        self.assertFalse(needs_conf)

    def test_the_crown_pub(self):
        """'The Crown Pub' must classify into restaurants_cafes."""
        domain, conf, needs_conf, candidates = classify_business(None, "The Crown Pub")
        self.assertEqual(domain, "restaurants_cafes")
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(conf, 0.85)

    def test_glow_beauty_studio(self):
        """'Glow Beauty Studio' must classify into salons_spas."""
        domain, conf, needs_conf, candidates = classify_business(None, "Glow Beauty Studio")
        self.assertEqual(domain, "salons_spas")
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(conf, 0.85)

    def test_violas_couture_with_tailor_category(self):
        """'Viola’s Couture' with Google category 'Tailor' maps to boutiques_retail."""
        domain, conf, needs_conf, candidates = classify_business("Tailor", "Viola’s Couture")
        self.assertEqual(domain, "boutiques_retail")
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(conf, 0.90)

    def test_violas_couture_name_alone(self):
        """'Viola’s Couture' with no category matches couture -> boutiques_retail."""
        domain, conf, needs_conf, candidates = classify_business(None, "Viola’s Couture")
        self.assertEqual(domain, "boutiques_retail")
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(conf, 0.85)

    def test_pet_groomers(self):
        """'Pet Groomers' maps to home_trade_services."""
        domain, conf, needs_conf, candidates = classify_business(None, "Pet Groomers")
        self.assertEqual(domain, "home_trade_services")
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(conf, 0.85)

    def test_autocare_with_nearby_restaurant_snippet_prefers_name(self):
        """'Autocare' must match trade/auto service even when snippet mentions a nearby Italian restaurant."""
        domain, conf, needs_conf, candidates = classify_business(
            None,
            "Autocare",
            "Located right next to the best Italian restaurant on High Street. Ample customer parking."
        )
        self.assertEqual(domain, "home_trade_services")
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(conf, 0.85)

    def test_blue_door_with_coffee_snippet_fails_closed(self):
        """'Blue Door' with a single casual coffee snippet must NOT classify as a cafe;
        must fail closed to general_services with needs_confirmation=True."""
        domain, conf, needs_conf, candidates = classify_business(
            None,
            "Blue Door",
            "Located across from the best coffee spot in London. Meeting space and private studios available."
        )
        self.assertEqual(domain, "general_services")
        self.assertTrue(needs_conf)
        self.assertLessEqual(conf, 0.50)

    def test_blue_door_with_two_snippet_hits_qualifies_with_confirmation(self):
        """'Blue Door' with >= 2 distinct snippet hits matches domain with needs_confirmation=True."""
        domain, conf, needs_conf, candidates = classify_business(
            None,
            "Blue Door",
            "Reserve a table for dinner and explore our seasonal tasting menu crafted by our chef."
        )
        self.assertEqual(domain, "restaurants_cafes")
        self.assertTrue(needs_conf)
        self.assertEqual(conf, 0.60)

    def test_unknown_fails_closed_to_general_services(self):
        """Unknown business name without category fails closed to general_services with needs_confirmation."""
        domain, conf, needs_conf, candidates = classify_business(None, "Quantum Flux Syndicate")
        self.assertEqual(domain, "general_services")
        self.assertTrue(needs_conf)
        self.assertLessEqual(conf, 0.20)

    def test_candidates_format_and_ordering(self):
        """Classifier must return top 3 candidates with domain, score, and reason."""
        domain, conf, needs_conf, candidates = classify_business(
            None,
            "Blue Door",
            "Best coffee in town next to great plumbing service."
        )
        self.assertLessEqual(len(candidates), 3)
        self.assertEqual(candidates[0]["domain"], domain)
        for c in candidates:
            self.assertIn("domain", c)
            self.assertIn("score", c)
            self.assertIn("reason", c)

    def test_table_driven_batch_catalog_industries(self):
        """Table-driven test with 65+ cases asserting exact domain mapping and confirmation flag."""
        test_cases = [
            # --- 20 Detailing & Automotive / Trades (home_trade_services) ---
            ("Car detailing service", "Precision Detailing", "", "home_trade_services", False),
            (None, "Apex Ceramic Coating Studio", "", "home_trade_services", False),
            (None, "Mayfair Paint Protection & PPF", "", "home_trade_services", False),
            ("Car wash", "Kensington Hand Car Wash", "", "home_trade_services", False),
            (None, "Elite Valeting London", "", "home_trade_services", False),
            ("Auto repair shop", "London Motor Works", "", "home_trade_services", False),
            (None, "Battersea Autocare Centre", "", "home_trade_services", False),
            (None, "Chelsea Body Shop & Garage", "", "home_trade_services", False),
            (None, "Vanguard Paint Correction", "", "home_trade_services", False),
            (None, "City Locksmith Services", "", "home_trade_services", False),
            ("Plumber", "Pimlico Express Plumbers", "", "home_trade_services", False),
            (None, "Pro Electrical Installation", "", "home_trade_services", False),
            (None, "Thames Valley HVAC Services", "", "home_trade_services", False),
            (None, "Knightsbridge Handyman Group", "", "home_trade_services", False),
            (None, "Apex Roofing Contractors", "", "home_trade_services", False),
            (None, "Canary Wharf Carpet Cleaning", "", "home_trade_services", False),
            ("Pet groomer", "Fluffy Paws Grooming", "", "home_trade_services", False),
            (None, "Bark & Bubble Dog Groomers", "", "home_trade_services", False),
            (None, "The Grooming Lounge For Dogs", "", "home_trade_services", False),
            (None, "London Mobile Pet Groomer", "", "home_trade_services", False),

            # --- 20 Barbershops / Hair Salons / Spas (salons_spas) ---
            ("Barbershop", "Sweeney & Co Barbershop", "", "salons_spas", False),
            (None, "Truefitt Barber Lounge", "", "salons_spas", False),
            ("Hair salon", "Vidal Hairdressing Atelier", "", "salons_spas", False),
            (None, "Covent Garden Hair Studio", "", "salons_spas", False),
            (None, "Soho Hairstylist & Color", "", "salons_spas", False),
            ("Beauty salon", "Radiance Beauty Clinic", "", "salons_spas", False),
            (None, "Mayfair Aesthetic Beauty Studio", "", "salons_spas", False),
            ("Nail salon", "Gloss & Glam Nail Bar", "", "salons_spas", False),
            (None, "Luxury Lashes & Brow Studio", "", "salons_spas", False),
            (None, "Kensington Eyelash Salon", "", "salons_spas", False),
            (None, "Pure Glow Facial Spa", "", "salons_spas", False),
            (None, "Thai Herbal Massage Sanctuary", "", "salons_spas", False),
            ("Day spa", "The Banya Wellness Sanctuary", "", "salons_spas", False),
            (None, "Clerkenwell Skin Care Clinic", "", "salons_spas", False),
            (None, "Nail Artist London", "", "salons_spas", False),
            (None, "The Gentleman Haircut Club", "", "salons_spas", False),
            (None, "Bespoke Brow & Lash Artist", "", "salons_spas", False),
            (None, "Harley Street Aesthetic Clinic", "", "salons_spas", False),
            (None, "The Waxing & Facial Studio", "", "salons_spas", False),
            (None, "Serenity Massage Therapist", "", "salons_spas", False),

            # --- 20 Pubs / Restaurants / Cafes (restaurants_cafes) ---
            ("Pub", "The Red Lion Pub", "", "restaurants_cafes", False),
            (None, "The Churchill Arms Pub", "", "restaurants_cafes", False),
            (None, "The Black Dog Gastropub", "", "restaurants_cafes", False),
            ("Public house", "The Grenadier", "", "restaurants_cafes", False),
            (None, "The Old Swan Tavern", "", "restaurants_cafes", False),
            (None, "The White Hart Inn", "", "restaurants_cafes", False),
            ("Brewpub", "Camden Town Brewery Taproom", "", "restaurants_cafes", False),
            (None, "Bermondsey Craft Taproom", "", "restaurants_cafes", False),
            ("Cocktail bar", "Nightjar Speakeasy", "", "restaurants_cafes", False),
            (None, "Gordon's Wine Bar", "", "restaurants_cafes", False),
            ("Restaurant", "The Ivy Chelsea Garden", "", "restaurants_cafes", False),
            (None, "Locanda Locatelli Italian Bistro", "", "restaurants_cafes", False),
            (None, "Monmouth Coffee Roastery", "", "restaurants_cafes", False),
            (None, "Origin Espresso Bar", "", "restaurants_cafes", False),
            (None, "Bake & Brew Cafe", "", "restaurants_cafes", False),
            (None, "Sushi Tetsu Omakase", "", "restaurants_cafes", False),
            (None, "Hawksmoor Steak House", "", "restaurants_cafes", False),
            (None, "Rules Traditional Eatery", "", "restaurants_cafes", False),
            (None, "Borough Market Brasserie", "", "restaurants_cafes", False),
            (None, "The Clachan Irish Pub", "", "restaurants_cafes", False),

            # --- Additional Sectors: Health, Legal, Retail, Property, Tutors ---
            ("Tailor", "Huntsman Savile Row", "", "boutiques_retail", False),
            (None, "Gieves Bespoke Suiting", "", "boutiques_retail", False),
            (None, "Mayfair Haute Couture Atelier", "", "boutiques_retail", False),
            ("Optician", "Specsavers Eye Clinic", "", "dental_medical", False),
            (None, "Harley Street Physiotherapy", "", "dental_medical", False),
            (None, "City Chiropractic Clinic", "", "dental_medical", False),
            ("Solicitors", "Clifford Legal Associates", "", "legal_accounting", False),
            (None, "Chartered Accountants LLP", "", "legal_accounting", False),
            ("Estate agent", "Knight Frank Residential", "", "real_estate", False),
            ("Florist", "Rebel Rebel Botanical Stylist", "", "wedding_florists", False),
            ("Driving school", "AA Driving School London", "", "tutors", False),
            ("Tutoring service", "Excel SAT Academy", "", "tutors", False),
        ]

        for cat, name, snip, expected_domain, expected_needs_conf in test_cases:
            domain, conf, needs_conf, candidates = classify_business(cat, name, snip)
            self.assertEqual(
                domain,
                expected_domain,
                f"Mismatch for '{name}' (cat='{cat}'): expected {expected_domain}, got {domain}"
            )
            self.assertEqual(
                needs_conf,
                expected_needs_conf,
                f"needs_conf mismatch for '{name}': expected {expected_needs_conf}, got {needs_conf}"
            )
            self.assertGreater(len(candidates), 0)


if __name__ == "__main__":
    unittest.main()
