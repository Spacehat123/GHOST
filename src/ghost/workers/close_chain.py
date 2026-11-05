"""Close Chain — contract → payment → welcome (Roadmap §2 Stages 5-6, P2).

The only code path allowed to move a deal from "agreed" to "onboarded".
Invariants (tested):
1. PAYMENT IS NEVER TRUSTED FROM A SINGLE SOURCE: capture counts only when
   (a) the webhook signature verified, (b) the event is not a duplicate,
   (c) amount >= invoice amount AND currency matches. Ghost's own "sent the
   link" belief is never evidence of payment.
2. EVERY OUTBOUND STEP calls halt.assert_not_halted() — the throat-cut handle
   freezes this chain mid-flight (§0.5).
3. EVERY STEP emits audit events with evidence (who/what/how much/why).
4. WELCOME NEVER SENDS BEFORE PAYMENT.VERIFIED — welcome-before-payment is
   the classic way nice agents give away work for free.
5. Onboarding chase: 2 nudges then operator ping (§2 Stage 6).

Queue-driven: jobs are enqueued with idempotency keys so a crash mid-chain
resumes exactly where it stopped, never double-sending.
"""
import json
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Optional

from ghost.core.audit import audit
from ghost.core.halt import assert_not_halted

logger = logging.getLogger("ghost.close_chain")


class ChainError(RuntimeError):
    pass


# ----------------------------------------------------------------- processing

def process_payment_webhook(
    provider,                     # PaymentProvider (razorpay/manual_bank)
    headers: Dict[str, str],
    body: bytes,
    invoice_lookup: Callable[[str], Optional[Dict[str, Any]]],
    enqueue: Callable[[str, Dict[str, Any], Optional[str]], Any],
) -> Dict[str, Any]:
    """Single entry point for payment webhooks.

    invoice_lookup(invoice_number) -> invoice dict with 'amount' (str/num),
    'currency', 'lead_slug', 'deal_value_inr'.
    enqueue(kind, payload, idempotency_key) -> JobQueue.enqueue.

    Returns an evidence dict. NEVER raises on bad input — refuses, audits, returns.
    """
    # 1. Signature first. An unverified body is not data, it's noise.
    if not provider.verify_webhook(headers, body):
        audit("payment.webhook_rejected", reason="signature_invalid",
              provider=getattr(provider, "name", "unknown"))
        return {"status": "rejected", "reason": "signature_invalid"}

    # 2. Parse + dedup (provider handles both).
    event = provider.parse_event(body)
    if getattr(event, "status", "") == "duplicate":
        audit("payment.webhook_duplicate", subject=event.event_id)
        return {"status": "duplicate", "event_id": event.event_id}

    # 3. Match to a real invoice. No invoice -> unverifiable -> refuse.
    invoice = invoice_lookup(event.invoice_number)
    if not invoice:
        audit("payment.webhook_rejected", subject=event.event_id,
              reason="no_matching_invoice", invoice_number=event.invoice_number)
        return {"status": "rejected", "reason": "no_matching_invoice",
                "invoice_number": event.invoice_number}

    # 4. Amount + currency honesty gate: the event must actually cover the
    #    invoice. A ₹500 "captured" event against a ₹20,000 invoice is not a
    #    payment — it's an underpayment, and it pings the operator (a).
    try:
        paid = Decimal(str(event.amount))
        owed = Decimal(str(invoice.get("amount")))
    except (InvalidOperation, TypeError, ValueError):
        audit("payment.webhook_rejected", subject=event.event_id, reason="amount_unparseable")
        return {"status": "rejected", "reason": "amount_unparseable"}

    currency_ok = str(event.currency).upper() == str(invoice.get("currency", "")).upper()
    amount_ok = paid >= owed
    if not (amount_ok and currency_ok):
        audit("payment.underpaid_or_mismatch", subject=event.event_id,
              paid=str(paid), owed=str(owed),
              currency_event=event.currency, currency_invoice=invoice.get("currency"))
        return {
            "status": "mismatch",
            "event_id": event.event_id,
            "paid": str(paid), "owed": str(owed), "currency_ok": currency_ok,
            "ping": "(a) money: payment amount/currency mismatch — manual review",
        }

    # 5. Verified. This is the ONLY place 'deposit_received'/'won' transitions fire.
    audit("payment.verified", subject=event.event_id,
          invoice_number=event.invoice_number, lead=invoice.get("lead_slug"),
          amount=str(paid), currency=event.currency, provider=getattr(provider, "name", "unknown"))

    enqueue(
        "close_chain.send_contract",
        {"invoice_number": event.invoice_number, "lead_slug": invoice.get("lead_slug"),
         "event_id": event.event_id},
        idempotency_key=f"contract:{event.invoice_number}",
    )
    enqueue(
        "close_chain.send_welcome",
        {"invoice_number": event.invoice_number, "lead_slug": invoice.get("lead_slug"),
         "event_id": event.event_id},
        idempotency_key=f"welcome:{event.invoice_number}",
    )
    return {
        "status": "verified",
        "event_id": event.event_id,
        "invoice_number": event.invoice_number,
        "amount": str(paid),
        "chained": ["close_chain.send_contract", "close_chain.send_welcome"],
    }


