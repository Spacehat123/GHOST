"""3D Fit Assessment Gate for Ghost Web Agency.
Enforces non-negotiable architectural invariant:
3D sites are NEVER built by default. A 3D site requires either an explicit
operator command or an approved proposal for a qualified high-ticket lead.
"""
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("ghost.fit_3d")

EMERGENCY_AND_LOCAL_TRADES = {
    "plumbing", "plumber", "emergency", "locksmith", "towing", "roofing",
    "electrician", "hvac", "pest control", "cleaning", "handyman", "auto repair"
}

HIGH_TICKET_3D_SECTORS = {
    "automotive_detailing": {"weight": 35, "hero": "vehicle"},
    "architecture": {"weight": 35, "hero": "building"},
    "luxury_real_estate": {"weight": 35, "hero": "building"},
    "product_design": {"weight": 30, "hero": "product"},
    "jewelry": {"weight": 30, "hero": "product"},
    "luxury_hospitality": {"weight": 25, "hero": "building"},
    "fine_dining": {"weight": 20, "hero": "interior"},
    "gyms_fitness": {"weight": 20, "hero": "interior"}
}


class Fit3DAssessor:
    """Evaluates whether a lead justifies an expensive 3D website build."""

    @classmethod
    def assess(
        cls,
        lead_slug: str,
        lead_data: Optional[Dict[str, Any]] = None,
        operator_requested: bool = False
    ) -> Dict[str, Any]:
        """
        Assess 3D suitability for a lead.
        Returns score (0-100), reasons, blockers, and recommendation.
        """
        data = lead_data or {}
        category = str(data.get("category", "")).lower()
        business_name = str(data.get("business_name", lead_slug)).lower()
        domain = str(data.get("domain", "")).lower()
        deal_value = float(data.get("deal_value", 0.0) or 0.0)
        has_licensed_glb = data.get("has_licensed_glb", True)  # Can be found or supplied
        connection_profile = data.get("connection_profile", "broadband")

        reasons: List[str] = []
        blockers: List[str] = []
        score = 0
        hero_object: Optional[str] = None

        # 1. BLOCKER EVALUATION
        # Blocker 1: Low-end mobile / emergency trade
        is_emergency_trade = any(t in category or t in business_name or t in domain for t in EMERGENCY_AND_LOCAL_TRADES)
        if is_emergency_trade:
            blockers.append("Emergency/local convenience trade: audience requires instant phone/directions, not 3D.")

        # Blocker 2: Slow connection profile
        if connection_profile == "2g_3g_slow":
            blockers.append("Audience primarily on slow mobile connections: 3D GLB weight would cause bounce.")

        # Blocker 3: No usable GLB obtainable
        if not has_licensed_glb:
            blockers.append("No usable GLB model obtainable with permissive CC0/CC-BY license.")

        # Blocker 4: Business explicitly requires simple text info page
        if data.get("plain_info_only"):
            blockers.append("Lead explicitly requested plain informational page.")

        # 2. FIT SIGNALS & SCORING
        # Check high-ticket visual sectors
        matched_sector = None
        for sec, info in HIGH_TICKET_3D_SECTORS.items():
            if sec in domain or sec in category or sec.replace("_", " ") in category:
                matched_sector = sec
                score += info["weight"]
                hero_object = info["hero"]
                reasons.append(f"High-ticket visual sector ({sec.replace('_', ' ')}): sells physical craftsmanship.")
                break

        # Fallback keyword match in name or category
        if not matched_sector:
            if any(w in business_name or w in category for w in ["detailing", "supercar", "exotic", "porsche", "ferrari"]):
                score += 30
                hero_object = "vehicle"
                reasons.append("Automotive detailing / exotic studio: vehicles are ideal 3D hero subjects.")
            elif any(w in business_name or w in category for w in ["architect", "villa", "residence", "estate", "penthouse"]):
                score += 30
                hero_object = "building"
                reasons.append("Architectural / luxury real estate: structures benefit from multi-stage camera exploration.")
            elif any(w in business_name or w in category for w in ["watch", "horology", "jewelry", "eyewear"]):
                score += 25
                hero_object = "product"
                reasons.append("Precision product manufacture: ideal for micro-chamfer and material wireframe inspection.")

        # Deal value / budget signals
        if deal_value >= 1000.0 or data.get("high_ticket"):
            score += 25
            reasons.append(f"High-ticket commercial engagement (${deal_value:,.0f}): justifies flagship production.")
        elif deal_value > 0:
            score += 10

        # Review / brand signals
        review_count = int(data.get("review_count", 0) or 0)
        rating = float(data.get("rating", 0.0) or 0.0)
        if review_count >= 30 and rating >= 4.5:
            score += 15
            reasons.append(f"Established premium reputation ({rating}★ from {review_count} reviews).")

        # 3. RECOMMENDATION
        score = min(100, max(0, score))

        if blockers:
            score = min(score, 25)
            recommendation = "no"
        elif operator_requested:
            score = max(score, 75)
            recommendation = "operator_requested"
            reasons.append("Operator explicitly requested 3D site build.")
        elif score >= 60:
            recommendation = "propose"
            reasons.append("Exceeds 3D fit threshold (60+): Ghost proposes flagship 3D demo to operator.")
        else:
            recommendation = "no"
            reasons.append("Below 3D threshold: standard luxury single-page demo recommended.")

        return {
            "ok": True,
            "status": "success",
            "tool": "assess_3d_fit",
            "lead_slug": lead_slug,
            "score": score,
            "recommendation": recommendation,
            "hero_object": hero_object,
            "reasons": reasons,
            "blockers": blockers
        }
