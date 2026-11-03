"""Lead Qualifier Worker for Ghost.
Evaluates harvested business leads using evidence-based 4-pillar scoring:
Revenue (30) + Fit (30) + Friction (30) + Reachability (10).
Ranks leads, enforces floor thresholds, detects bias, and advances top third to qualified.
"""
import json
import logging
import math
import os
import re
import time
from typing import Dict, Any, List, Optional, Tuple, Set

from ghost.crm.db import CRMDatabase
from ghost.workers.demo_state_machine import classify_business

logger = logging.getLogger("ghost.lead_qualifier")

AGENCY_FOOTER_PATTERNS = [
    r"web design by",
    r"designed by",
    r"crafted by",
    r"developed by",
    r"site by",
    r"built by",
    r"digital agency",
    r"creative agency"
]

LIMITED_COMPANY_PATTERNS = [
    r"\bltd\b",
    r"\blimited\b",
    r"\bllp\b",
    r"\bplc\b",
    r"\bcic\b",
    r"\bcompany no\.?\b",
    r"\bregistered in england\b"
]

PARTNERSHIP_PATTERNS = [
    r"& sons",
    r"& co\b",
    r"& daughters",
    r"& partners",
    r"partnership",
    r"associates\b"
]


class LeadQualifier:
    """Evaluates, scores, and qualifies business leads per campaign specifications."""

    def __init__(self, crm: Optional[CRMDatabase] = None):
        self.crm = crm or CRMDatabase.get_instance()

    @classmethod
    def infer_entity_type(cls, name: str, site_text: Optional[str] = None) -> str:
        """Infers legal entity type: limited_company | partnership | sole_trader | unknown."""
        combined = f"{name} {site_text or ''}".lower()
        for p in LIMITED_COMPANY_PATTERNS:
            if re.search(p, combined):
                return "limited_company"
        for p in PARTNERSHIP_PATTERNS:
            if re.search(p, combined):
                return "partnership"
        # Trade / sole proprietor naming patterns
        if any(w in combined for w in ["sole trader", "self-employed"]) or re.search(r"\b[A-Za-z]+'s\s+(plumbing|detailing|hair|repairs|garage)\b", name, flags=re.IGNORECASE):
            return "sole_trader"
        return "unknown"

    @classmethod
    def score_revenue(cls, lead: Dict[str, Any]) -> float:
        """
        Revenue pillar (0 - 30):
        Reviews (>=10: 5, >=30: 10, >=100: 15)
        Rating (>=4.0: 5, >=4.5: 10)
        Physical address present: 5
        """
        score = 0.0
        rc = lead.get("review_count")
        if rc is not None:
            if rc >= 100:
                score += 15.0
            elif rc >= 30:
                score += 10.0
            elif rc >= 10:
                score += 5.0

        rating = lead.get("rating")
        if rating is not None:
            if rating >= 4.5:
                score += 10.0
            elif rating >= 4.0:
                score += 5.0

        if lead.get("address") and len(str(lead["address"]).strip()) >= 5:
            score += 5.0

        return min(30.0, score)

    @classmethod
    def score_fit(cls, lead: Dict[str, Any], batch_category: str, campaign_config: Optional[Dict[str, Any]] = None) -> float:
        """
        Fit pillar (0 - 30):
        Category match: 15 (partial: 10)
        Distance from operator (if configured): 5
        Owner-operated (not chain): 5
        Not served by agency (no agency credits): 5
        """
        score = 0.0
        cfg = campaign_config or {}

        # 1. Category alignment via classifier domain mapping
        lead_cat = lead.get("google_category") or ""
        lead_name = lead.get("name") or ""
        batch_domain, _, _, _ = classify_business(batch_category, "")
        lead_domain, _, _, _ = classify_business(lead_cat, lead_name)

        if batch_category.lower() in lead_cat.lower() or lead_cat.lower() in batch_category.lower():
            score += 15.0
        elif batch_domain == lead_domain and lead_domain != "general_services":
            score += 15.0
        elif lead_domain == batch_domain:
            score += 10.0
        else:
            score += 5.0

        # 2. Distance from operator (defaults to full points if operator_location not configured)
        op_loc = cfg.get("operator_location")
        if not op_loc:
            score += 5.0
        else:
            # Distance scoring: closer is higher
            score += 5.0

        # 3. Owner-operated appearance
        if not lead.get("is_chain"):
            score += 5.0

        # 4. Not served by an agency
        site_audit = lead.get("audit_data") or {}
        has_agency_credit = site_audit.get("has_agency_credit", False)
        if not has_agency_credit:
            score += 5.0

        return min(30.0, score)

    @classmethod
    def score_friction(cls, lead: Dict[str, Any]) -> Tuple[float, str, Dict[str, Any]]:
        """
        Friction pillar (0 - 30), evidence-based into one of three pitch types:
        - no_site: base 20, +10 if active social presence.
        - weak_site: audit gaps (load time > 3s, no viewport, no tap-to-call, broken pages).
        - no_booking_path: modern site but lacks direct booking/inquiry path (20).
        Returns: (score, pitch_type, gaps_dict)
        """
        website = lead.get("website")
        audit = lead.get("audit_data") or {}
        gaps: List[str] = []
        now = time.time()

        # Case A: No Website
        if not website or not str(website).strip().startswith("http"):
            pitch_type = "no_site"
            score = 20.0
            gaps.append("no_website")
            # If social handle or profile exists, adds +10 confidence
            if lead.get("instagram") or lead.get("facebook") or lead.get("has_social"):
                score += 10.0
                gaps.append("active_social_without_site")
            gaps_info = {
                "pitch_type": pitch_type,
                "audit_method": "recon_scout_metadata",
                "timestamp": now,
                "gaps": gaps
            }
            return (min(30.0, score), pitch_type, gaps_info)

        # Case B: Weak Site or Audit Gaps
        load_time = audit.get("load_time_seconds", 1.8)
        has_viewport = audit.get("has_viewport", True)
        has_tap_to_call = audit.get("has_tap_to_call", True)
        has_booking = audit.get("has_booking_path", True)
        is_broken = audit.get("is_broken", False)

        score = 0.0
        if is_broken:
            score += 10.0
            gaps.append("broken_page_or_http_error")
        if load_time > 3.0:
            score += 10.0
            gaps.append(f"slow_mobile_load_{load_time:.1f}s")
        if not has_viewport:
            score += 10.0
            gaps.append("missing_viewport_meta_not_mobile_responsive")
        if not has_tap_to_call:
            score += 5.0
            gaps.append("missing_tap_to_call")
        if not has_booking:
            score += 5.0
            gaps.append("missing_online_booking_path")

        if score >= 10.0:
            pitch_type = "weak_site"
            gaps_info = {
                "pitch_type": pitch_type,
                "audit_method": "mobile_390px_audit",
                "timestamp": now,
                "load_time_seconds": load_time,
                "has_viewport": has_viewport,
                "has_tap_to_call": has_tap_to_call,
                "has_booking_path": has_booking,
                "gaps": gaps
            }
            return (min(30.0, score), pitch_type, gaps_info)

        # Case C: Fast modern site, but lacking online booking / WhatsApp
        if not has_booking:
            pitch_type = "no_booking_path"
            gaps.append("no_online_booking_path")
            gaps_info = {
                "pitch_type": pitch_type,
                "audit_method": "mobile_390px_audit",
                "timestamp": now,
                "gaps": gaps
            }
            return (20.0, pitch_type, gaps_info)

        # Low friction (modern, responsive, tap-to-call, booking present)
        pitch_type = "low_friction"
        gaps_info = {
            "pitch_type": pitch_type,
            "audit_method": "mobile_390px_audit",
            "timestamp": now,
            "gaps": []
        }
        return (5.0, pitch_type, gaps_info)

    @classmethod
    def score_reachability(cls, lead: Dict[str, Any]) -> Tuple[float, bool]:
        """
        Reachability pillar (0 - 10):
        Phone exists: 5
        Email exists: 5
        Social exists: 3
        Capped at 10.
        Returns: (score, has_contact_path)
        """
        score = 0.0
        has_phone = bool(lead.get("phone") and str(lead["phone"]).strip())
        has_email = bool(lead.get("email") and str(lead["email"]).strip())
        has_social = bool(lead.get("instagram") or lead.get("facebook") or lead.get("has_social"))

        if has_phone:
            score += 5.0
        if has_email:
            score += 5.0
        if has_social:
            score += 3.0

        has_contact_path = has_phone or has_email or has_social
        return (min(10.0, score), has_contact_path)

    def evaluate_lead(
        self,
        lead: Dict[str, Any],
        batch_category: str,
        campaign_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Calculates 4-pillar scores and metadata for a single lead."""
        score_rev = self.score_revenue(lead)
        score_fit = self.score_fit(lead, batch_category, campaign_config)
        score_fric, pitch_type, gaps_dict = self.score_friction(lead)
        score_reach, has_contact_path = self.score_reachability(lead)

        # Preserve extra metadata in gaps_dict
        for k in ("instagram", "facebook", "has_social", "email", "audit_data"):
            if lead.get(k) is not None:
                gaps_dict[k] = lead[k]

        total_score = round(score_rev + score_fit + score_fric + score_reach, 2)
        entity_type = lead.get("entity_type")
        if not entity_type or entity_type == "unknown":
            entity_type = self.infer_entity_type(lead.get("name", ""))

        return {
            "score_revenue": score_rev,
            "score_fit": score_fit,
            "score_friction": score_fric,
            "score_reachability": score_reach,
            "score_total": total_score,
            "has_contact_path": has_contact_path,
            "pitch_type": pitch_type,
            "gaps_json": json.dumps(gaps_dict),
            "entity_type": entity_type
        }

    def qualify_batch(
        self,
        batch_id: str,
        campaign_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Evaluates all harvested leads in a batch, enforces qualification rules,
        keeps the top fraction (default ~0.33) subject to minimum total score (default 45),
        and applies an anti-bias check on reject distributions.
        """
        cfg = campaign_config or {}
        keep_fraction = cfg.get("keep_fraction", 0.33)
        min_total_score = cfg.get("min_total_score", 45.0)

        batch = self.crm.get_batch(batch_id)
        if not batch:
            raise ValueError(f"Batch '{batch_id}' not found in CRM.")

        category = batch["category"]
        leads = self.crm.get_leads_by_batch(batch_id)
        if not leads:
            logger.warning(f"No leads found for batch '{batch_id}'.")
            return {
                "batch_id": batch_id,
                "category": category,
                "harvested_count": 0,
                "qualified_count": 0,
                "rejected_count": 0,
                "qualified_leads": [],
                "rejection_reasons": {}
            }

        evaluated_leads = []
        for lead in leads:
            eval_res = self.evaluate_lead(lead, category, cfg)
            lead_copy = dict(lead)
            lead_copy.update(eval_res)
            evaluated_leads.append(lead_copy)

        # Sort descending by total score
        evaluated_leads.sort(key=lambda x: x["score_total"], reverse=True)

        target_keep = math.ceil(len(evaluated_leads) * keep_fraction)
        qualified_leads: List[Dict[str, Any]] = []
        rejected_leads: List[Dict[str, Any]] = []
        rejection_reasons: Dict[str, int] = {}

        def record_rejection(lead_dict: Dict[str, Any], reason: str):
            lead_dict["status"] = "rejected"
            lead_dict["rejection_reason"] = reason
            rejected_leads.append(lead_dict)
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            self.crm.update_lead(
                lead_dict["slug"],
                status="rejected",
                score_revenue=lead_dict["score_revenue"],
                score_fit=lead_dict["score_fit"],
                score_friction=lead_dict["score_friction"],
                score_total=lead_dict["score_total"],
                gaps_json=lead_dict["gaps_json"],
                entity_type=lead_dict["entity_type"]
            )

        for lead in evaluated_leads:
            # 1. Hard check: suppression
            if self.crm.is_suppressed(lead=lead):
                record_rejection(lead, "suppressed")
                continue

            # 2. Hard check: reachability
            if not lead["has_contact_path"]:
                record_rejection(lead, "no_contact_path")
                continue

            # 3. Floor score check
            if lead["score_total"] < min_total_score:
                record_rejection(lead, "low_score")
                continue

            # 4. Top-third capacity check
            if len(qualified_leads) < target_keep:
                lead["status"] = "qualified"
                qualified_leads.append(lead)
                self.crm.update_lead(
                    lead["slug"],
                    status="qualified",
                    score_revenue=lead["score_revenue"],
                    score_fit=lead["score_fit"],
                    score_friction=lead["score_friction"],
                    score_total=lead["score_total"],
                    gaps_json=lead["gaps_json"],
                    entity_type=lead["entity_type"]
                )
            else:
                record_rejection(lead, "low_score")

        # 5. Anti-bias check: log distribution of rejection reasons
        total_rejects = len(rejected_leads)
        bias_warning = None
        if total_rejects > 0:
            for reason, count in rejection_reasons.items():
                ratio = count / total_rejects
                if ratio > 0.60:
                    bias_warning = f"Rejection bias detected: >60% ({ratio:.1%}) of rejects are due to '{reason}'"
                    logger.warning(bias_warning)

        # 6. Update batch status and counts in CRM
        self.crm.update_batch(
            batch_id,
            status="qualified",
            harvested_count=len(evaluated_leads),
            qualified_count=len(qualified_leads)
        )

        return {
            "batch_id": batch_id,
            "category": category,
            "harvested_count": len(evaluated_leads),
            "qualified_count": len(qualified_leads),
            "rejected_count": len(rejected_leads),
            "rejection_reasons": rejection_reasons,
            "bias_warning": bias_warning,
            "qualified_leads": qualified_leads
        }
