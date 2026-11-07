"""Tests for generated interactive booking module and JavaScript logic."""
import asyncio
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from ghost.workers.demo_state_machine import AutonomousDemoStateMachine


class TestBookingJS(unittest.TestCase):

    def test_booking_js_with_uk_landline(self):
        """When phone is a UK landline, button points to tel: link."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="Topaz Detailing London",
                    location="London, UK",
                    slug="topaz-detailing-london"
                )
                sm.brand_data = {
                    "business_name": "Topaz Detailing",
                    "location": "London, UK",
                    "phone": "+44 20 7946 0912",
                    "email": None,
                    "domain": "home_trade_services",
                    "copy": self._get_copy("home_trade_services"),
                    "assets": self._get_assets("home_trade_services"),
                    "reviews": []
                }

                html = asyncio.run(sm.run_phase3_build_sections())
                # Check for JS business initialization
                self.assertIn('"isLandline": true', html)
                self.assertIn('href="tel:+44 20 7946 0912"', html)
                self.assertIn('Call Directly:', html)
                self.assertIn('booking-service', html)
                self.assertIn('booking-detail', html)

    def test_booking_js_with_mobile_number(self):
        """When phone is a mobile number, button initializes with WhatsApp link."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="Bengaluru Roastery",
                    location="Bengaluru, India",
                    slug="bengaluru-roastery"
                )
                sm.brand_data = {
                    "business_name": "Bengaluru Roastery",
                    "location": "Bengaluru, India",
                    "phone": "+91 98450 12345",
                    "email": None,
                    "domain": "restaurants_cafes",
                    "copy": self._get_copy("restaurants_cafes"),
                    "assets": self._get_assets("restaurants_cafes"),
                    "reviews": []
                }

                html = asyncio.run(sm.run_phase3_build_sections())
                self.assertIn('"isLandline": false', html)
                self.assertIn('https://wa.me/919845012345', html)
                self.assertIn('Instant WhatsApp Direct', html)
                self.assertIn('updateBookingLink', html)

    def test_booking_js_when_no_phone(self):
        """When phone is None, button displays clean inquiry without broken link."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ghost.workers.demo_state_machine.DEMOS_DIR", tmpdir):
                sm = AutonomousDemoStateMachine(
                    business_name="Mystery Studio",
                    location="London, UK",
                    slug="mystery-studio"
                )
                sm.brand_data = {
                    "business_name": "Mystery Studio",
                    "location": "London, UK",
                    "phone": None,
                    "email": None,
                    "domain": "general_services",
                    "copy": self._get_copy("general_services"),
                    "assets": self._get_assets("general_services"),
                    "reviews": []
                }

                html = asyncio.run(sm.run_phase3_build_sections())
                self.assertIn('"hasPhone": false', html)
                self.assertIn('href="#contact"', html)
                self.assertIn('[Inquire Directly]', html)

    @staticmethod
    def _get_copy(domain):
        from ghost.workers.demo_state_machine import DOMAIN_COPY
        return DOMAIN_COPY.get(domain, DOMAIN_COPY["general_services"])

    @staticmethod
    def _get_assets(domain):
        from ghost.workers.demo_state_machine import DOMAIN_CURATED_ASSETS, GENERAL_SERVICES_ASSETS
        return DOMAIN_CURATED_ASSETS.get(domain, GENERAL_SERVICES_ASSETS)


if __name__ == "__main__":
    unittest.main()
