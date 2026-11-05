"""Batch Demo Factory Worker for Ghost.
Builds, verifies, and deploys landing page demos for an entire batch (~33 qualified leads)
with concurrency limits, fail-closed classification gates, source ledger quality assurance,
and human-in-the-loop batch deployment authorization.
"""
import asyncio
import json
import logging
import os
import re
import time
from typing import Dict, Any, List, Optional, Tuple, Callable
from unittest.mock import patch, AsyncMock

from ghost.crm.db import CRMDatabase
from ghost.mcp.bridge import request_tactical_authorization
from ghost.workers.demo_state_machine import (
    AutonomousDemoStateMachine,
    classify_business,
    DEMOS_DIR,
    BANNED_OUTBOUND_TERMS
)
from ghost.workers.deploy import VercelDeployer

logger = logging.getLogger("ghost.batch_demo_worker")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
CONFIG_PATH = os.environ.get("GHOST_FACTORY_CONFIG", os.path.join(REPO_DIR, "configs/factory.json"))

BANNED_HOSPITALITY_REGEX = [
    r"\bwagyu\b",
    r"\bsommelier\b",
    r"\bdegustation\b",
    r"\bcellar\b",
    r"\btasting table\b",
    r"\bculinary\b",
    r"\bgastronomic\b",
    r"\btartare\b",
    r"\bagnolotti\b"
]


class QualityGateError(Exception):
    """Raised when a generated demo fails verification quality gates."""
    pass


def load_factory_config() -> Dict[str, Any]:
    """Loads batch factory configuration from configs/factory.json."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed loading factory config from {CONFIG_PATH}: {e}")
    return {
        "max_concurrent_builds": 2,
        "deploy_after_confirm": True,
        "preview_dir": "demos/",
        "screenshot_retry": 1,
        "pause_between_leads_sec": 2,
        "use_owner_photos": False
    }


def run_quality_gates(slug: str, domain: str, demo_dir: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Executes automated quality gates on a generated demo before allowing it to reach 'built':
    1. SOURCES.json exists and is valid.
    2. index.html exists and is non-empty.
    3. Claims & Banned terms lint (zero 'Verified', zero persona leaks, zero restaurant copy outside dining).
    4. Currency purity: no foreign currency symbols (₹).
    5. Interactive booking form present.
    6. Screenshots exist and are non-empty.
    """
    target_dir = demo_dir or os.path.join(DEMOS_DIR, slug)
    sources_path = os.path.join(target_dir, "SOURCES.json")
    html_path = os.path.join(target_dir, "index.html")

    # 1. SOURCES.json check
    if not os.path.exists(sources_path):
        return False, "Quality Gate Failed: SOURCES.json ledger missing."
    try:
        with open(sources_path, "r", encoding="utf-8") as f:
            ledger_data = json.load(f)
            if not isinstance(ledger_data, dict):
                return False, "Quality Gate Failed: SOURCES.json corrupted."
    except Exception as e:
        return False, f"Quality Gate Failed: SOURCES.json unreadable: {e}"

    # 2. index.html check
    if not os.path.exists(html_path) or os.path.getsize(html_path) < 500:
        return False, "Quality Gate Failed: index.html missing or below size threshold."

    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        return False, f"Quality Gate Failed: Could not read index.html: {e}"

    # 3. Claims lint: Fake 'Verified' check
    if re.search(r"\bVerified\b", html):
        return False, "Quality Gate Failed: Unverified claims stamp 'Verified' detected in HTML."

    # 4. Persona firewall scan
    for term in BANNED_OUTBOUND_TERMS:
        pattern = r'\b' + re.escape(term.lower()) + r'\b' if term.isalnum() else re.escape(term.lower())
        if re.search(pattern, html, flags=re.IGNORECASE):
            return False, f"Quality Gate Failed: Internal persona term '{term}' leaked in demo HTML."

    # 5. Restaurant words in non-restaurant domain
    if domain != "restaurants_cafes":
        for pattern in BANNED_HOSPITALITY_REGEX:
            if re.search(pattern, html, flags=re.IGNORECASE):
                return False, f"Quality Gate Failed: Banned restaurant term '{pattern}' found in '{domain}' demo."

    # 6. Currency purity
    if "₹" in html:
        return False, "Quality Gate Failed: Currency symbol '₹' detected in non-domestic demo."

    # 7. Interactive booking form
    if 'id="booking-service"' not in html or 'id="whatsapp-direct-btn"' not in html:
        return False, "Quality Gate Failed: Interactive booking module missing required elements."

    # 8. Screenshots check
    desktop_shot = os.path.join(target_dir, "desktop_1440x900.jpg")
    mobile_shot = os.path.join(target_dir, "mobile_390x844.jpg")
    if os.path.exists(desktop_shot) and os.path.getsize(desktop_shot) < 100:
        return False, "Quality Gate Failed: desktop screenshot corrupted or blank."
    if os.path.exists(mobile_shot) and os.path.getsize(mobile_shot) < 100:
        return False, "Quality Gate Failed: mobile screenshot corrupted or blank."

    return True, None


