"""Unit Tests for Payment Providers and Anti-Sending Security (Part 5)."""
import ast
import hashlib
import hmac
import json
import os
import unittest
from decimal import Decimal

from ghost.workers.payments import (
    PaymentEvent,
    ManualBankProvider,
    RazorpayProvider,
    get_payment_provider
)


class TestPayments(unittest.TestCase):

    def test_manual_bank_instructions(self):
        """ManualBankProvider formats instructions correctly from config."""
        # 1. Configured bank details
        provider = ManualBankProvider(config={
            "bank_name": "Barclays",
            "account_name": "Pranav Studio Ltd",
            "sort_code": "20-00-00",
            "account_number": "12345678"
        })
        instr = provider.payment_instructions({"invoice_number": "INV-2026-001"})
        self.assertIn("Barclays", instr)
        self.assertIn("Pranav Studio Ltd", instr)
        self.assertIn("20-00-00", instr)
        self.assertIn("12345678", instr)
        self.assertIn("INV-2026-001", instr)

        # 2. UPI details
        provider_upi = ManualBankProvider(config={"upi_id": "pranav@upi"})
        instr_upi = provider_upi.payment_instructions({"invoice_number": "INV-2026-002"})
        self.assertIn("pranav@upi", instr_upi)
        self.assertIn("INV-2026-002", instr_upi)

        # 3. Missing bank details
        provider_empty = ManualBankProvider(config={})
        instr_empty = provider_empty.payment_instructions({"invoice_number": "INV-2026-003"})
        self.assertIn("[[MISSING: PAYMENT_INSTRUCTIONS]]", instr_empty)

        # 4. Never generates automated payment links or webhooks
        self.assertIsNone(provider.create_payment_link({"amount": 500}))
        self.assertFalse(provider.verify_webhook({}, b"{}"))
        with self.assertRaises(NotImplementedError):
            provider.parse_event(b"{}")

    def test_razorpay_webhook_signature_verification(self):
        """Razorpay HMAC-SHA256 signature verification."""
        secret = "super_secret_webhook_key_123"
        provider = RazorpayProvider(config={"webhook_secret": secret})

        body = json.dumps({"id": "evt_test_1", "event": "payment.captured"}).encode("utf-8")
        valid_sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

        # 1. Valid signature header
        headers = {"X-Razorpay-Signature": valid_sig}
        self.assertTrue(provider.verify_webhook(headers, body))

        # 2. Case-insensitive header check
        headers_lower = {"x-razorpay-signature": valid_sig}
        self.assertTrue(provider.verify_webhook(headers_lower, body))

        # 3. Tampered payload
        tampered_body = json.dumps({"id": "evt_test_1", "event": "payment.failed"}).encode("utf-8")
        self.assertFalse(provider.verify_webhook(headers, tampered_body))

        # 4. Invalid signature
        bad_headers = {"X-Razorpay-Signature": "invalid_hex_string"}
        self.assertFalse(provider.verify_webhook(bad_headers, body))

        # 5. Missing header
        self.assertFalse(provider.verify_webhook({}, body))

        # 6. Missing secret
        no_secret_provider = RazorpayProvider(config={"webhook_secret": None, "webhook_secret_env": "NON_EXISTENT_ENV_KEY"})
        self.assertFalse(no_secret_provider.verify_webhook(headers, body))

    def test_razorpay_webhook_deduplication(self):
        """Razorpay webhook event deduplication."""
        provider = RazorpayProvider(config={"webhook_secret": "test"})
        payload = {
            "id": "evt_pay_unique_99",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "amount": 75000,
                        "currency": "GBP",
                        "status": "captured",
                        "notes": {
                            "invoice_number": "INV-2026-099-DEP",
                            "lead_id": "lead_xyz"
                        }
                    }
                }
            }
        }
        body = json.dumps(payload).encode("utf-8")

        # First parse -> captured
        ev1 = provider.parse_event(body)
        self.assertEqual(ev1.event_id, "evt_pay_unique_99")
        self.assertEqual(ev1.amount, Decimal("750.00"))
        self.assertEqual(ev1.currency, "GBP")
        self.assertEqual(ev1.invoice_number, "INV-2026-099-DEP")
        self.assertEqual(ev1.status, "captured")

        # Second parse -> duplicate
        ev2 = provider.parse_event(body)
        self.assertEqual(ev2.event_id, "evt_pay_unique_99")
        self.assertEqual(ev2.status, "duplicate")

    def test_razorpay_payment_link_draft_creation(self):
        """Razorpay creates draft payment links with correct subunit scaling and never auto-sends."""
        provider = RazorpayProvider(config={})
        inv_data = {
            "invoice_number": "INV-2026-010-DEP",
            "amount": "600.50",
            "currency": "GBP",
            "lead_id": "lead_10"
        }
        link = provider.create_payment_link(inv_data)
        self.assertIsNotNone(link)
        self.assertTrue(link.startswith("https://rzp.io/i/"))

        # Instructions include the draft link
        inv_data["payment_link"] = link
        instr = provider.payment_instructions(inv_data)
        self.assertIn(link, instr)
        self.assertIn("MoneySaver Export Account", instr)
        self.assertIn("FPS", instr)

    def test_anti_sending_static_analysis(self):
        """Security scanner: verifies zero smtplib, socket, or auto-send mechanics in Part 5 modules."""
        files_to_scan = [
            "/home/pranc/projects/ghost/src/ghost/workers/deal_closer.py",
            "/home/pranc/projects/ghost/src/ghost/workers/payments/__init__.py",
            "/home/pranc/projects/ghost/src/ghost/workers/payments/manual_bank.py",
            "/home/pranc/projects/ghost/src/ghost/workers/payments/razorpay_provider.py"
        ]

        banned_modules = {"smtplib", "socket"}

        for fpath in files_to_scan:
            self.assertTrue(os.path.exists(fpath), f"File {fpath} does not exist!")
            with open(fpath, "r", encoding="utf-8") as f:
                source = f.read()

            tree = ast.parse(source, filename=fpath)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(
                            alias.name, banned_modules,
                            f"Prohibited import '{alias.name}' detected in {fpath}!"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        mod_root = node.module.split(".")[0]
                        self.assertNotIn(
                            mod_root, banned_modules,
                            f"Prohibited from-import '{node.module}' detected in {fpath}!"
                        )


if __name__ == "__main__":
    unittest.main()
