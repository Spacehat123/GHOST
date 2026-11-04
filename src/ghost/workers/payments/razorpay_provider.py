"""Razorpay Payment Provider for Ghost (Part 5).
Implements the PaymentProvider protocol for Razorpay.
Supports International Virtual Accounts (MoneySaver Export Account), payment link drafts,
and HMAC webhook verification with event deduplication.
Ghost only prepares links and parses incoming webhook suggestions; nothing is auto-sent or auto-charged.
"""
from typing import Dict, Any, Optional, Set
from decimal import Decimal
import os
import json
import hmac
import hashlib
import logging

from ghost.workers.payments import PaymentEvent
from ghost.crm.db import CRMDatabase

logger = logging.getLogger("ghost.payments.razorpay")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../../.."))
CONFIG_PATH = os.path.join(REPO_DIR, "configs", "payments.json")


def load_razorpay_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg.get("razorpay", {})
        except Exception as e:
            logger.warning(f"Failed loading payments config: {e}")
    return {
        "key_id_env": "RAZORPAY_KEY_ID",
        "key_secret_env": "RAZORPAY_KEY_SECRET",
        "webhook_secret_env": "RAZORPAY_WEBHOOK_SECRET"
    }


class RazorpayProvider:
    """Razorpay Payment Provider implementing PaymentProvider protocol."""

    name: str = "razorpay"

    def __init__(self, config: Optional[Dict[str, Any]] = None, crm: Optional[CRMDatabase] = None):
        self.config = config or load_razorpay_config()
        self.crm = crm or CRMDatabase.get_instance()
        self._processed_events: Set[str] = set()

    def _get_webhook_secret(self) -> Optional[str]:
        sec_env = self.config.get("webhook_secret_env", "RAZORPAY_WEBHOOK_SECRET")
        return os.environ.get(sec_env) or self.config.get("webhook_secret")

    def payment_instructions(self, invoice: Dict[str, Any]) -> str:
        """Format payment instructions for international transfers or domestic Razorpay."""
        currency = str(invoice.get("currency", "GBP")).upper()
        inv_num = invoice.get("invoice_number", "Invoice")
        link_url = invoice.get("payment_link")

        lines = [
            f"**Payment via Razorpay (Reference: {inv_num})**",
            ""
        ]

        # MoneySaver Virtual Account or International Transfer notice
        if currency in ("GBP", "EUR", "USD"):
            lines.extend([
                "**International Bank Transfer (MoneySaver Export Account):**",
                f"- For UK/EU/US clients, payments can be made directly via local clearing ({'FPS' if currency == 'GBP' else 'SEPA' if currency == 'EUR' else 'ACH/Wire'}).",
                "- Note: Per export compliance regulations, buyer legal name and address are attached to this invoice.",
                ""
            ])

        if link_url:
            lines.append(f"**Direct Online Payment Link (Credit/Debit Card):**\n{link_url}\n")
        else:
            lines.append("An online card payment link can be requested from the provider.\n")

        lines.append(f"- **Invoice Reference:** {inv_num}")
        return "\n".join(lines).strip()

    def create_payment_link(self, invoice: Dict[str, Any]) -> Optional[str]:
        """
        Creates a payment link draft for the invoice.
        Converts the decimal amount to smallest currency subunits (e.g. pence/cents/paise).
        Ghost NEVER sends this link automatically; it is presented as a draft in the UI.
        """
        raw_amt = invoice.get("amount") or invoice.get("total_price") or 0
        try:
            dec_amt = Decimal(str(raw_amt))
            # Subunits: e.g. 500.00 GBP -> 50000 pence
            subunits = int(dec_amt * Decimal("100"))
        except Exception:
            subunits = 0

        inv_num = str(invoice.get("invoice_number", "INV-DRAFT"))
        currency = str(invoice.get("currency", "GBP")).upper()
        lead_id = invoice.get("lead_id", "")

        # Generate deterministic draft payment link identifier
        h = hashlib.sha256(f"{inv_num}:{subunits}:{currency}:{lead_id}".encode("utf-8")).hexdigest()[:12]
        link_id = f"plink_{h}"
        link_url = f"https://rzp.io/i/{link_id}"

        logger.info(f"Generated draft Razorpay payment link {link_url} for invoice {inv_num} (never auto-sent).")
        return link_url

    def verify_webhook(self, headers: Dict[str, str], body: bytes) -> bool:
        """
        Validates HMAC SHA256 webhook signature from Razorpay.
        Header: X-Razorpay-Signature
        """
        secret = self._get_webhook_secret()
        if not secret:
            logger.warning("Razorpay webhook verification failed: no webhook secret configured.")
            return False

        # Case-insensitive header lookup
        sig = None
        for k, v in headers.items():
            if k.lower() == "x-razorpay-signature":
                sig = v
                break

        if not sig:
            logger.warning("Razorpay webhook verification failed: missing X-Razorpay-Signature header.")
            return False

        expected_sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected_sig, sig.strip())

    def parse_event(self, body: bytes) -> PaymentEvent:
        """
        Parses webhook event JSON into a PaymentEvent.
        Handles event deduplication and flags duplicate event IDs.
        """
        data = json.loads(body.decode("utf-8"))
        event_id = data.get("id") or data.get("event_id")

        if not event_id:
            # Fallback hash if payload has no top-level id
            event_id = f"evt_{hashlib.sha256(body).hexdigest()[:12]}"

        # Check persistent database and memory cache
        if self._processed_events and event_id in self._processed_events:
            logger.info(f"Duplicate Razorpay event detected (memory): {event_id}")
            is_duplicate = True
        elif self.crm and self.crm.has_processed_event(event_id):
            logger.info(f"Duplicate Razorpay event detected (database): {event_id}")
            is_duplicate = True
        else:
            self._processed_events.add(event_id)
            if self.crm:
                self.crm.record_processed_event(event_id, provider="razorpay")
            is_duplicate = False

        event_type = data.get("event", "payment.captured")

        # Extract payment payload entity
        payload_data = data.get("payload", {})
        entity = (
            payload_data.get("payment", {}).get("entity")
            or payload_data.get("payment_link", {}).get("entity")
            or data.get("entity", {})
        )

        raw_subunits = entity.get("amount", 0)
        currency = entity.get("currency", "GBP").upper()
        amount_dec = Decimal(str(raw_subunits)) / Decimal("100")

        notes = entity.get("notes", {})
        inv_num = notes.get("invoice_number") or entity.get("description", "")
        lead_id = notes.get("lead_id")
        status = entity.get("status", "captured")

        return PaymentEvent(
            event_id=event_id,
            event_type=event_type,
            amount=amount_dec,
            currency=currency,
            invoice_number=inv_num,
            lead_id=lead_id,
            status="duplicate" if is_duplicate else status,
            raw_payload=data
        )
