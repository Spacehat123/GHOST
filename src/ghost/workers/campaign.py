"""Campaign Orchestrator for Ghost.
Coordinates multi-batch lead generation pipelines (100 -> top third, 3 categories).
Enforces category uniqueness, domain diversity, idempotent batch processing,
and strict human-in-the-loop review boundaries.
"""
import json
import logging
import os
import re
from typing import Dict, Any, List, Optional, Set, Tuple, Callable

from ghost.crm.db import CRMDatabase
from ghost.workers.demo_state_machine import classify_business
from ghost.workers.lead_hunter import LeadHunter
from ghost.workers.lead_qualifier import LeadQualifier

logger = logging.getLogger("ghost.campaign")

CONFIG_PATH = "/home/pranc/projects/ghost/configs/campaign.json"

CATEGORY_SYNONYM_GROUPS: List[Set[str]] = [
    {"car detailing", "auto detailing", "vehicle detailing", "car valet", "car wash", "auto valet"},
    {"dentist", "dental clinic", "cosmetic dental clinic", "orthodontist", "dental practice"},
    {"gym", "boutique gym", "fitness studio", "fitness centre", "athletic club", "crossfit gym"},
    {"barbershop", "barber lounge", "gentlemen's barber", "hair salon", "hairdresser"},
    {"restaurant", "fine dining", "bistro", "eatery", "brasserie"},
    {"pub", "gastropub", "tavern", "public house", "taproom"}
]


