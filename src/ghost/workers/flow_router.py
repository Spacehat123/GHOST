"""Flow Router for Ghost Autonomous Web Agency.
Routes qualified leads to one of three production flows:
1. 'standard': High-velocity sectional batch factory (~33/batch).
2. 'motion': Premium non-3D flagship with signature interaction (top 3-5/batch).
3. '3d': Cinematic Three.js flagship with camera stages & licensed GLB (top 1-3/batch).

Enforces non-negotiable architectural invariants:
- Default flow is always 'standard'.
- Flagships are never triggered silently; requires operator proposal and approval.
- Operator override always wins, with transparent reporting of missing prerequisites.
- Enforces batch quotas and visual sector eligibility.
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional

from ghost.workers.fit_3d import Fit3DAssessor

logger = logging.getLogger("ghost.flow_router")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
CONFIG_PATH = os.path.join(REPO_DIR, "configs", "flows.json")


def _load_flow_config() -> Dict[str, Any]:
    """Load flows config with fallback defaults."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load flows config: {e}")
    return {
        "batch_limits": {
            "max_3d_flagship_per_batch": 3,
            "max_motion_flagship_per_batch": 5,
            "default_flow": "standard"
        },
        "visual_experience_sectors": [
            "automotive_detailing", "detailing", "dental_medical", "dental",
            "salon_spa", "salon", "spa", "beauty", "gyms_fitness", "fitness", "gym",
            "boutique_tailor", "boutique", "tailor", "luxury_hospitality", "hotel",
            "fine_dining", "restaurant", "photography", "wedding"
        ]
    }


class FlowRouter:
    """Intelligent flow router for client website production."""

    @classmethod
    def choose_flow(
        cls,
        lead_slug: str,
        operator_choice: Optional[str] = None,
        lead_data: Optional[Dict[str, Any]] = None,
        batch_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Evaluate and select appropriate website flow: 'standard', 'motion', or '3d'.
        Returns flow, reasons, blockers, and whether operator approval is needed.
        """
        cfg = _load_flow_config()
        data = lead_data or {}
        batch = batch_context or {}

        reasons: List[str] = []
        blockers: List[str] = []

        category = str(data.get("category", "")).lower()
        domain = str(data.get("domain", "")).lower()
        business_name = str(data.get("business_name", lead_slug)).lower()
        lead_score = float(data.get("lead_score", 0.0) or data.get("score", 0.0) or 0.0)
        has_licensed_glb = bool(data.get("has_licensed_glb", False))
        batch_rank = int(batch.get("batch_rank", 99))
        current_motion_count = int(batch.get("current_motion_count", 0))
        current_3d_count = int(batch.get("current_3d_count", 0))

        visual_sectors = cfg.get("visual_experience_sectors", [])
        is_visual_sector = any(sec in category or sec in domain or sec in business_name for sec in visual_sectors)

        # 1. OPERATOR OVERRIDE LOGIC
        if operator_choice:
            choice = operator_choice.strip().lower()
            if choice in ["3d", "three", "threejs"]:
                if not has_licensed_glb:
                    blockers.append("3D requested by operator, but no licensed GLB model found on disk or ledger.")
                reasons.append("Operator explicitly mandated '3d' flagship flow.")
                return {
                    "ok": True,
                    "status": "success",
                    "tool": "choose_flow",
                    "lead_slug": lead_slug,
                    "flow": "3d",
                    "reasons": reasons,
                    "blockers": blockers,
                    "needs_operator_approval": False
                }
            elif choice in ["motion", "motion_flagship", "flagship"]:
                reasons.append("Operator explicitly mandated 'motion' flagship flow.")
                return {
                    "ok": True,
                    "status": "success",
                    "tool": "choose_flow",
                    "lead_slug": lead_slug,
                    "flow": "motion",
                    "reasons": reasons,
                    "blockers": blockers,
                    "needs_operator_approval": False
                }
            elif choice in ["standard", "batch"]:
                reasons.append("Operator explicitly selected 'standard' batch flow.")
                return {
                    "ok": True,
                    "status": "success",
                    "tool": "choose_flow",
                    "lead_slug": lead_slug,
                    "flow": "standard",
                    "reasons": reasons,
                    "blockers": blockers,
                    "needs_operator_approval": False
                }

        # 2. AUTOMATIC EVALUATION: CHECK 3D FIRST
        fit_3d = Fit3DAssessor.assess(lead_slug=lead_slug, lead_data=data)
        max_3d = cfg.get("batch_limits", {}).get("max_3d_flagship_per_batch", 3)

        if fit_3d["recommendation"] == "propose" and has_licensed_glb:
            if current_3d_count < max_3d:
                reasons.extend(fit_3d["reasons"])
                reasons.append("Lead qualifies for flagship 3D experience. Requires operator sign-off before build.")
                return {
                    "ok": True,
                    "status": "success",
                    "tool": "choose_flow",
                    "lead_slug": lead_slug,
                    "flow": "3d",
                    "reasons": reasons,
                    "blockers": fit_3d["blockers"],
                    "needs_operator_approval": True
                }
            else:
                reasons.append(f"Batch quota for 3D flagships reached ({current_3d_count}/{max_3d}). Downgrading consideration.")

        # 3. CHECK MOTION FLAGSHIP
        max_motion = cfg.get("batch_limits", {}).get("max_motion_flagship_per_batch", 5)
        # Condition: Top 5 in batch (rank <= 5 or score >= 70) AND visual sector
        is_top_candidate = (batch_rank <= 5 or lead_score >= 70.0)

        # Check for emergency trade blockers
        if fit_3d.get("blockers"):
            for b in fit_3d["blockers"]:
                if "emergency" in b.lower() or "convenience" in b.lower():
                    blockers.append(b)

        if not blockers and is_visual_sector and is_top_candidate:
            if current_motion_count < max_motion:
                reasons.append(f"High-ranking visual sector candidate (Score: {lead_score}, Rank: {batch_rank}).")
                reasons.append("Recommends 'motion' flagship with bespoke signature interaction.")
                reasons.append("Flagship upgrade requires operator approval.")
                return {
                    "ok": True,
                    "status": "success",
                    "tool": "choose_flow",
                    "lead_slug": lead_slug,
                    "flow": "motion",
                    "reasons": reasons,
                    "blockers": blockers,
                    "needs_operator_approval": True
                }
            else:
                reasons.append(f"Batch quota for motion flagships reached ({current_motion_count}/{max_motion}).")

        # 4. DEFAULT: STANDARD BATCH FACTORY
        reasons.append("Standard high-velocity single-page conversion flow selected.")
        return {
            "ok": True,
            "status": "success",
            "tool": "choose_flow",
            "lead_slug": lead_slug,
            "flow": "standard",
            "reasons": reasons,
            "blockers": blockers,
            "needs_operator_approval": False
        }
