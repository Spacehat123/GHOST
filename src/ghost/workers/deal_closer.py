"""Deal Closer & Document Drafter for Ghost (Part 5).
Generates strictly grounded, watermarked contract drafts, welcome kits, onboarding questionnaires,
and deposit/balance invoices with exact decimal arithmetic.
Strict human-in-the-loop: Ghost prepares drafts; Pranav verifies and sends.
Nothing is auto-sent, auto-signed, or auto-charged.
"""
from typing import Dict, Any, List, Optional, Tuple, Set
from decimal import Decimal, ROUND_HALF_UP
import os
import json
import re
import time
import datetime
import logging

from jinja2 import Environment, FileSystemLoader

try:
    import docx
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

from ghost.crm.db import CRMDatabase
from ghost.workers.demo_state_machine import sanitize_outbound
from ghost.workers.payments import get_payment_provider, PaymentProvider

logger = logging.getLogger("ghost.deal_closer")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
CONFIG_DEAL_PATH = os.environ.get("GHOST_DEAL_CONFIG", os.path.join(REPO_DIR, "configs/deal.json"))
TEMPLATES_DIR = os.environ.get("GHOST_TEMPLATES_DIR", os.path.join(REPO_DIR, "templates"))
DOCUMENTS_BASE_DIR = os.environ.get("GHOST_DOCUMENTS_DIR", os.path.join(REPO_DIR, "data/documents"))

MANDATORY_LEGAL_TOKENS = [
    "CLIENT_LEGAL_NAME",
    "CLIENT_ADDRESS",
    "TOTAL_PRICE",
    "GOVERNING_LAW",
    "PROVIDER_NAME",
    "PROVIDER_ADDRESS"
]

CURRENCY_SYMBOLS: Dict[str, str] = {
    "GBP": "£",
    "USD": "$",
    "EUR": "€",
    "INR": "₹",
    "CAD": "CAD $",
    "AUD": "AUD $",
    "JPY": "¥"
}

COUNTRY_CURRENCIES: Dict[str, Tuple[str, str]] = {
    "uk": ("GBP", "£"),
    "united kingdom": ("GBP", "£"),
    "great britain": ("GBP", "£"),
    "london": ("GBP", "£"),
    "england": ("GBP", "£"),
    "us": ("USD", "$"),
    "usa": ("USD", "$"),
    "united states": ("USD", "$"),
    "germany": ("EUR", "€"),
    "france": ("EUR", "€"),
    "ireland": ("EUR", "€"),
    "spain": ("EUR", "€"),
    "italy": ("EUR", "€"),
    "netherlands": ("EUR", "€"),
    "india": ("INR", "₹"),
    "canada": ("CAD", "CAD $"),
    "australia": ("AUD", "AUD $"),
    "japan": ("JPY", "¥")
}


