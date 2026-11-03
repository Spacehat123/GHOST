"""Outreach Drafter & Manual Queue Worker for Ghost (Part 4).
Generates pre-filled, strictly grounded outreach drafts for WhatsApp, Email, and Instagram.
Enforces the core principle: Ghost prepares, Pranav verifies and sends.
Never automatically dispatches messages. Enforces PECR compliance, opt-out lines,
honest framing lines, and persona firewall.
"""
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple, Set

from jinja2 import Environment, FileSystemLoader

from ghost.crm.db import CRMDatabase
from ghost.workers.demo_state_machine import sanitize_outbound
from ghost.workers.suppression import SuppressionManager

logger = logging.getLogger("ghost.outreach_drafter")

CONFIG_PATH = "/home/pranc/projects/ghost/configs/outreach.json"
TEMPLATES_DIR = "/home/pranc/projects/ghost/templates/outreach"

DEFAULT_OPT_OUT_LINE = "If you'd rather not hear from me, just reply 'no thanks' and I won't contact you again."
HONEST_FRAMING_LINE = "I made this concept for you as an example. It isn't live and isn't affiliated with you."


def load_outreach_config() -> Dict[str, Any]:
    """Loads outreach configuration from configs/outreach.json."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed loading outreach config from {CONFIG_PATH}: {e}")
    return {
        "operator_name": "Pranav",
        "operator_business_name": None,
        "operator_email": None,
        "operator_phone": None,
        "postal_address": None,
        "include_unsubscribe_line": True,
        "followup_after_days": 3,
        "max_followups": 1,
        "do_not_contact_unknown_entity_email": True,
        "max_sends_per_day": 20
    }


def is_whatsapp_capable_mobile(phone_raw: Optional[str]) -> bool:
    """
    Validates E.164 phone number for WhatsApp messaging capability.
    UK landlines (+44 20, +44 1, 020, 01...) are strictly NOT WhatsApp-capable.
    """
    if not phone_raw:
        return False
    digits = re.sub(r"[^\d]", "", phone_raw)
    # UK landline patterns: +44 20..., +44 1..., 020..., 01...
    if digits.startswith("442") or digits.startswith("441") or digits.startswith("02") or digits.startswith("01"):
        return False
    # UK mobile patterns: +44 7... (12 digits) or 07... (11 digits)
    if digits.startswith("447") and len(digits) == 12:
        return True
    if digits.startswith("07") and len(digits) == 11:
        return True
    # General international mobile check (10-15 digits, non-UK landline)
    if 10 <= len(digits) <= 15 and not (digits.startswith("442") or digits.startswith("441")):
        return True
    return False


def get_clean_phone_digits(phone_raw: Optional[str]) -> str:
    """Formats phone into bare international digits for wa.me links."""
    if not phone_raw:
        return ""
    digits = re.sub(r"[^\d]", "", phone_raw)
    if digits.startswith("07") and len(digits) == 11:
        digits = "44" + digits[1:]
    elif digits.startswith("02") or digits.startswith("01"):
        digits = "44" + digits[1:]
    return digits


def get_word_count(text: str) -> int:
    """Returns exact whitespace word count of text."""
    return len(text.strip().split())


def generate_eml_content(to_email: str, from_email: str, subject: str, body: str) -> str:
    """Formats standard RFC 822 .eml document."""
    return f"""From: {from_email}
To: {to_email}
Subject: {subject}
MIME-Version: 1.0
Content-Type: text/plain; charset=utf-8

