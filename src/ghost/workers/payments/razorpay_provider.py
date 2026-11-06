"""Razorpay Payment Provider for Ghost (Part 5).
Implements the PaymentProvider protocol for Razorpay.
Supports International Virtual Accounts (MoneySaver Export Account), honest
payment-link handling (real links only — a fabricated URL is a lie), and HMAC
webhook verification with event deduplication.
Ghost only prepares links and parses incoming webhook suggestions; nothing is auto-sent or auto-charged.
"""
from typing import Dict, Any, Optional, Set
from decimal import Decimal
import os
import json
import hmac
import hashlib
import logging
import re

from ghost.workers.payments import PaymentEvent
from ghost.crm.db import CRMDatabase

logger = logging.getLogger("ghost.payments.razorpay")


def render_qr_png(data: str, out_path: str) -> Optional[str]:
    """Deterministic scan-to-pay QR for a REAL payment target (a Razorpay
    short_url or a UPI deep-link). Never called for a fabricated URL —
    there are none anymore. Library missing -> honest skip, operator pinged."""
    from ghost.core.audit import audit
    try:
        import qrcode   # pip install qrcode (PIL-backed)
    except ImportError:
        audit("payment.qr_unavailable",
              ping="(c) unverifiable: the `qrcode` package is missing — "
                   "run: pip install --user --break-system-packages qrcode")
        return None
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img = qrcode.make(data, box_size=10, border=2)
    img.save(out_path, format="PNG")
    return out_path

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
        # upi_id / payee name live at the TOP level of payments.json, outside
        # the razorpay subdict — load them honestly, absent = no UPI rail
        self.payments_cfg = self._load_upi_config()

    def _load_upi_config(self) -> Dict[str, Any]:
        """upi_id / payee name live at the top level of payments.json."""
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                full = json.load(f)
            return {k: full.get(k) for k in ("upi_id", "upi_payee_name", "bank_details_text")}
        except Exception:
            return {}

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
            if link_url.startswith("upi://"):
                lines.append(f"**UPI Payment (scan the attached QR or tap on mobile):**\n{link_url}\n")
            else:
                lines.append(f"**Direct Online Payment Link (Razorpay):**\n{link_url}\n")
            if invoice.get("payment_qr_path"):
                lines.append(f"- QR code file: `{invoice['payment_qr_path']}` (scan to pay)\n")
        else:
            lines.append("Online payment link: PENDING — configure Razorpay API keys "
                         "(🛡️ Operator Control → razorpay) or set your UPI ID in "
                         "configs/payments.json before a real link can be issued. "
                         "Never send a link the provider did not create.\n")

        lines.append(f"- **Invoice Reference:** {inv_num}")
        return "\n".join(lines).strip()

    # ------------------------------------------------- P4: real collection rails

    def _api_keys(self) -> tuple:
        from ghost.core.secrets import get
        key_id = get(self.config.get("key_id_env", "RAZORPAY_KEY_ID"))
        key_secret = get(self.config.get("key_secret_env", "RAZORPAY_KEY_SECRET"))
        return key_id, key_secret

    @staticmethod
    def _http_transport(url: str, payload: Dict[str, Any],
                        key_id: str, key_secret: str) -> tuple:
        """The only network call in the provider. Injectable in tests."""
        import base64
        import urllib.request
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Basic " + base64.b64encode(
                         f"{key_id}:{key_secret}".encode()).decode()})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())

    def _upi_uri(self, invoice: Dict[str, Any]) -> Optional[str]:
        """Deterministic UPI deep-link from the operator's VPA. A REAL
        payment request — honest about being the no-API fallback rail.
        Same invoice -> same URI -> same QR."""
        vpa = self.payments_cfg.get("upi_id") or None
        if not vpa:
            return None
        from urllib.parse import quote
        amt = Decimal(str(invoice.get("amount") or invoice.get("total_price") or 0))
        params = {
            "pa": str(vpa),
            "pn": str(self.payments_cfg.get("upi_payee_name") or "Pranav Studio"),
            "am": str(amt.quantize(Decimal("0.01"))),
            "cu": "INR",
            "tn": str(invoice.get("invoice_number", "Invoice")),
        }
        return "upi://pay?" + "&".join(f"{k}={quote(str(v), safe='')}"
                                       for k, v in params.items())

    def create_payment_link(self, invoice: Dict[str, Any],
                            transport=None) -> Optional[str]:
        """P4 (Operator's 11): REAL collection rails — never a fabricated URL.

        INR + configured keys -> Razorpay Payment Links API (real short_url;
        notes carry invoice_number/lead_id so webhook events match invoices).
        INR + no keys -> deterministic local UPI URI from configs/payments.json
        (upi_id) — a real payment request through the operator's own VPA.
        Anything else -> None: the bank-transfer instructions remain the path.
        The link is NEVER sent by Ghost — it lands in drafts the operator sends.
        """
        from ghost.core.audit import audit
        currency = str(invoice.get("currency", "INR")).upper()
        if currency != "INR":
            audit("payment.link_skipped", subject=invoice.get("invoice_number"),
                  reason=f"currency {currency} uses bank-transfer instructions")
            return None

        key_id, key_secret = self._api_keys()
        if key_id and key_secret:
            amt = Decimal(str(invoice.get("amount") or invoice.get("total_price") or 0))
            payload = {
                "amount": int(amt.quantize(Decimal("0.01")) * 100),  # paise
                "currency": "INR",
                "accept_partial": False,
                "description": f"Invoice {invoice.get('invoice_number', 'Invoice')}",
                "notes": {"invoice_number": str(invoice.get("invoice_number", "")),
                          "lead_id": str(invoice.get("lead_id", ""))},
            }
            customer: Dict[str, Any] = {}
            for src, dst in (("client_name", "name"), ("client_email", "email"),
                             ("client_phone", "contact")):
                if invoice.get(src):
                    customer[dst] = str(invoice[src])
            if customer:
                payload["customer"] = customer
            try:
                status, body = (transport or self._http_transport)(
                    "https://api.razorpay.com/v1/payment_links",
                    payload, key_id, key_secret)
                if status // 100 != 2 or not body.get("short_url"):
                    raise ValueError(f"HTTP {status}: {str(body)[:160]}")
                audit("payment.link_created_real", subject=invoice.get("invoice_number"),
                      link_id=str(body.get("id", ""))[:40], rail="razorpay_api")
                return body["short_url"]
            except Exception as e:
                audit("payment.link_api_failed", subject=invoice.get("invoice_number"),
                      error=str(e)[:200],
                      ping="(c) unverifiable: Razorpay link creation FAILED — no link was issued")
                return None

        uri = self._upi_uri(invoice)
        if uri:
            audit("payment.upi_uri_issued", subject=invoice.get("invoice_number"),
                  rail="upi_fallback", vpa=self.payments_cfg.get("upi_id"))
            return uri
        audit("payment.link_not_configured", subject=invoice.get("invoice_number"),
              ping="(c) unverifiable: no Razorpay keys and no UPI ID — payment link pending")
        return None

    def attach_payment_artifacts(self, invoice: Dict[str, Any],
                                 out_dir: str) -> Dict[str, Any]:
        """One-stop for the closing pack: resolve the real rail, render the
        scan-to-pay QR when there is something real to scan, and stamp the
        invoice dict with exactly what happened."""
        link = self.create_payment_link(invoice)
        rail = None
        qr_path = None
        if link:
            rail = "razorpay_payment_link" if link.startswith("https://") else "upi_qr"
            inv_num = re.sub(r"[^\w.-]", "-", str(invoice.get("invoice_number", "inv")))
            qr_path = render_qr_png(link, os.path.join(out_dir, f"pay-{inv_num}-qr.png"))
        invoice["payment_link"] = link
        invoice["payment_rail"] = rail
        invoice["payment_qr_path"] = qr_path
        return {"link": link, "rail": rail, "qr_path": qr_path}

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
