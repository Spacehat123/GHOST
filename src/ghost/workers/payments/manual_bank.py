"""Manual Bank Transfer Payment Provider for Ghost (Part 5).
Default payment provider with zero dependencies and zero auto-dispatching.
Provides human-verified bank and transfer instructions for invoices.
"""
from typing import Dict, Any, Optional
import json
import os
import logging

logger = logging.getLogger("ghost.payments.manual_bank")

CONFIG_PATH = "/home/pranc/projects/ghost/configs/payments.json"


def load_payments_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed loading payments config from {CONFIG_PATH}: {e}")
    return {"provider": "manual_bank", "bank_details_text": None, "upi_id": None}


class ManualBankProvider:
    """Manual Bank Transfer payment instructions provider."""

    name: str = "manual_bank"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or load_payments_config()

    def payment_instructions(self, invoice: Dict[str, Any]) -> str:
        """Format bank transfer and payment instructions for an invoice."""
        # 1. Direct text override from config
        custom_text = self.config.get("bank_details_text")
        if custom_text and custom_text.strip():
            return custom_text.strip()

        # 2. UPI details if present
        upi_id = self.config.get("upi_id")
        if upi_id and upi_id.strip():
            return f"Please transfer payment via UPI to: **{upi_id.strip()}** (Reference: {invoice.get('invoice_number', 'Invoice')})"

        # 3. Formatted bank fields if present
        bank_name = self.config.get("bank_name")
        acc_name = self.config.get("account_name")
        sort_code = self.config.get("sort_code")
        acc_num = self.config.get("account_number")
        iban = self.config.get("iban")
        swift = self.config.get("swift_bic")

        lines = ["Please transfer the total amount via direct bank transfer using the following details:"]
        has_detail = False

        if bank_name:
            lines.append(f"- **Bank:** {bank_name}")
            has_detail = True
        if acc_name:
            lines.append(f"- **Account Name:** {acc_name}")
            has_detail = True
        if sort_code:
            lines.append(f"- **Sort Code:** {sort_code}")
            has_detail = True
        if acc_num:
            lines.append(f"- **Account Number:** {acc_num}")
            has_detail = True
        if iban:
            lines.append(f"- **IBAN:** {iban}")
            has_detail = True
        if swift:
            lines.append(f"- **SWIFT / BIC:** {swift}")
            has_detail = True

        inv_num = invoice.get("invoice_number", "Invoice Reference")
        lines.append(f"- **Payment Reference:** {inv_num}")

        if not has_detail:
            return "[[MISSING: PAYMENT_INSTRUCTIONS]] (Please configure bank details in configs/payments.json)"

        return "\n".join(lines)

    def create_payment_link(self, invoice: Dict[str, Any]) -> Optional[str]:
        """Manual bank transfer does not generate automated payment links."""
        return None

    def verify_webhook(self, headers: Dict[str, str], body: bytes) -> bool:
        """Manual bank provider does not accept automated webhooks."""
        return False

    def parse_event(self, body: bytes) -> Any:
        """Not implemented for manual bank provider."""
        raise NotImplementedError("Manual bank provider does not process webhooks.")
