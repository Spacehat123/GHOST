"""Payment Provider Interface & Implementations for Ghost (Part 5).
Strict human-in-the-loop: Ghost prepares payment instructions and drafts;
Ghost NEVER auto-charges, auto-sends links, or dispatches funds.
"""
from typing import Protocol, Optional, Dict, Any, runtime_checkable
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class PaymentEvent:
    event_id: str
    event_type: str
    amount: Decimal
    currency: str
    invoice_number: str
    lead_id: Optional[str]
    status: str
    raw_payload: Dict[str, Any]


@runtime_checkable
class PaymentProvider(Protocol):
    """Protocol defining the interface for all payment providers."""
    name: str

    def payment_instructions(self, invoice: Dict[str, Any]) -> str:
        """Text block for the invoice explaining how to pay."""
        ...

    def create_payment_link(self, invoice: Dict[str, Any]) -> Optional[str]:
        """Optional payment link draft. Returns a URL or None. Never sends it."""
        ...

    def verify_webhook(self, headers: Dict[str, str], body: bytes) -> bool:
        """Verifies incoming webhook signature."""
        ...

    def parse_event(self, body: bytes) -> PaymentEvent:
        """Parses webhook event body for mark-as-paid suggestions."""
        ...


from ghost.workers.payments.manual_bank import ManualBankProvider
from ghost.workers.payments.razorpay_provider import RazorpayProvider


def get_payment_provider(
    provider_name: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None
) -> PaymentProvider:
    """Factory helper to instantiate payment providers."""
    p_name = (provider_name or "manual_bank").lower().strip()
    if p_name == "razorpay":
        return RazorpayProvider(config=config)
    return ManualBankProvider(config=config)


__all__ = [
    "PaymentEvent",
    "PaymentProvider",
    "ManualBankProvider",
    "RazorpayProvider",
    "get_payment_provider"
]
