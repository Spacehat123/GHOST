"""Unit Tests for Payment Providers and Anti-Sending Security (Part 5)."""
import ast
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from decimal import Decimal

from ghost.crm.db import CRMDatabase
from ghost.workers.payments import (
    PaymentEvent,
    ManualBankProvider,
    RazorpayProvider,
    get_payment_provider
)


class TestPayments(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_payments_crm.db")
        self.crm = CRMDatabase(db_path=self.db_path)
        CRMDatabase._instance = self.crm

    def tearDown(self):
        self.tmpdir.cleanup()
        CRMDatabase._instance = None

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

        # Third parse after simulated server restart (fresh provider instance) -> still duplicate (persistent CRM storage)
        provider_fresh = RazorpayProvider(config={"webhook_secret": "test"})
        ev3 = provider_fresh.parse_event(body)
        self.assertEqual(ev3.event_id, "evt_pay_unique_99")
        self.assertEqual(ev3.status, "duplicate")

    def test_razorpay_payment_link_is_never_fabricated(self):
        """P1 honesty fix: the old code minted deterministic `rzp.io` URLs that
        pointed nowhere — a lie in a draft a human might send. Until the real
        Payment Links API is wired (Operator's 11 P4), NO link is emitted and
        the instructions say so."""
        provider = RazorpayProvider(config={})
        inv_data = {
            "invoice_number": "INV-2026-010-DEP",
            "amount": "600.50",
            "currency": "GBP",
            "lead_id": "lead_10"
        }
        link = provider.create_payment_link(inv_data)
        self.assertIsNone(link, "no link may be fabricated before the API is wired")

        # Instructions honestly say the link is pending, and contain no URL
        instr = provider.payment_instructions(inv_data)
        self.assertIn("PENDING", instr)
        self.assertNotIn("https://rzp.io", instr)
        self.assertIn("MoneySaver Export Account", instr)
        self.assertIn("FPS", instr)

        # A REAL link (injected, as the API integration will provide) still renders
        inv_data["payment_link"] = "https://rzp.io/i/real-from-api"
        instr_real = provider.payment_instructions(inv_data)
        self.assertIn("https://rzp.io/i/real-from-api", instr_real)

    def test_anti_sending_static_analysis(self):
        """Security scanner: verifies zero smtplib, socket, or auto-send mechanics in Part 5 modules."""
        repo_dir = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), ".."))
        files_to_scan = [
            os.path.join(repo_dir, "src/ghost/workers/deal_closer.py"),
            os.path.join(repo_dir, "src/ghost/workers/payments/__init__.py"),
            os.path.join(repo_dir, "src/ghost/workers/payments/manual_bank.py"),
            os.path.join(repo_dir, "src/ghost/workers/payments/razorpay_provider.py"),
            os.path.join(repo_dir, "src/ghost/comms/email_service.py")
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


class TestRazorpayRealRails(unittest.TestCase):
    """P4 (Operator's 11): REAL collection rails. No fabricated URL may ever
    exist; keys absent -> honest fallback (UPI) or honest None; the QR is a
    deterministic scan-to-pay image of something real."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self._saved = {k: os.environ.get(k) for k in
                       ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "GHOST_UPI_ID")}
        for k in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "GHOST_UPI_ID"):
            os.environ.pop(k, None)
        self.out_dir = self.tmpdir.name

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmpdir.cleanup()

    def _provider(self, upi_id=None):
        cfg = {"key_id_env": "RAZORPAY_KEY_ID", "key_secret_env": "RAZORPAY_KEY_SECRET"}
        p = RazorpayProvider(config=cfg, crm=self.crm if hasattr(self, "crm") else None)
        p.payments_cfg = {"upi_id": upi_id, "upi_payee_name": "Pranav Studio"}
        return p

    def _invoice(self, **over):
        inv = {"invoice_number": "INV-2026-070-DEP", "amount": "5000.00",
               "currency": "INR", "lead_id": "lead_10",
               "client_name": "Salt Hospitality Ltd"}
        inv.update(over)
        return inv

    def test_real_link_via_api(self):
        calls = []

        def transport(url, payload, key_id, key_secret):
            calls.append((url, payload, key_id, key_secret))
            return 200, {"id": "plink_real123", "short_url": "https://rzp.io/i/real123"}

        os.environ["RAZORPAY_KEY_ID"] = "rzp_test_kid"
        os.environ["RAZORPAY_KEY_SECRET"] = "rzp_test_ksec"
        link = self._provider().create_payment_link(self._invoice(), transport=transport)
        self.assertEqual(link, "https://rzp.io/i/real123")
        url, payload, kid, ksec = calls[0]
        self.assertEqual(url, "https://api.razorpay.com/v1/payment_links")
        self.assertEqual(payload["amount"], 500000, "Decimal-exact paise")
        self.assertEqual(payload["currency"], "INR")
        self.assertEqual(payload["notes"]["invoice_number"], "INV-2026-070-DEP",
                         "webhook events must be able to match this invoice")
        self.assertEqual(payload["notes"]["lead_id"], "lead_10")
        self.assertEqual(payload["customer"]["name"], "Salt Hospitality Ltd")
        self.assertEqual(kid, "rzp_test_kid")

    def test_api_failure_is_honest_no_link(self):
        def transport(url, payload, kid, ksec):
            return 500, {"error": "provider down"}

        os.environ["RAZORPAY_KEY_ID"] = "rzp_test_kid"
        os.environ["RAZORPAY_KEY_SECRET"] = "rzp_test_ksec"
        link = self._provider(upi_id="studio@upi").create_payment_link(
            self._invoice(), transport=transport)
        self.assertIsNone(link, "API failure must not silently fall back or fabricate")

    def test_upi_fallback_deterministic(self):
        p = self._provider(upi_id="studio@upi")
        l1 = p.create_payment_link(self._invoice())
        l2 = p.create_payment_link(self._invoice())
        self.assertTrue(l1.startswith("upi://pay?pa=studio%40upi"))
        self.assertIn("am=5000.00", l1)
        self.assertIn("cu=INR", l1)
        self.assertIn("tn=INV-2026-070-DEP", l1)
        self.assertEqual(l1, l2, "same invoice -> same URI -> same QR")

    def test_no_keys_no_upi_honest_none(self):
        link = self._provider(upi_id=None).create_payment_link(self._invoice())
        self.assertIsNone(link)

    def test_non_inr_uses_bank_instructions_not_links(self):
        link = self._provider(upi_id="studio@upi").create_payment_link(
            self._invoice(currency="GBP"))
        self.assertIsNone(link, "UPI is INR-only; GBP keeps bank instructions")

    def test_attach_renders_deterministic_qr(self):
        from ghost.workers.payments.razorpay_provider import render_qr_png
        p = self._provider(upi_id="studio@upi")
        inv = self._invoice()
        art = p.attach_payment_artifacts(inv, self.out_dir)
        self.assertEqual(art["rail"], "upi_qr")
        self.assertTrue(os.path.exists(art["qr_path"]))
        # deterministic: an independent render of the same URI is byte-identical
        again = render_qr_png(inv["payment_link"], os.path.join(self.out_dir, "again.png"))
        self.assertEqual(open(art["qr_path"], "rb").read(),
                         open(again, "rb").read())
        # instructions reference the QR artifact
        instr = p.payment_instructions(inv)
        self.assertIn("QR", instr)
        self.assertIn("upi://", instr)

    def test_qr_of_real_api_link(self):
        os.environ["RAZORPAY_KEY_ID"] = "rzp_test_kid"
        os.environ["RAZORPAY_KEY_SECRET"] = "rzp_test_ksec"

        def transport(url, payload, kid, ksec):
            return 200, {"id": "plink_1", "short_url": "https://rzp.io/i/scan-me"}

        p = self._provider()
        inv = self._invoice()
        # attach with the API transport injected (mirrors the live path)
        original_create = p.create_payment_link
        p.create_payment_link = lambda invoice, transport=transport: original_create(invoice, transport=transport)
        art = p.attach_payment_artifacts(inv, self.out_dir)
        self.assertEqual(art["rail"], "razorpay_payment_link")
        self.assertEqual(art["link"], "https://rzp.io/i/scan-me")
        self.assertTrue(os.path.exists(art["qr_path"]),
                        "the QR encodes the REAL short_url — scan-to-pay")
        instr = p.payment_instructions(inv)
        self.assertIn("https://rzp.io/i/scan-me", instr)

    def test_qr_library_missing_is_honest(self):
        import sys
        from ghost.workers.payments import razorpay_provider as rp
        saved = sys.modules.get("qrcode")
        sys.modules["qrcode"] = None   # import raises ImportError
        try:
            self.assertIsNone(rp.render_qr_png("upi://pay?pa=x@y", os.path.join(self.out_dir, "n.png")))
        finally:
            if saved is None:
                sys.modules.pop("qrcode", None)
            else:
                sys.modules["qrcode"] = saved


if __name__ == "__main__":
    unittest.main()