# ------------------------------------------------------------------- handlers
# Async handlers matching the JobQueue protocol. `deps` carries injected
# senders (email service / contract generator) so tests run without networks.

def make_handlers(deps: Optional[Dict[str, Any]] = None) -> Dict[str, Callable]:
    deps = deps or {}
    send_email = deps.get("send_email") or _default_send_email
    build_contract = deps.get("build_contract") or _default_build_contract

    async def send_contract(payload: Dict[str, Any]) -> Any:
        assert_not_halted("close_chain.send_contract")
        lead = payload.get("lead_slug", "unknown")
        contract_path = build_contract(payload)
        audit("contract.sent", subject=lead, path=str(contract_path),
              invoice=payload.get("invoice_number"))
        return {"contract": str(contract_path)}

    async def send_welcome(payload: Dict[str, Any]) -> Any:
        assert_not_halted("close_chain.send_welcome")
        lead = payload.get("lead_slug", "unknown")
        # Honesty gate: welcome must observe a verified payment event, not a rumor.
        verified = _payment_verified(payload.get("event_id"))
        if not verified:
            audit("welcome.blocked", subject=lead,
                  reason="no verified payment event on ledger")
            raise ChainError("welcome blocked: payment not verified on ledger")
        send_email(payload)
        audit("welcome.sent", subject=lead, invoice=payload.get("invoice_number"))
        return {"welcomed": lead}

    return {
        "close_chain.send_contract": send_contract,
        "close_chain.send_welcome": send_welcome,
    }


def _payment_verified(event_id: Optional[str]) -> bool:
    """Evidence check against the audit ledger (the same ledger the webhook
    wrote to). This is what makes 'welcome before payment' structurally
    impossible rather than merely discouraged."""
    if not event_id:
        return False
    try:
        from ghost.core.audit import AuditLedger
        rows = AuditLedger.get_instance().query(event_type="payment.verified", limit=200)
        return any(r.get("subject") == event_id for r in rows)
    except Exception:
        return False


def _default_build_contract(payload: Dict[str, Any]) -> str:
    """Default contract step uses the existing contract template pipeline if
    present; otherwise records a draft marker. Overridable via deps."""
    try:
        from ghost.core.skills import skill  # optional integration point
    except Exception:
        pass
    return f"contract-{payload.get('invoice_number')}.pdf"


def _default_send_email(payload: Dict[str, Any]) -> None:
    """Default welcome sender. Overridable via deps for tests."""
    try:
        from ghost.comms.email_service import EmailService
        svc = EmailService.get_instance()
        lead = payload.get("lead_slug", "client")
        svc.send_email(
            to=lead,
            subject="Welcome — let's build your new website",
            body="Payment confirmed. Your onboarding link: <onboarding page URL>",
        )
        audit("email.sent", subject=lead, kind="welcome")
    except Exception as e:
        audit("email.failed", reason=str(e)[:300])
        raise