class CampaignOrchestrator:
    """Manages end-to-end multi-batch lead campaigns."""

    def __init__(self, crm: Optional[CRMDatabase] = None):
        self.crm = crm or CRMDatabase.get_instance()
        self.hunter = LeadHunter(crm=self.crm)
        self.qualifier = LeadQualifier(crm=self.crm)

    @classmethod
    def load_default_config(cls) -> Dict[str, Any]:
        """Loads campaign defaults from configs/campaign.json."""
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed loading campaign config from {CONFIG_PATH}: {e}")
        return {
            "city": "London",
            "categories": ["car detailing", "cosmetic dental clinic", "boutique gym"],
            "harvest_per_batch": 100,
            "keep_fraction": 0.33,
            "min_rating": 4.0,
            "min_reviews": 10,
            "min_total_score": 45.0,
            "max_distance_km": None,
            "exclude_chains": True,
            "operator_location": None
        }

    @classmethod
    def check_duplicate_or_synonym(cls, categories: List[str]) -> Tuple[Optional[str], Optional[str]]:
        """
        Validates categories to ensure all are distinct.
        Detects exact duplicates and semantically synonymous categories.
        Returns (cat1, cat2) of duplicate/synonym collision, or (None, None).
        """
        normalized = [re.sub(r"[^\w\s]", "", c.lower()).strip() for c in categories]

        # 1. Exact string duplicate check
        seen: Dict[str, str] = {}
        for original, norm in zip(categories, normalized):
            if norm in seen:
                return (seen[norm], original)
            seen[norm] = original

        # 2. Semantic synonym group check
        for i in range(len(normalized)):
            for j in range(i + 1, len(normalized)):
                norm_a = normalized[i]
                norm_b = normalized[j]
                for group in CATEGORY_SYNONYM_GROUPS:
                    if norm_a in group and norm_b in group:
                        return (categories[i], categories[j])

                # 3. Classifier domain and stem check
                domain_a, _, _, _ = classify_business(norm_a, "")
                domain_b, _, _, _ = classify_business(norm_b, "")
                if domain_a == domain_b and domain_a != "general_services":
                    words_a = set(norm_a.split())
                    words_b = set(norm_b.split())
                    common = words_a.intersection(words_b)
                    # Significant overlap in same domain (e.g. "detailing", "dental", "gym")
                    if common and any(len(w) >= 4 for w in common):
                        return (categories[i], categories[j])

        return (None, None)

    def create_campaign(
        self,
        name: str,
        city: str,
        categories: List[str],
        config: Optional[Dict[str, Any]] = None,
        campaign_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates a new campaign with validated, distinct categories and initializes its batches.
        """
        if not categories or len(categories) < 1:
            raise ValueError("A campaign must contain at least one category.")

        # Duplicate & synonym check
        col_a, col_b = self.check_duplicate_or_synonym(categories)
        if col_a and col_b:
            raise ValueError(f"Duplicate or synonymous category detected in campaign: '{col_a}' and '{col_b}'.")

        # Domain diversity check
        domains: List[str] = [classify_business(cat, "")[0] for cat in categories]
        domain_warnings = []
        if len(domains) != len(set(domains)):
            domain_warnings.append("Multiple categories share catalog domains. Different catalog domains are recommended.")

        cfg = dict(self.load_default_config())
        if config:
            cfg.update(config)

        harvest_per_batch = cfg.get("harvest_per_batch", 100)
        keep_fraction = cfg.get("keep_fraction", 0.33)

        campaign = self.crm.create_campaign(
            name=name,
            city=city,
            harvest_per_batch=harvest_per_batch,
            keep_fraction=keep_fraction,
            campaign_id=campaign_id
        )

        batches = []
        for cat in categories:
            batch = self.crm.create_batch(campaign_id=campaign["id"], category=cat)
            batches.append(batch)

        campaign["batches"] = batches
        campaign["warnings"] = domain_warnings

        self.crm.record_event(
            event_type="campaign_created",
            lead_id=None,
            payload={"campaign_id": campaign["id"], "name": name, "city": city, "categories": categories}
        )

        return campaign

    async def run_next_batch(
        self,
        campaign_id: str,
        mock_provider: Optional[Callable] = None,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Runs the next uncompleted batch in the campaign:
        Harvests -> Scores & Qualifies -> Updates Batch Status to 'qualified'.
        Idempotent: Re-running a completed batch does nothing.
        Never auto-starts demo building!
        """
        campaign = self.crm.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign '{campaign_id}' not found in CRM.")

        batches = self.crm.get_batches_for_campaign(campaign_id)
        if not batches:
            raise ValueError(f"No batches found for campaign '{campaign_id}'.")

        # Find the next pending or uncompleted batch
        target_batch = None
        for b in batches:
            if b["status"] in ("pending", "harvested"):
                target_batch = b
                break

        if not target_batch:
            return {
                "status": "campaign_complete",
                "campaign_id": campaign_id,
                "message": "All batches in campaign have already been qualified.",
                "batches": batches
            }

        batch_id = target_batch["id"]
        category = target_batch["category"]
        cfg = dict(self.load_default_config())
        if config:
            cfg.update(config)

        harvest_target = campaign.get("harvest_per_batch") or cfg.get("harvest_per_batch", 100)

        # 1. Harvest Phase
        self.crm.record_event(
            event_type="batch_harvest_started",
            lead_id=None,
            payload={"batch_id": batch_id, "category": category, "target_count": harvest_target}
        )

        harvested = await self.hunter.harvest(
            city=campaign["city"],
            category=category,
            target_count=harvest_target,
            batch_id=batch_id,
            config=cfg,
            mock_provider=mock_provider
        )

        self.crm.record_event(
            event_type="batch_harvest_completed",
            lead_id=None,
            payload={"batch_id": batch_id, "harvested_count": len(harvested)}
        )

        # 2. Qualification Phase
        qual_res = self.qualifier.qualify_batch(batch_id=batch_id, campaign_config=cfg)

        self.crm.record_event(
            event_type="batch_qualified",
            lead_id=None,
            payload={
                "batch_id": batch_id,
                "qualified_count": qual_res.get("qualified_count", 0),
                "rejected_count": qual_res.get("rejected_count", 0)
            }
        )

        return {
            "status": "batch_completed",
            "campaign_id": campaign_id,
            "batch_id": batch_id,
            "category": category,
            "harvested_count": qual_res.get("harvested_count", len(harvested)),
            "qualified_count": qual_res.get("qualified_count", 0),
            "rejected_count": qual_res.get("rejected_count", 0),
            "rejection_reasons": qual_res.get("rejection_reasons", {}),
            "bias_warning": qual_res.get("bias_warning"),
            "qualified_leads": qual_res.get("qualified_leads", [])
        }
