"""Unit tests for Ghost business domain classifier."""
import unittest
from ghost.workers.demo_state_machine import classify_business, detect_domain_profile


class TestBusinessClassifier(unittest.TestCase):

    def test_topaz_detailing_google_category(self):
        """Topaz Detailing with Google category 'Car detailing service' maps to home_trade_services."""
        domain, conf, needs_conf = classify_business("Car detailing service", "Topaz Detailing")
        self.assertEqual(domain, "home_trade_services")
        self.assertGreaterEqual(conf, 0.90)
        self.assertFalse(needs_conf)

    def test_word_boundaries_barber_does_not_match_bar(self):
        """'Barber' must match salons_spas, never bar / restaurants_cafes."""
        domain, conf, needs_conf = classify_business(None, "Gentleman Barber Lounge")
        self.assertEqual(domain, "salons_spas")
        self.assertFalse(needs_conf)

    def test_word_boundaries_public_does_not_match_pub(self):
        """'Public' must not match pub / restaurants_cafes."""
        domain, conf, needs_conf = classify_business(None, "Public Interest Law Associates")
        self.assertEqual(domain, "legal_accounting")
        self.assertFalse(needs_conf)

    def test_dental_clinic(self):
        """Dr. Smile Dental maps to dental_medical."""
        domain, conf, needs_conf = classify_business(None, "Dr. Smile Dental Clinic")
        self.assertEqual(domain, "dental_medical")
        self.assertFalse(needs_conf)

    def test_unknown_fails_closed_to_general_services(self):
        """Unknown business name without category fails closed to general_services with needs_confirmation."""
        domain, conf, needs_conf = classify_business(None, "Quantum Flux Syndicate")
        self.assertEqual(domain, "general_services")
        self.assertTrue(needs_conf)
        self.assertLessEqual(conf, 0.50)

    def test_the_crown_pub(self):
        """'The Crown Pub' must classify into restaurants_cafes."""
        domain, conf, needs_conf = classify_business(None, "The Crown Pub")
        self.assertEqual(domain, "restaurants_cafes")
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(conf, 0.85)

    def test_glow_beauty_studio(self):
        """'Glow Beauty Studio' must classify into salons_spas."""
        domain, conf, needs_conf = classify_business(None, "Glow Beauty Studio")
        self.assertEqual(domain, "salons_spas")
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(conf, 0.85)

    def test_violas_couture_with_tailor_category(self):
        """'Viola’s Couture' with Google category 'Tailor' maps to boutiques_retail."""
        domain, conf, needs_conf = classify_business("Tailor", "Viola’s Couture")
        self.assertEqual(domain, "boutiques_retail")
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(conf, 0.90)

    def test_violas_couture_name_alone(self):
        """'Viola’s Couture' with no category matches couture -> boutiques_retail."""
        domain, conf, needs_conf = classify_business(None, "Viola’s Couture")
        self.assertEqual(domain, "boutiques_retail")
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(conf, 0.85)

    def test_pet_groomers(self):
        """'Pet Groomers' maps to home_trade_services."""
        domain, conf, needs_conf = classify_business(None, "Pet Groomers")
        self.assertEqual(domain, "home_trade_services")
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(conf, 0.85)

    def test_autocare_with_nearby_restaurant_snippet_prefers_name(self):
        """'Autocare' must match trade/auto service even when snippet mentions a nearby Italian restaurant."""
        domain, conf, needs_conf = classify_business(
            None,
            "Autocare",
            "Located right next to the best Italian restaurant on High Street. Ample customer parking."
        )
        self.assertEqual(domain, "home_trade_services")
        self.assertFalse(needs_conf)
        self.assertGreaterEqual(conf, 0.85)

    def test_blue_door_with_coffee_snippet_fails_closed(self):
        """'Blue Door' with a casual coffee snippet must NOT classify as a cafe; must fail closed to general_services with needs_confirmation."""
        domain, conf, needs_conf = classify_business(
            None,
            "Blue Door",
            "Located across from the best coffee spot in London. Meeting space and private studios available."
        )
        self.assertEqual(domain, "general_services")
        self.assertTrue(needs_conf)
        self.assertLessEqual(conf, 0.50)

    def test_detect_domain_profile_shim(self):
        """detect_domain_profile shim returns correct domain string."""
        dom = detect_domain_profile("Apex Performance Gym", "London", "crossfit and strength training")
        self.assertEqual(dom, "gyms_fitness")


if __name__ == "__main__":
    unittest.main()