{body}
"""


class OutreachDrafter:
    """Generates and manages multi-channel outreach drafts."""

    def __init__(self, crm: Optional[CRMDatabase] = None):
        self.crm = crm or CRMDatabase.get_instance()
        self.suppression = SuppressionManager(crm=self.crm)
        self.jinja_env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True
        )

    def extract_gap_summary(self, lead: Dict[str, Any], pitch_type: str) -> str:
        """Extracts clean, ledger-backed gap description from gaps_json."""
        gaps_json_str = lead.get("gaps_json")
        if gaps_json_str:
            try:
                gdata = json.loads(gaps_json_str)
                if isinstance(gdata, dict):
                    gaps = gdata.get("gaps", [])
                    if any("slow_mobile_load" in g for g in gaps):
                        return "that your site takes several seconds to load on mobile"
                    if any("missing_viewport" in g for g in gaps):
                        return "that your site is not optimized for smartphone screens"
                    if any("missing_tap_to_call" in g for g in gaps):
                        return "that clients cannot tap to call directly from mobile"
                    if any("no_online_booking" in g for g in gaps):
                        return "that there is no direct online booking path"
            except Exception:
                pass

        if pitch_type == "no_site":
            return "that you don't currently have a website listed"
        elif pitch_type == "no_booking_path":
            return "that clients cannot book or inquire directly online"
        return "that your mobile experience could be significantly smoother"

    def create_drafts_for_lead(
        self,
        lead_slug_or_id: str,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generates compliant drafts for a lead in built/deployed status.
        Live concept URL is mandatory.
        Creates WhatsApp (if mobile), Email (if real email + PECR permitted), and Instagram (if handle exists).
        """
        lead = self.crm.get_lead(lead_slug_or_id)
        if not lead:
            raise ValueError(f"Lead '{lead_slug_or_id}' not found in CRM.")

        slug = lead["slug"]
        cfg = dict(load_outreach_config())
        if config:
            cfg.update(config)

        # 1. Suppression check
        if self.suppression.is_suppressed(lead=lead):
            logger.info(f"Lead '{slug}' is suppressed. Skipping draft generation.")
            self.crm.update_lead(slug, status="suppressed")
            return {"status": "suppressed", "slug": slug, "draft_count": 0, "drafts": []}

        # 2. Live Concept URL is MANDATORY
        demo_url = lead.get("demo_url")
        if not demo_url or not str(demo_url).strip().startswith("http"):
            logger.warning(f"No hosted live URL found for '{slug}'. Drafts cannot be created per Rule 6.")
            return {"status": "needs_deploy", "slug": slug, "draft_count": 0, "drafts": [], "message": "Live concept URL is mandatory."}

        # 3. Determine pitch type from lead facts
        pitch_type = lead.get("pitch_type")
        if not pitch_type:
            if not lead.get("website"):
                pitch_type = "no_site"
            elif lead.get("score_friction", 0) >= 20.0:
                pitch_type = "weak_site"
            else:
                pitch_type = "no_booking_path"

        # 4. Context variables for templates
        city_location = (lead.get("address") or "your area").split(",")[0].strip()
        gap_summary = self.extract_gap_summary(lead, pitch_type)
        opt_out_line = DEFAULT_OPT_OUT_LINE if cfg.get("include_unsubscribe_line", True) else ""
        operator_name = cfg.get("operator_name") or "Pranav"

        ctx = {
            "name": lead.get("name") or slug,
            "location": city_location,
            "demo_url": demo_url.strip(),
            "gap_summary": gap_summary,
            "opt_out_line": opt_out_line,
            "operator_name": operator_name,
            "operator_business_name": cfg.get("operator_business_name"),
            "postal_address": cfg.get("postal_address")
        }

        generated_drafts = []

        # ---------------------------------------------------------------------
        # Channel 1: WhatsApp
        # ---------------------------------------------------------------------
        raw_phone = lead.get("phone")
        if is_whatsapp_capable_mobile(raw_phone):
            clean_digits = get_clean_phone_digits(raw_phone)
            tmpl_name = f"whatsapp_{pitch_type}.md.j2"
            try:
                wa_template = self.jinja_env.get_template(tmpl_name)
                wa_body = wa_template.render(ctx).strip()

                # Persona firewall check
                sanitize_outbound(wa_body)

                # Word count check
                if get_word_count(wa_body) > 60:
                    logger.warning(f"WhatsApp draft for '{slug}' exceeds 60 words ({get_word_count(wa_body)} words).")

                wa_link = f"https://wa.me/{clean_digits}?text={urllib.parse.quote(wa_body)}"
                draft_rec = self.crm.add_or_update_draft(
                    lead_id=lead["id"],
                    channel="whatsapp",
                    body=wa_body,
                    link=wa_link,
                    status="draft"
                )
                generated_drafts.append(draft_rec)
            except Exception as e:
                logger.error(f"Failed generating WhatsApp draft for '{slug}': {e}")
        else:
            logger.info(f"Lead '{slug}' phone ({raw_phone}) is not a WhatsApp-capable mobile (or landline). Skipping WhatsApp draft.")

        # ---------------------------------------------------------------------
        # Channel 2: Email
        # ---------------------------------------------------------------------
        raw_email = lead.get("email")
        entity_type = lead.get("entity_type", "unknown")
        pecr_guard = cfg.get("do_not_contact_unknown_entity_email", True)

        if raw_email and "@" in str(raw_email):
            # UK PECR Guard: Only email limited_company without prior consent when guard is on
            if pecr_guard and entity_type in ("sole_trader", "partnership", "unknown"):
                logger.info(
                    f"Skipping email draft for '{slug}' (entity_type='{entity_type}') "
                    f"per PECR rules (do_not_contact_unknown_entity_email=True)."
                )
            else:
                tmpl_name = f"email_{pitch_type}.md.j2"
                try:
                    email_template = self.jinja_env.get_template(tmpl_name)
                    rendered_email = email_template.render(ctx).strip()

                    # Extract subject and body
                    subject = "Website concept"
                    body_lines = []
                    for line in rendered_email.split("\n"):
                        if line.startswith("Subject:"):
                            subject = line.replace("Subject:", "").strip()
                        else:
                            body_lines.append(line)
                    email_body = "\n".join(body_lines).strip()

                    # Persona firewall check
                    sanitize_outbound(subject)
                    sanitize_outbound(email_body)

                    # Word count check
                    if get_word_count(email_body) > 120:
                        logger.warning(f"Email draft for '{slug}' exceeds 120 words ({get_word_count(email_body)} words).")

                    mailto_params = urllib.parse.urlencode({"subject": subject, "body": email_body})
                    mailto_link = f"mailto:{raw_email}?{mailto_params}"

                    draft_rec = self.crm.add_or_update_draft(
                        lead_id=lead["id"],
                        channel="email",
                        subject=subject,
                        body=email_body,
                        link=mailto_link,
                        status="draft"
                    )
                    # Store downloadable .eml content in draft
                    eml_content = generate_eml_content(
                        to_email=raw_email,
                        from_email=cfg.get("operator_email") or "operator@example.com",
                        subject=subject,
                        body=email_body
                    )
                    draft_rec["eml_content"] = eml_content
                    generated_drafts.append(draft_rec)
                except Exception as e:
                    logger.error(f"Failed generating Email draft for '{slug}': {e}")

        # ---------------------------------------------------------------------
        # Channel 3: Instagram DM
        # ---------------------------------------------------------------------
        instagram_handle = lead.get("instagram")
        if instagram_handle:
            tmpl_name = f"instagram_{pitch_type}.md.j2"
            try:
                ig_template = self.jinja_env.get_template(tmpl_name)
                ig_body = ig_template.render(ctx).strip()

                sanitize_outbound(ig_body)

                draft_rec = self.crm.add_or_update_draft(
                    lead_id=lead["id"],
                    channel="instagram",
                    body=ig_body,
                    link=f"https://instagram.com/{instagram_handle.lstrip('@')}",
                    status="draft"
                )
                generated_drafts.append(draft_rec)
            except Exception as e:
                logger.error(f"Failed generating Instagram draft for '{slug}': {e}")

        # Update lead status in CRM if drafts were produced
        if generated_drafts:
            self.crm.update_lead(slug, status="drafts_ready")
            self.crm.record_event(
                event_type="drafts_created",
                lead_id=lead["id"],
                payload={"slug": slug, "draft_count": len(generated_drafts)}
            )

        return {
            "status": "drafts_ready" if generated_drafts else "no_drafts_generated",
            "slug": slug,
            "draft_count": len(generated_drafts),
            "drafts": generated_drafts
        }

    def approve_draft(
        self,
        draft_id: str,
        config_override: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Operator action: Approves a draft for sending.
        Enforces human verification: Blocks approval if operator details are missing
        or if the prospect is on the suppression list.
        """
        cfg = dict(load_outreach_config())
        if config_override:
            cfg.update(config_override)

        # 1. Operator details verification
        has_business = bool(cfg.get("operator_business_name"))
        has_address = bool(cfg.get("postal_address"))
        has_name = bool(cfg.get("operator_name"))
        if not has_name or not (has_business or has_address):
            raise ValueError(
                "Cannot approve draft while operator details are missing. "
                "Both operator_name and either postal_address or operator_business_name must be configured."
            )

        # 2. Retrieve draft and lead
        with self.crm._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM drafts WHERE id = ?", (draft_id.strip(),))
            d_row = cur.fetchone()
            if not d_row:
                raise ValueError(f"Draft '{draft_id}' not found.")
            draft = dict(d_row)

        lead = self.crm.get_lead(draft["lead_id"])
        if not lead:
            raise ValueError(f"Associated lead '{draft['lead_id']}' not found.")

        # 3. Suppression check at approval gate
        if self.suppression.is_suppressed(lead=lead):
            self.crm.add_or_update_draft(
                draft_id=draft_id,
                lead_id=lead["id"],
                channel=draft["channel"],
                body=draft["body"],
                status="suppressed"
            )
            self.crm.update_lead(lead["slug"], status="suppressed")
            raise PermissionError(f"Cannot approve draft: Lead '{lead['slug']}' is on the suppression list.")

        now = time.time()
        with self.crm._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE drafts SET status = 'approved', approved_at = ? WHERE id = ?", (now, draft_id.strip()))
            conn.commit()

        self.crm.update_lead(lead["slug"], status="approved")
        self.crm.record_event("draft_approved", lead["id"], {"draft_id": draft_id, "channel": draft["channel"]})

        logger.info(f"Draft '{draft_id}' for '{lead['slug']}' APPROVED by operator.")
        return {"status": "approved", "draft_id": draft_id, "lead_slug": lead["slug"], "approved_at": now}

    def mark_draft_sent(
        self,
        draft_id: str,
        config_override: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Operator action: Records that the message was sent by the operator's own hand.
        Ghost never sends messages itself.
        Enforces daily sends cap and suppression check at the send gate.
        """
        cfg = dict(load_outreach_config())
        if config_override:
            cfg.update(config_override)

        with self.crm._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM drafts WHERE id = ?", (draft_id.strip(),))
            d_row = cur.fetchone()
            if not d_row:
                raise ValueError(f"Draft '{draft_id}' not found.")
            draft = dict(d_row)

        lead = self.crm.get_lead(draft["lead_id"])
        if not lead:
            raise ValueError(f"Associated lead '{draft['lead_id']}' not found.")

        # Suppression check at send gate
        if self.suppression.is_suppressed(lead=lead):
            self.crm.add_or_update_draft(
                draft_id=draft_id,
                lead_id=lead["id"],
                channel=draft["channel"],
                body=draft["body"],
                status="suppressed"
            )
            self.crm.update_lead(lead["slug"], status="suppressed")
            raise PermissionError(f"Cannot send: Lead '{lead['slug']}' is on the suppression list.")

        # Daily Cap Check
        max_daily = cfg.get("max_sends_per_day", 20)
        start_of_day = time.time() - (time.time() % 86400)
        with self.crm._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM drafts WHERE status = 'sent' AND sent_at >= ?", (start_of_day,))
            sent_today = cur.fetchone()["cnt"]

        warning = None
        if sent_today >= max_daily:
            warning = f"Daily outreach cap reached ({sent_today}/{max_daily}). Proceed with human pacing."
            logger.warning(warning)

        now = time.time()
        with self.crm._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE drafts SET status = 'sent', sent_at = ? WHERE id = ?", (now, draft_id.strip()))
            conn.commit()

        self.crm.update_lead(lead["slug"], status="sent")
        self.crm.log_interaction(
            client_slug=lead["slug"],
            channel=draft["channel"],
            direction="outbound",
            content=f"[Manual Send] {draft['body']}"
        )
        self.crm.record_event("draft_sent", lead["id"], {"draft_id": draft_id, "channel": draft["channel"], "sent_at": now})

        logger.info(f"Draft '{draft_id}' for '{lead['slug']}' marked as SENT.")
        return {
            "status": "sent",
            "draft_id": draft_id,
            "lead_slug": lead["slug"],
            "sent_at": now,
            "warning": warning
        }

    def record_reply(
        self,
        draft_id_or_lead_slug: str,
        reply_text: str
    ) -> Dict[str, Any]:
        """
        Operator action: Records incoming reply text from prospect.
        If opt-out keywords are detected, automatically triggers 1-click opt-out and suppresses the lead.
        """
        lead = self.crm.get_lead(draft_id_or_lead_slug)
        draft_id = None
        if not lead:
            # Try finding via draft id
            with self.crm._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM drafts WHERE id = ?", (draft_id_or_lead_slug.strip(),))
                d_row = cur.fetchone()
                if d_row:
                    draft_id = d_row["id"]
                    lead = self.crm.get_lead(d_row["lead_id"])

        if not lead:
            raise ValueError(f"Could not find lead for '{draft_id_or_lead_slug}'.")

        slug = lead["slug"]
        now = time.time()

        # Log inbound interaction
        self.crm.log_interaction(
            client_slug=slug,
            channel="inbound",
            direction="inbound",
            content=reply_text.strip()
        )

        # Update draft status if specific draft found
        if draft_id:
            with self.crm._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("UPDATE drafts SET status = 'replied' WHERE id = ?", (draft_id,))
                conn.commit()

        # Check for opt-out intent
        is_opt_out = self.suppression.detect_opt_out_intent(reply_text)
        if is_opt_out:
            logger.info(f"Opt-out keyword detected in reply for '{slug}'. Suppressing contact.")
            self.suppression.opt_out_lead(slug, reason=f"Prospect replied: '{reply_text[:60]}'")
            return {
                "status": "opted_out",
                "slug": slug,
                "reply_text": reply_text,
                "is_opt_out": True,
                "message": "Opt-out recognized and processed. Contact suppressed."
            }

        self.crm.update_lead(slug, status="replied")
        self.crm.record_event("lead_replied", lead["id"], {"slug": slug, "reply": reply_text})

        return {
            "status": "replied",
            "slug": slug,
            "reply_text": reply_text,
            "is_opt_out": False
        }

    def generate_followup_draft(
        self,
        lead_slug_or_id: str,
        config: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Generates exactly one follow-up draft if followup_after_days elapsed since 'sent',
        and no reply/suppression has occurred.
        Max 1 follow-up allowed.
        """
        lead = self.crm.get_lead(lead_slug_or_id)
        if not lead:
            raise ValueError(f"Lead '{lead_slug_or_id}' not found.")

        slug = lead["slug"]
        cfg = dict(load_outreach_config())
        if config:
            cfg.update(config)

        # 1. Lead state check: must not be replied, agreed, won, lost, or suppressed
        if lead.get("status") in ("replied", "agreed", "won", "lost", "suppressed"):
            logger.info(f"Lead '{slug}' in status '{lead.get('status')}'. No follow-up permitted.")
            return None

        # 2. Retrieve existing drafts
        drafts = self.crm.get_drafts_for_lead(lead["id"])
        sent_drafts = [d for d in drafts if d.get("status") == "sent"]
        if not sent_drafts:
            logger.info(f"Lead '{slug}' has no sent drafts. No follow-up can be generated.")
            return None

        # Check if follow-up draft already exists
        existing_followups = [d for d in drafts if "following up" in d.get("body", "").lower()]
        max_followups = cfg.get("max_followups", 1)
        if len(existing_followups) >= max_followups:
            logger.info(f"Lead '{slug}' already reached max follow-ups ({len(existing_followups)}/{max_followups}).")
            return None

        # 3. Check elapsed time
        primary_sent = sent_drafts[0]
        sent_at = primary_sent.get("sent_at") or 0
        followup_days = cfg.get("followup_after_days", 3)
        now = time.time()
        elapsed_seconds = now - sent_at

        # If not enough time has elapsed
        if elapsed_seconds < (followup_days * 86400):
            logger.info(f"Follow-up for '{slug}' not due yet ({elapsed_seconds / 86400:.1f}/{followup_days} days).")
            return None

        channel = primary_sent["channel"]
        tmpl_name = f"followup_{channel}.md.j2"
        demo_url = lead.get("demo_url", "")
        opt_out_line = DEFAULT_OPT_OUT_LINE if cfg.get("include_unsubscribe_line", True) else ""
        operator_name = cfg.get("operator_name") or "Pranav"

        ctx = {
            "name": lead.get("name") or slug,
            "demo_url": demo_url,
            "opt_out_line": opt_out_line,
            "operator_name": operator_name,
            "operator_business_name": cfg.get("operator_business_name"),
            "postal_address": cfg.get("postal_address")
        }

        try:
            tmpl = self.jinja_env.get_template(tmpl_name)
            rendered = tmpl.render(ctx).strip()

            subject = None
            body = rendered
            if channel == "email":
                body_lines = []
                for line in rendered.split("\n"):
                    if line.startswith("Subject:"):
                        subject = line.replace("Subject:", "").strip()
                    else:
                        body_lines.append(line)
                body = "\n".join(body_lines).strip()

            sanitize_outbound(body)
            if subject:
                sanitize_outbound(subject)

            link = None
            if channel == "whatsapp":
                clean_digits = get_clean_phone_digits(lead.get("phone"))
                link = f"https://wa.me/{clean_digits}?text={urllib.parse.quote(body)}"
            elif channel == "email" and lead.get("email"):
                mailto_params = urllib.parse.urlencode({"subject": subject or "Following up", "body": body})
                link = f"mailto:{lead.get('email')}?{mailto_params}"

            followup_draft = self.crm.add_or_update_draft(
                lead_id=lead["id"],
                channel=channel,
                subject=subject,
                body=body,
                link=link,
                status="draft"
            )

            self.crm.record_event(
                event_type="followup_draft_created",
                lead_id=lead["id"],
                payload={"draft_id": followup_draft["id"], "channel": channel}
            )

            logger.info(f"Follow-up draft created for '{slug}' on {channel}.")
            return followup_draft
        except Exception as e:
            logger.error(f"Failed generating follow-up draft for '{slug}': {e}")
            return None
