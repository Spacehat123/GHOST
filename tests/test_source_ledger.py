"""Unit tests for SourceLedger and Fact Verification."""
import os
import tempfile
import unittest
from ghost.workers.source_ledger import SourceLedger, infer_currency_symbol


class TestSourceLedger(unittest.TestCase):

    def test_ledger_add_and_require(self):
        """Adding verified facts returns exact strings; missing facts return placeholders."""
        ledger = SourceLedger()
        ledger.add("business_name", "Topaz Detailing London", source_url="https://google.com/maps")
        ledger.add("phone", "+44 20 7946 0912", source_url="https://google.com/maps")
        ledger.add("missing_price", None)

        self.assertEqual(ledger.get("business_name"), "Topaz Detailing London")
        self.assertEqual(ledger.require("business_name"), "Topaz Detailing London")
        self.assertIsNone(ledger.get("missing_price"))
        self.assertEqual(ledger.require("missing_price", "[Inquire for quotation]"), "[Inquire for quotation]")

    def test_currency_symbol_inference(self):
        """Infers currency symbol from country and location."""
        self.assertEqual(infer_currency_symbol("London, UK"), "£")
        self.assertEqual(infer_currency_symbol("Chelsea, London"), "£")
        self.assertEqual(infer_currency_symbol("Bengaluru, India"), "₹")
        self.assertEqual(infer_currency_symbol("Mumbai, Maharashtra"), "₹")
        self.assertEqual(infer_currency_symbol("New York, NY"), "$")
        self.assertEqual(infer_currency_symbol("Paris, France"), "€")

    def test_ledger_persistence(self):
        """Ledger persists to JSON and reloads accurately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "SOURCES.json")
            ledger = SourceLedger()
            ledger.add("business_name", "Test Venue", source_url="maps")
            ledger.add("rating", "4.9")
            ledger.save(json_path)

            loaded = SourceLedger.load(json_path)
            self.assertEqual(loaded.get("business_name"), "Test Venue")
            self.assertEqual(loaded.get("rating"), "4.9")
            self.assertTrue(loaded.has("rating"))
            self.assertFalse(loaded.has("unadded_field"))


if __name__ == "__main__":
    unittest.main()
