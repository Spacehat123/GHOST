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

    def test_detect_domain_profile_shim(self):
        """detect_domain_profile shim returns correct domain string."""
        dom = detect_domain_profile("Apex Performance Gym", "London", "crossfit and strength training")
        self.assertEqual(dom, "gyms_fitness")


if __name__ == "__main__":
    unittest.main()
