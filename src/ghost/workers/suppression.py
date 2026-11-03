"""Suppression Management & Opt-Out System for Ghost (Part 4.3).
Maintains an immutable, centralized suppression registry (Do-Not-Contact list).
Enforces opt-outs across phones, emails, bare domains, maps_cid, and names.
Provides CSV import/export and automated intent detection.
"""
import csv
import logging
import os
import re
import time
from typing import Dict, Any, List, Optional, Tuple, Set

from ghost.crm.db import CRMDatabase

logger = logging.getLogger("ghost.suppression")

OPT_OUT_KEYWORDS = [
    r"\bstop\b",
    r"\bunsubscribe\b",
    r"\bremove\s+me\b",
    r"\bnot\s+interested\b",
    r"\bdon'?t\s+contact\b",
    r"\bleave\s+me\s+alone\b",
    r"\bno\s+thanks\b",
    r"\bopt\s*-?\s*out\b",
    r"\btake\s+me\s+off\b",
    r"\bdo\s+not\s+contact\b"
]


class SuppressionManager:
    """Manages the centralized suppression registry and enforces compliance."""

    def __init__(self, crm: Optional[CRMDatabase] = None):
        self.crm = crm or CRMDatabase.get_instance()

    def add(
        self,
        kind: str,
        value: str,
        reason: str,
        source: str = "manual"
    ) -> Dict[str, Any]:
        """Add an identifier (email, phone, domain, maps_cid, name) to suppression."""
        return self.crm.add_suppression(kind=kind, value=value, reason=reason, source=source)

    def is_suppressed(
        self,
        lead: Optional[Dict[str, Any]] = None,
        kind: Optional[str] = None,
        value: Optional[str] = None
    ) -> bool:
        """Checks if a lead or specific identifier is on the suppression list."""
        return self.crm.is_suppressed(kind=kind, value=value, lead=lead)

    @classmethod
    def detect_opt_out_intent(cls, reply_text: str) -> bool:
        """Scans prospect reply text for opt-out / unsubscribe signals."""
        if not reply_text:
            return False
        text_lower = reply_text.strip().lower()
        for kw in OPT_OUT_KEYWORDS:
            if re.search(kw, text_lower):
                return True
        return False

    def opt_out_lead(
        self,
        lead_id_or_slug: str,
        reason: str = "Prospect requested opt-out",
        source: str = "manual"
    ) -> Dict[str, Any]:
        """
        Executes a 1-click opt-out: adds all known identifiers of the lead
        (phone, email, website/domain, maps_cid, name) to the suppression list,
        marks the lead as 'suppressed' in CRM, and cancels/deletes pending drafts.
        """
        lead = self.crm.get_lead(lead_id_or_slug)
        if not lead:
            raise ValueError(f"Lead '{lead_id_or_slug}' not found in CRM.")

        slug = lead["slug"]
        suppressed_items = []

        if lead.get("phone"):
            entry = self.add(kind="phone", value=str(lead["phone"]), reason=reason, source=source)
            suppressed_items.append(entry)

        if lead.get("email"):
            entry = self.add(kind="email", value=str(lead["email"]), reason=reason, source=source)
            suppressed_items.append(entry)

        if lead.get("website"):
            entry = self.add(kind="domain", value=str(lead["website"]), reason=reason, source=source)
            suppressed_items.append(entry)

        if lead.get("maps_cid"):
            entry = self.add(kind="maps_cid", value=str(lead["maps_cid"]), reason=reason, source=source)
            suppressed_items.append(entry)

        if lead.get("name"):
            entry = self.add(kind="name", value=str(lead["name"]), reason=reason, source=source)
            suppressed_items.append(entry)

        # Update lead status to 'suppressed' in CRM
        self.crm.update_lead(slug, status="suppressed")
        self.crm.update_lead_status(slug, "suppressed")

        # Invalidate / cancel active drafts for this lead
        drafts = self.crm.get_drafts_for_lead(lead["id"])
        for d in drafts:
            if d.get("status") in ("draft", "approved"):
                self.crm.add_or_update_draft(
                    draft_id=d["id"],
                    lead_id=lead["id"],
                    channel=d["channel"],
                    body=d["body"],
                    status="suppressed"
                )

        self.crm.record_event(
            event_type="lead_opted_out",
            lead_id=lead["id"],
            payload={"slug": slug, "reason": reason, "suppressed_count": len(suppressed_items)}
        )

        logger.info(f"Lead '{slug}' successfully opted out. {len(suppressed_items)} identifiers suppressed.")
        return {
            "status": "success",
            "slug": slug,
            "suppressed_identifiers": suppressed_items
        }

    def import_csv(
        self,
        csv_path: str,
        default_reason: str = "Imported suppression"
    ) -> int:
        """Imports suppression records from a CSV file (columns: kind, value, reason)."""
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"CSV file not found: {csv_path}")

        count = 0
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # If headers are missing, fallback to standard reader
            if not reader.fieldnames or "kind" not in reader.fieldnames:
                f.seek(0)
                raw_reader = csv.reader(f)
                for row in raw_reader:
                    if not row:
                        continue
                    if len(row) == 1:
                        val = row[0].strip()
                        kind = "email" if "@" in val else ("phone" if re.search(r"\d{7,}", val) else "domain")
                        self.add(kind=kind, value=val, reason=default_reason, source="csv_import")
                        count += 1
                    elif len(row) >= 2:
                        self.add(kind=row[0].strip(), value=row[1].strip(), reason=row[2].strip() if len(row) > 2 else default_reason, source="csv_import")
                        count += 1
            else:
                for row in reader:
                    kind = row.get("kind", "").strip()
                    value = row.get("value", "").strip()
                    reason = row.get("reason", default_reason).strip()
                    if kind and value:
                        self.add(kind=kind, value=value, reason=reason, source="csv_import")
                        count += 1

        logger.info(f"Imported {count} suppression records from {csv_path}.")
        return count

    def export_csv(self, csv_path: str) -> int:
        """Exports all suppression entries to a CSV file."""
        records = self.crm.list_suppressions()
        os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "kind", "value_normalised", "reason", "added_at", "source"])
            writer.writeheader()
            for r in records:
                writer.writerow(r)

        logger.info(f"Exported {len(records)} suppression records to {csv_path}.")
        return len(records)