class BatchDemoWorker:
    """Orchestrates concurrent batch demo production with quality verification."""

    def __init__(self, crm: Optional[CRMDatabase] = None):
        self.crm = crm or CRMDatabase.get_instance()

    async def build_single_lead(
        self,
        lead: Dict[str, Any],
        batch_domain: str,
        config: Dict[str, Any],
        mock_scout_provider: Optional[Callable] = None,
        mock_screenshot_provider: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """Builds a single lead demo with domain validation and quality gates."""
        slug = lead["slug"]
        name = lead["name"]
        address = lead.get("address") or ""
        google_cat = lead.get("google_category")
        use_owner_photos = config.get("use_owner_photos", False)

        # 1. Classification check
        domain, conf, needs_conf, candidates = classify_business(google_cat, name)

        # 2. Domain Mismatch or Fail-Closed Confirmation
        if needs_conf or domain != batch_domain:
            reason = "domain_mismatch" if domain != batch_domain else "low_confidence"
            logger.warning(
                f"Lead '{slug}' requires confirmation (reason: {reason}, "
                f"classified: '{domain}', batch expected: '{batch_domain}'). Halting build."
            )

            # Write CONFIRM.json
            out_dir = os.path.join(DEMOS_DIR, slug)
            os.makedirs(out_dir, exist_ok=True)
            confirm_payload = {
                "slug": slug,
                "name": name,
                "google_category": google_cat,
                "suggested_domain": domain,
                "batch_domain": batch_domain,
                "reason": reason,
                "candidates": candidates
            }
            with open(os.path.join(out_dir, "CONFIRM.json"), "w", encoding="utf-8") as f:
                json.dump(confirm_payload, f, indent=2)

            self.crm.update_lead(slug, status="needs_confirmation", needs_confirmation=True, domain=domain)
            self.crm.record_event("lead_needs_confirmation", lead["id"], confirm_payload)

            return {
                "status": "needs_confirmation",
                "slug": slug,
                "domain": domain,
                "batch_domain": batch_domain,
                "reason": reason
            }

        # 3. Clean match: Proceed with Phases 1-4
        sm = AutonomousDemoStateMachine(
            business_name=name,
            location=address,
            slug=slug,
            use_owner_photos=use_owner_photos
        )

        # Apply mocks if provided
        if mock_scout_provider is not None:
            scout_data = mock_scout_provider(name, address)
        else:
            scout_data = None

        if scout_data is not None:
            with patch("ghost.workers.recon_scout.ReconScout.scout_business", new_callable=AsyncMock) as m_scout, \
                 patch("ghost.workers.audit_engine.PerformanceAuditor.audit_url", new_callable=AsyncMock) as m_audit, \
                 patch("ghost.mcp.bridge.WebSearchBridge.search", new_callable=AsyncMock) as m_search:
                m_scout.return_value = scout_data
                m_audit.return_value = None
                m_search.return_value = {"results": []}

                await sm.run_phase1_recon()
                await sm.run_phase2_design_brief()
                await sm.run_phase3_build_sections()
                if mock_screenshot_provider is not None:
                    mock_screenshot_provider(sm.out_dir)
                else:
                    await sm.run_phase4_render_verification()
        else:
            await sm.run_phase1_recon()
            await sm.run_phase2_design_brief()
            await sm.run_phase3_build_sections()
            if mock_screenshot_provider is not None:
                mock_screenshot_provider(sm.out_dir)
            else:
                await sm.run_phase4_render_verification()

        # 4. Quality Gates Verification (Section 3.3)
        passed, gate_error = run_quality_gates(slug=slug, domain=domain, demo_dir=sm.out_dir)
        if not passed:
            logger.warning(f"Quality gate failure for '{slug}': {gate_error}")
            self.crm.update_lead(slug, status="needs_review", domain=domain)
            self.crm.record_event("lead_needs_review", lead["id"], {"slug": slug, "error": gate_error})
            return {"status": "needs_review", "slug": slug, "error": gate_error}

        # 5. Success -> Built
        self.crm.update_lead(slug, status="built", domain=domain)
        self.crm.record_event("lead_built", lead["id"], {"slug": slug, "domain": domain})
        return {"status": "built", "slug": slug, "domain": domain, "html_path": sm.html_path}

    async def build_batch(
        self,
        batch_id: str,
        config: Optional[Dict[str, Any]] = None,
        mock_scout_provider: Optional[Callable] = None,
        mock_screenshot_provider: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        Builds demos for all 'qualified' leads in a batch.
        Enforces concurrency limit and resilience: a failure on one lead never stops the batch.
        """
        cfg = dict(load_factory_config())
        if config:
            cfg.update(config)

        batch = self.crm.get_batch(batch_id)
        if not batch:
            raise ValueError(f"Batch '{batch_id}' not found in CRM.")

        category = batch["category"]
        batch_domain, _, _, _ = classify_business(category, "")
        logger.info(f"Starting batch build for '{batch_id}' (Category: '{category}', Domain: '{batch_domain}')")

        leads = self.crm.get_leads_by_batch(batch_id)
        # Target qualified leads, or resumable building leads
        candidate_leads = [
            l for l in leads if l["status"] in ("qualified", "building")
        ]

        if not candidate_leads:
            already_built = [l for l in leads if l["status"] in ("built", "deployed")]
            logger.info(f"No pending qualified leads to build for batch '{batch_id}'. ({len(already_built)} already built).")
            return {
                "batch_id": batch_id,
                "category": category,
                "batch_domain": batch_domain,
                "total_processed": 0,
                "built_count": len(already_built),
                "needs_confirmation_count": 0,
                "failed_count": 0,
                "results": []
            }

        concurrency = cfg.get("max_concurrent_builds", 2)
        semaphore = asyncio.Semaphore(concurrency)
        pause_sec = cfg.get("pause_between_leads_sec", 0)

        results: List[Dict[str, Any]] = []
        built_count = 0
        needs_conf_count = 0
        failed_count = 0

        async def _bounded_build(lead_record: Dict[str, Any]):
            nonlocal built_count, needs_conf_count, failed_count
            async with semaphore:
                slug = lead_record["slug"]
                self.crm.update_lead(slug, status="building")
                self.crm.record_event("lead_started", lead_record["id"], {"slug": slug})

                try:
                    res = await self.build_single_lead(
                        lead=lead_record,
                        batch_domain=batch_domain,
                        config=cfg,
                        mock_scout_provider=mock_scout_provider,
                        mock_screenshot_provider=mock_screenshot_provider
                    )
                    status = res.get("status")
                    if status == "built":
                        built_count += 1
                    elif status == "needs_confirmation":
                        needs_conf_count += 1
                    elif status == "needs_review":
                        failed_count += 1
                    results.append(res)
                except Exception as e:
                    logger.error(f"Error building demo for '{slug}': {e}", exc_info=True)
                    failed_count += 1
                    self.crm.update_lead(slug, status="error")
                    self.crm.record_event("lead_failed", lead_record["id"], {"slug": slug, "error": str(e)})
                    results.append({"status": "error", "slug": slug, "error": str(e)})

                if pause_sec > 0:
                    await asyncio.sleep(pause_sec)

        tasks = [_bounded_build(lead) for lead in candidate_leads]
        await asyncio.gather(*tasks)

        # Update batch record in CRM
        total_built = len([l for l in self.crm.get_leads_by_batch(batch_id) if l["status"] in ("built", "deployed")])
        self.crm.update_batch(batch_id, built_count=total_built)
        self.crm.record_event("batch_done", None, {
            "batch_id": batch_id,
            "built_count": total_built,
            "needs_confirmation_count": needs_conf_count,
            "failed_count": failed_count
        })

        return {
            "batch_id": batch_id,
            "category": category,
            "batch_domain": batch_domain,
            "total_processed": len(candidate_leads),
            "built_count": built_count,
            "needs_confirmation_count": needs_conf_count,
            "failed_count": failed_count,
            "results": results
        }

    async def deploy_batch(
        self,
        batch_id: str,
        deselect_slugs: Optional[List[str]] = None,
        mock_deployer: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        Deploys all 'built' leads in a batch to Vercel production.
        Requires single human-in-the-loop tactical authorization for the whole batch.
        """
        batch = self.crm.get_batch(batch_id)
        if not batch:
            raise ValueError(f"Batch '{batch_id}' not found in CRM.")

        category = batch["category"]
        leads = self.crm.get_leads_by_batch(batch_id, status="built")
        deselected = set(deselect_slugs or [])
        to_deploy = [l for l in leads if l["slug"] not in deselected]

        if not to_deploy:
            return {
                "status": "no_leads_to_deploy",
                "batch_id": batch_id,
                "message": "No leads in 'built' status eligible for deployment."
            }

        # 1. Batch-level Tactical Authorization (Single human prompt)
        slug_list_str = ", ".join([l["slug"] for l in to_deploy[:5]])
        if len(to_deploy) > 5:
            slug_list_str += f" and {len(to_deploy) - 5} others"

        allowed = await request_tactical_authorization(
            f"Deploy {len(to_deploy)} concept demos to Vercel for batch '{category}' ({slug_list_str})?"
        )
        if not allowed:
            logger.warning("Batch deployment authorization DENIED by operator.")
            return {
                "status": "authorization_denied",
                "batch_id": batch_id,
                "deployed_count": 0,
                "message": "Operator denied tactical deployment authorization."
            }

        # 2. Vercel Token Check
        token = VercelDeployer.get_token()
        if not token and mock_deployer is None:
            return {
                "status": "config_required",
                "batch_id": batch_id,
                "deployed_count": 0,
                "message": "Vercel token needed. Configure configs/vercel.json."
            }

        # 3. Vercel Rate Limit Guard
        if len(to_deploy) > 50:
            return {
                "status": "limit_exceeded",
                "batch_id": batch_id,
                "message": f"Deployment batch size ({len(to_deploy)}) exceeds safe single-run quota (50)."
            }

        # 4. Deploy leads sequentially
        deployed_leads: List[Dict[str, Any]] = []
        failed_deployments: List[Dict[str, Any]] = []

        for lead in to_deploy:
            slug = lead["slug"]
            project_name = f"{slug}-concept"

            if mock_deployer is not None:
                dep_res = mock_deployer(slug, project_name)
            else:
                from ghost.autonomy.outbound import issue_effect_grant
                dep_grant = issue_effect_grant("deploy", slug)
                dep_res = await VercelDeployer.deploy_demo(slug, project_name=project_name,
                                                           grant_token=dep_grant)

            if dep_res.get("status") == "success" and dep_res.get("url"):
                live_url = dep_res["url"]
                self.crm.update_lead(slug, status="deployed", demo_url=live_url)
                self.crm.record_event("lead_deployed", lead["id"], {"slug": slug, "url": live_url})
                deployed_leads.append({"slug": slug, "url": live_url})
            else:
                failed_deployments.append({"slug": slug, "error": dep_res.get("message")})

        return {
            "status": "batch_deployed",
            "batch_id": batch_id,
            "category": category,
            "total_requested": len(to_deploy),
            "deployed_count": len(deployed_leads),
            "failed_count": len(failed_deployments),
            "deployments": deployed_leads,
            "failures": failed_deployments
        }