def load_deal_config() -> Dict[str, Any]:
    """Loads deal configuration from configs/deal.json."""
    if os.path.exists(CONFIG_DEAL_PATH):
        try:
            with open(CONFIG_DEAL_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed loading deal config from {CONFIG_DEAL_PATH}: {e}")
    return {
        "currency": "GBP",
        "deposit_percent": 50,
        "delivery_days": 14,
        "revision_rounds": 2,
        "governing_law": None,
        "payment_provider": "manual_bank",
        "provider_name": "Pranav Studio",
        "provider_address": None,
        "project_description": "Custom Website Design, Development, and Deployment"
    }


def get_currency_and_symbol(currency_code: Optional[str] = None, address_or_country: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Determines currency code and currency symbol.
    Currency symbol follows the client's country; no symbol when currency unknown.
    """
    if currency_code:
        code = str(currency_code).upper().strip()
        if code in CURRENCY_SYMBOLS:
            return code, CURRENCY_SYMBOLS[code]
        # Unknown code gets no symbol
        return code, None

    if address_or_country:
        addr_lower = str(address_or_country).lower()
        for k, (c_code, c_sym) in COUNTRY_CURRENCIES.items():
            if re.search(r'\b' + re.escape(k) + r'\b', addr_lower):
                return c_code, c_sym

    return None, None


def split_payment_decimals(total_price: Any, deposit_percent: Any = 50) -> Tuple[Decimal, Decimal]:
    """
    Computes deposit and balance amounts using Decimal arithmetic (no floats).
    Rounding rules: deposit is rounded half-up to 2 decimals, and balance = total - deposit,
    guaranteeing that deposit + balance == total at all times.
    """
    total = Decimal(str(total_price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    dep_pct = Decimal(str(deposit_percent))
    deposit = (total * dep_pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    balance = total - deposit
    return deposit, balance


def render_markdown_to_docx(md_content: str, output_path: str):
    """Simple parser to convert markdown text into a formatted docx document."""
    if not HAS_DOCX:
        logger.warning(f"python-docx not installed; skipping DOCX rendering for {output_path}")
        return

    doc = docx.Document()
    lines = md_content.splitlines()

    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("> **DRAFT") or s.startswith("> **REVIEWED"):
            p = doc.add_paragraph()
            run = p.add_run(s.replace("> ", "").strip())
            run.bold = True
            run.font.size = Pt(11)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif s.startswith("# "):
            doc.add_heading(s[2:].strip(), level=1)
        elif s.startswith("## "):
            doc.add_heading(s[3:].strip(), level=2)
        elif s.startswith("### "):
            doc.add_heading(s[4:].strip(), level=3)
        elif s.startswith("- ") or s.startswith("* "):
            doc.add_paragraph(s[2:].strip(), style='List Bullet')
        elif s.startswith("1. ") or s.startswith("2. ") or s.startswith("3. ") or s.startswith("4. ") or s.startswith("5. "):
            doc.add_paragraph(s[3:].strip(), style='List Number')
        else:
            doc.add_paragraph(s)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.save(output_path)


class DealCloser:
    """Orchestrates contract and document drafts for agreed leads."""

    def __init__(self, crm: Optional[CRMDatabase] = None, payment_provider: Optional[PaymentProvider] = None):
        self.crm = crm or CRMDatabase.get_instance()
        self.payment_provider = payment_provider
        self.jinja_env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True
        )

    def _get_provider(self, provider_name: Optional[str] = None) -> PaymentProvider:
        if self.payment_provider:
            return self.payment_provider
        return get_payment_provider(provider_name)

    def prepare_closing_pack(
        self,
        lead_slug: str,
        overrides: Optional[Dict[str, Any]] = None,
        lawyer_reviewed: bool = False
    ) -> Dict[str, Any]:
        """
        Prepares complete closing pack for a lead:
        1. One-page agreement (.md and .docx)
        2. Services agreement (.md and .docx)
        3. Welcome document (.md and .docx)
        4. Onboarding questionnaire (.md and .docx)
        5. Deposit invoice (.md and .docx)
        6. Balance invoice (.md and .docx)
        7. HANDOVER_CHECKLIST.md
        """
        lead = self.crm.get_lead(lead_slug)
        if not lead:
            raise ValueError(f"Lead '{lead_slug}' not found in CRM.")

        cfg = load_deal_config()
        saved_overrides = lead.get("deal_overrides") or {}
        if isinstance(saved_overrides, dict):
            cfg.update(saved_overrides)
        if overrides:
            cfg.update(overrides)
            self.crm.add_or_update_lead({
                "slug": lead_slug,
                "deal_overrides": cfg
            })

        lead_id = lead["id"]
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        year_str = datetime.date.today().strftime("%Y")

        # 1. Determine Currency and Symbol
        currency = cfg.get("currency")
        client_address = lead.get("address") or cfg.get("client_address")
        detected_cur, detected_sym = get_currency_and_symbol(currency, client_address)
        curr_code = detected_cur or currency
        curr_sym = detected_sym if detected_sym is not None else ""

        # 2. Financial Arithmetic
        total_price_val = cfg.get("total_price") or lead.get("deal_value")
        deposit_pct_val = cfg.get("deposit_percent", 50)

        missing_fields: List[str] = []

        if total_price_val is None:
            missing_fields.append("TOTAL_PRICE")
            total_price_str = "[[MISSING: TOTAL_PRICE]]"
            deposit_amount_str = "[[MISSING: DEPOSIT_AMOUNT]]"
            balance_amount_str = "[[MISSING: BALANCE_AMOUNT]]"
            deposit_dec = Decimal("0.00")
            balance_dec = Decimal("0.00")
        else:
            deposit_dec, balance_dec = split_payment_decimals(total_price_val, deposit_pct_val)
            total_price_str = f"{Decimal(str(total_price_val)):.2f}"
            deposit_amount_str = f"{deposit_dec:.2f}"
            balance_amount_str = f"{balance_dec:.2f}"

        # 3. Build token context
        client_legal = (
            cfg.get("client_legal_name")
            or lead.get("legal_name")
            or (lead.get("name") if any(str(lead.get("name", "")).lower().endswith(x) for x in ["ltd", "limited", "llp", "plc", "inc", "corp"]) else None)
        )
        if not client_legal:
            missing_fields.append("CLIENT_LEGAL_NAME")
            client_legal = "[[MISSING: CLIENT_LEGAL_NAME]]"

        if not client_address:
            missing_fields.append("CLIENT_ADDRESS")
            client_address = "[[MISSING: CLIENT_ADDRESS]]"

        gov_law = cfg.get("governing_law")
        if not gov_law:
            missing_fields.append("GOVERNING_LAW")
            gov_law = "[[MISSING: GOVERNING_LAW]]"

        prov_name = cfg.get("provider_name")
        if not prov_name:
            missing_fields.append("PROVIDER_NAME")
            prov_name = "[[MISSING: PROVIDER_NAME]]"

        prov_addr = cfg.get("provider_address")
        if not prov_addr:
            missing_fields.append("PROVIDER_ADDRESS")
            prov_addr = "[[MISSING: PROVIDER_ADDRESS]]"

        if not curr_code:
            missing_fields.append("CURRENCY")
            curr_code_str = "[[MISSING: CURRENCY]]"
        else:
            curr_code_str = curr_code

        # Watermark
        if lawyer_reviewed and not missing_fields:
            watermark_header = "> **REVIEWED - Approved for sending.**\n"
        else:
            watermark_header = "> **DRAFT - not reviewed. Do not send.**\n"

        provider_engine = self._get_provider(cfg.get("payment_provider"))

        # Base token dict
        tokens = {
            "WATERMARK_HEADER": watermark_header,
            "DATE": today_str,
            "START_DATE": today_str,
            "CLIENT_BUSINESS_NAME": lead.get("name") or lead_slug,
            "CLIENT_LEGAL_NAME": client_legal,
            "CLIENT_ENTITY_TYPE": lead.get("entity_type", "unknown"),
            "CLIENT_ADDRESS": client_address,
            "CLIENT_EMAIL": lead.get("email") or "[[MISSING: CLIENT_EMAIL]]",
            "OWNER_NAME": lead.get("contact_name") or "Authorized Representative",
            "PROVIDER_NAME": prov_name,
            "PROVIDER_ADDRESS": prov_addr,
            "PROJECT_DESCRIPTION": cfg.get("project_description", "Custom Website Design, Development, and Deployment"),
            "DEMO_URL": lead.get("demo_url") or f"https://{lead_slug}-concept.vercel.app",
            "TOTAL_PRICE": total_price_str,
            "CURRENCY": curr_code_str,
            "CURRENCY_SYMBOL": curr_sym,
            "DEPOSIT_PERCENT": str(deposit_pct_val),
            "DEPOSIT_AMOUNT": deposit_amount_str,
            "BALANCE_AMOUNT": balance_amount_str,
            "DELIVERY_DAYS": str(cfg.get("delivery_days", 14)),
            "REVISION_ROUNDS": str(cfg.get("revision_rounds", 2)),
            "GOVERNING_LAW": gov_law
        }

        # Setup invoices
        inv_base_num = cfg.get("invoice_base_number") or f"INV-{year_str}-{lead_id[-3:] if len(lead_id) >= 3 else '001'}"
        inv_dep_num = f"{inv_base_num}-DEP"
        inv_bal_num = f"{inv_base_num}-BAL"

        inv_dep_data = {
            "invoice_number": inv_dep_num,
            "amount": deposit_dec,
            "total_price": total_price_val,
            "currency": curr_code or "GBP",
            "lead_id": lead_id,
            "client_name": client_legal,
            "client_address": client_address
        }
        pay_link_dep = provider_engine.create_payment_link(inv_dep_data)
        if pay_link_dep:
            inv_dep_data["payment_link"] = pay_link_dep

        pay_instructions_dep = provider_engine.payment_instructions(inv_dep_data)
        tokens["PAYMENT_INSTRUCTIONS"] = pay_instructions_dep

        # Target directory
        out_dir = os.path.join(DOCUMENTS_BASE_DIR, lead_slug)
        os.makedirs(out_dir, exist_ok=True)

        generated_documents: List[Dict[str, Any]] = []

        def _substitute_tokens(template_text: str, custom_tokens: Dict[str, Any]) -> str:
            res = template_text
            for k, v in custom_tokens.items():
                res = res.replace(f"{{{{{k}}}}}", str(v))
            return res

        # Check if document has missing markers
        has_missing = len(missing_fields) > 0
        doc_status = "draft" if has_missing or not lawyer_reviewed else "lawyer_reviewed"

        # 4. Render Agreement(s)
        # One-page agreement
        one_page_tmpl_path = os.path.join(TEMPLATES_DIR, "contract", "one_page_agreement.md")
        if os.path.exists(one_page_tmpl_path):
            with open(one_page_tmpl_path, "r", encoding="utf-8") as f:
                one_page_raw = f.read()
            one_page_rendered = _substitute_tokens(one_page_raw, tokens)
            md_path = os.path.join(out_dir, "one_page_agreement.md")
            docx_path = os.path.join(out_dir, "one_page_agreement.docx")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(one_page_rendered)
            render_markdown_to_docx(one_page_rendered, docx_path)
            doc_rec = self.crm.add_document(
                lead_id=lead_id,
                kind="contract",
                path=md_path,
                version=1,
                status=doc_status
            )
            generated_documents.append(doc_rec)

        # Services agreement
        services_tmpl_path = os.path.join(TEMPLATES_DIR, "contract", "services_agreement.md")
        if os.path.exists(services_tmpl_path):
            with open(services_tmpl_path, "r", encoding="utf-8") as f:
                services_raw = f.read()
            services_rendered = _substitute_tokens(services_raw, tokens)
            md_path = os.path.join(out_dir, "services_agreement.md")
            docx_path = os.path.join(out_dir, "services_agreement.docx")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(services_rendered)
            render_markdown_to_docx(services_rendered, docx_path)
            doc_rec = self.crm.add_document(
                lead_id=lead_id,
                kind="contract",
                path=md_path,
                version=1,
                status=doc_status
            )
            generated_documents.append(doc_rec)

        # 5. Render Welcome Doc & Questionnaire
        welcome_tmpl_path = os.path.join(TEMPLATES_DIR, "welcome", "welcome_doc.md")
        if os.path.exists(welcome_tmpl_path):
            with open(welcome_tmpl_path, "r", encoding="utf-8") as f:
                welcome_raw = f.read()
            welcome_rendered = _substitute_tokens(welcome_raw, tokens)
            md_path = os.path.join(out_dir, "welcome_doc.md")
            docx_path = os.path.join(out_dir, "welcome_doc.docx")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(welcome_rendered)
            render_markdown_to_docx(welcome_rendered, docx_path)
            doc_rec = self.crm.add_document(
                lead_id=lead_id,
                kind="welcome",
                path=md_path,
                version=1,
                status=doc_status
            )
            generated_documents.append(doc_rec)

        quest_tmpl_path = os.path.join(TEMPLATES_DIR, "welcome", "onboarding_questionnaire.md")
        if os.path.exists(quest_tmpl_path):
            with open(quest_tmpl_path, "r", encoding="utf-8") as f:
                quest_raw = f.read()
            quest_rendered = _substitute_tokens(quest_raw, tokens)
            md_path = os.path.join(out_dir, "onboarding_questionnaire.md")
            docx_path = os.path.join(out_dir, "onboarding_questionnaire.docx")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(quest_rendered)
            render_markdown_to_docx(quest_rendered, docx_path)
            doc_rec = self.crm.add_document(
                lead_id=lead_id,
                kind="welcome",
                path=md_path,
                version=1,
                status=doc_status
            )
            generated_documents.append(doc_rec)

        # 6. Render Deposit and Balance Invoices
        inv_tmpl_path = os.path.join(TEMPLATES_DIR, "invoice", "invoice.md")
        if os.path.exists(inv_tmpl_path):
            with open(inv_tmpl_path, "r", encoding="utf-8") as f:
                inv_raw = f.read()

            # Deposit Invoice
            tokens_dep = dict(tokens)
            tokens_dep["INVOICE_NUMBER"] = inv_dep_num
            tokens_dep["INVOICE_AMOUNT"] = deposit_amount_str
            tokens_dep["INVOICE_DESCRIPTION"] = f"Initial Project Deposit ({deposit_pct_val}%)"
            tokens_dep["PAYMENT_INSTRUCTIONS"] = pay_instructions_dep
            inv_dep_rendered = _substitute_tokens(inv_raw, tokens_dep)
            md_dep_path = os.path.join(out_dir, f"{inv_dep_num}.md")
            docx_dep_path = os.path.join(out_dir, f"{inv_dep_num}.docx")
            with open(md_dep_path, "w", encoding="utf-8") as f:
                f.write(inv_dep_rendered)
            render_markdown_to_docx(inv_dep_rendered, docx_dep_path)
            doc_rec = self.crm.add_document(
                lead_id=lead_id,
                kind="invoice",
                path=md_dep_path,
                version=1,
                status=doc_status
            )
            generated_documents.append(doc_rec)

            # Balance Invoice
            inv_bal_data = {
                "invoice_number": inv_bal_num,
                "amount": balance_dec,
                "total_price": total_price_val,
                "currency": curr_code or "GBP",
                "lead_id": lead_id,
                "client_name": client_legal,
                "client_address": client_address
            }
            pay_link_bal = provider_engine.create_payment_link(inv_bal_data)
            if pay_link_bal:
                inv_bal_data["payment_link"] = pay_link_bal
            pay_instructions_bal = provider_engine.payment_instructions(inv_bal_data)

            tokens_bal = dict(tokens)
            tokens_bal["INVOICE_NUMBER"] = inv_bal_num
            tokens_bal["INVOICE_AMOUNT"] = balance_amount_str
            tokens_bal["INVOICE_DESCRIPTION"] = f"Final Project Completion Balance ({100 - deposit_pct_val}%)"
            tokens_bal["PAYMENT_INSTRUCTIONS"] = pay_instructions_bal
            inv_bal_rendered = _substitute_tokens(inv_raw, tokens_bal)
            md_bal_path = os.path.join(out_dir, f"{inv_bal_num}.md")
            docx_bal_path = os.path.join(out_dir, f"{inv_bal_num}.docx")
            with open(md_bal_path, "w", encoding="utf-8") as f:
                f.write(inv_bal_rendered)
            render_markdown_to_docx(inv_bal_rendered, docx_bal_path)
            doc_rec = self.crm.add_document(
                lead_id=lead_id,
                kind="invoice",
                path=md_bal_path,
                version=1,
                status=doc_status
            )
            generated_documents.append(doc_rec)

        # 7. Render Checklist HANDOVER_CHECKLIST.md with all Flags
        checklist_content = self.generate_checklist_content(
            lead=lead,
            missing_fields=missing_fields,
            tokens=tokens,
            lawyer_reviewed=lawyer_reviewed
        )
        checklist_path = os.path.join(out_dir, "HANDOVER_CHECKLIST.md")
        with open(checklist_path, "w", encoding="utf-8") as f:
            f.write(checklist_content)
        doc_rec = self.crm.add_document(
            lead_id=lead_id,
            kind="checklist",
            path=checklist_path,
            version=1,
            status="reviewed" if (not missing_fields and lawyer_reviewed) else "draft"
        )
        generated_documents.append(doc_rec)

        # 8. Update Lead Status and CRM Event
        new_lead_status = "contract_drafted"
        self.crm.update_lead_status(lead_slug, new_lead_status)
        self.crm.record_event(
            event_type="contract_drafted",
            lead_id=lead_id,
            payload={
                "slug": lead_slug,
                "missing_fields": missing_fields,
                "documents_count": len(generated_documents),
                "lawyer_reviewed": lawyer_reviewed
            }
        )

        return {
            "status": "blocked" if has_missing else ("lawyer_reviewed" if lawyer_reviewed else "draft"),
            "lead_status": new_lead_status,
            "slug": lead_slug,
            "missing_fields": missing_fields,
            "documents": generated_documents,
            "checklist_path": checklist_path,
            "deposit_amount": str(deposit_dec),
            "balance_amount": str(balance_dec),
            "total_price": str(total_price_val)
        }

    def generate_checklist_content(
        self,
        lead: Dict[str, Any],
        missing_fields: List[str],
        tokens: Dict[str, Any],
        lawyer_reviewed: bool
    ) -> str:
        """Constructs HANDOVER_CHECKLIST.md with missing tokens and all 5 advisory flags."""
        lines = [
            "# HANDOVER CHECKLIST & LEGAL FLAGS",
            f"**Lead:** {lead.get('name')} (`{lead.get('slug')}`)",
            f"**Generated At:** {datetime.date.today().strftime('%Y-%m-%d')}",
            f"**Review Status:** {'Lawyer Reviewed' if lawyer_reviewed else 'DRAFT (Pending Review)'}",
            "",
            "---",
            "### 1. Missing Fields & Blockers"
        ]

        if missing_fields:
            lines.append("The following required legal/money fields are missing and must be completed before sending:")
            for mf in missing_fields:
                lines.append(f"- [ ] **[[MISSING: {mf}]]**")
        else:
            lines.append("- [x] All mandatory legal and financial fields are populated.")

        lines.extend([
            "",
            "---",
            "### 2. Legal Advisory Flags (For Operator Verification)",
            "",
            "#### Flag 1: Non-Refundable Deposit Policy",
            "- **Note:** Enforceability of non-refundable deposit terms varies by jurisdiction. In the UK, consumer protection regulations differ from B2B contracts. Confirm wording with your legal counsel before issuing to sole traders/consumers.",
            "",
            "#### Flag 2: Client Entity Type & Signatory Capacity",
            f"- **Entity Type:** `{lead.get('entity_type', 'unknown')}`",
            "- **Note:** For a Limited Company (Ltd), ensure the signatory is an authorized director or officer. For a Sole Trader or Partnership, personal liability applies; ensure the individual's full legal name is confirmed.",
            "",
            "#### Flag 3: Foreign Clients, Currency & Tax Treatment",
            f"- **Currency:** `{tokens.get('CURRENCY')}` | **Governing Law:** `{tokens.get('GOVERNING_LAW')}`",
            "- **Export of Services:** If billing foreign clients from India, ensure LUT (Letter of Undertaking) is active to treat as zero-rated export under GST.",
            "- **Reverse Charge / VAT:** Client may be subject to domestic reverse charge VAT/GST in their home country.",
            "- **E-Signatures:** Confirm e-signature validity and stamp duty requirements under governing law.",
            "",
            "#### Flag 4: Intellectual Property & Concept Staging Retention",
            "- **Clause Check:** Ensure contract states the interactive concept demo remains Provider's property until 100% of the total balance is paid.",
            "- **Status:** Concept retention clause is explicitly embedded in generated agreements.",
            "",
            "#### Flag 5: Data Protection & Privacy Notice",
            "- **Client Notice:** If the delivered website incorporates contact forms, booking tools, or customer analytics, remind the client that a compliant Privacy Policy and GDPR/DPDP notice must be published.",
            "",
            "---",
            "### 3. Action Items Before Sending",
            "- [ ] Operator reviewed filled contract and invoices.",
            "- [ ] Verified bank / payment instructions with active account.",
            "- [ ] Marked `lawyer_reviewed` in Command Deck.",
            "- [ ] Ready for manual dispatch by operator."
        ])

        return "\n".join(lines)

    def mark_lawyer_reviewed(self, lead_slug: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Operator marks documents as lawyer-reviewed in the UI."""
        lead = self.crm.get_lead(lead_slug)
        if not lead:
            raise ValueError(f"Lead '{lead_slug}' not found.")

        # Re-run prepare_closing_pack with lawyer_reviewed=True
        res = self.prepare_closing_pack(lead_slug=lead_slug, overrides=overrides, lawyer_reviewed=True)
        if res.get("missing_fields"):
            raise ValueError(
                f"Cannot mark lawyer-reviewed while missing fields remain: {res['missing_fields']}"
            )

        # Update CRM documents status
        docs = self.crm.get_documents_for_lead(lead["id"])
        for d in docs:
            self.crm.update_document_status(d["id"], "lawyer_reviewed")

        self.crm.record_event(
            event_type="lawyer_reviewed",
            lead_id=lead["id"],
            payload={"slug": lead_slug}
        )
        return {"status": "lawyer_reviewed", "slug": lead_slug, "documents_updated": len(docs)}

    def mark_contract_sent(self, lead_slug: str) -> Dict[str, Any]:
        """Operator clicks 'Mark contract sent' after emailing/messaging the documents."""
        lead = self.crm.get_lead(lead_slug)
        if not lead:
            raise ValueError(f"Lead '{lead_slug}' not found.")

        self.crm.update_lead_status(lead_slug, "contract_sent")
        self.crm.record_event(
            event_type="contract_sent",
            lead_id=lead["id"],
            payload={"slug": lead_slug, "sent_at": time.time()}
        )
        return {"status": "contract_sent", "slug": lead_slug}

    def mark_deposit_received(self, lead_slug: str) -> Dict[str, Any]:
        """Operator confirms receipt of deposit (or accepts payment webhook suggestion)."""
        lead = self.crm.get_lead(lead_slug)
        if not lead:
            raise ValueError(f"Lead '{lead_slug}' not found.")

        self.crm.update_lead_status(lead_slug, "deposit_received")
        self.crm.record_event(
            event_type="deposit_received",
            lead_id=lead["id"],
            payload={"slug": lead_slug, "received_at": time.time()}
        )

        # Automatically advance to won
        self.crm.update_lead_status(lead_slug, "won")
        self.crm.record_event(
            event_type="won",
            lead_id=lead["id"],
            payload={"slug": lead_slug, "won_at": time.time()}
        )

        return {"status": "won", "lead_status": "won", "slug": lead_slug}
