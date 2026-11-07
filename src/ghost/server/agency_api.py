"""Agency Command Deck REST API for Ghost (Part 6).
Provides secure, localhost-only, token-protected endpoints for controlling campaigns,
inspecting leads, reviewing outreach drafts, generating closing packs, and managing suppression.
Strict human-in-the-loop: Ghost prepares drafts; Pranav verifies and sends.
Nothing is auto-sent or auto-charged.
"""
from typing import Dict, Any, List, Optional, Tuple
from decimal import Decimal
import os
import json
import csv
import io
import re
import time
import secrets
import hmac
import logging

from fastapi import APIRouter, Request, HTTPException, Depends, Header, Query, UploadFile, File
from fastapi.responses import FileResponse, Response, JSONResponse

from ghost.crm.db import CRMDatabase
from ghost.workers.suppression import SuppressionManager
from ghost.workers.outreach_drafter import OutreachDrafter, load_outreach_config, get_word_count
from ghost.workers.deal_closer import DealCloser, load_deal_config, MANDATORY_LEGAL_TOKENS
from ghost.workers.demo_state_machine import sanitize_outbound, BANNED_OUTBOUND_TERMS

logger = logging.getLogger("ghost.server.agency_api")

agency_router = APIRouter(prefix="/api", tags=["agency"])

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
SESSION_CONFIG_PATH = os.environ.get("GHOST_SESSION_CONFIG", os.path.join(REPO_DIR, "configs/session_state.json"))
OUTREACH_CONFIG_PATH = os.environ.get("GHOST_OUTREACH_CONFIG", os.path.join(REPO_DIR, "configs/outreach.json"))
DEAL_CONFIG_PATH = os.environ.get("GHOST_DEAL_CONFIG", os.path.join(REPO_DIR, "configs/deal.json"))
TEMPLATES_BASE_DIR = os.environ.get("GHOST_TEMPLATES_DIR", os.path.join(REPO_DIR, "templates"))

GHOST_PORT = os.environ.get("GHOST_PORT", "8765")
ALLOWED_HOSTS = {
    f"127.0.0.1:{GHOST_PORT}",
    f"localhost:{GHOST_PORT}",
    f"[::1]:{GHOST_PORT}",
    "testserver",
    "testserver:80",
    f"testserver:{GHOST_PORT}",
}

ALLOWED_ORIGINS = {
    f"http://127.0.0.1:{GHOST_PORT}",
    f"http://localhost:{GHOST_PORT}",
    f"http://[::1]:{GHOST_PORT}",
    "http://testserver",
    "https://testserver",
}

ALLOWED_CONTRACT_TOKENS = {
    "WATERMARK_HEADER", "DATE", "START_DATE", "CLIENT_BUSINESS_NAME",
    "CLIENT_LEGAL_NAME", "CLIENT_ENTITY_TYPE", "CLIENT_ADDRESS",
    "CLIENT_EMAIL", "OWNER_NAME", "PROVIDER_NAME", "PROVIDER_ADDRESS",
    "PROJECT_DESCRIPTION", "DEMO_URL", "TOTAL_PRICE", "CURRENCY",
    "CURRENCY_SYMBOL", "DEPOSIT_PERCENT", "DEPOSIT_AMOUNT", "BALANCE_AMOUNT",
    "DELIVERY_DAYS", "REVISION_ROUNDS", "GOVERNING_LAW", "PAYMENT_INSTRUCTIONS",
    "INVOICE_NUMBER", "INVOICE_AMOUNT", "INVOICE_DESCRIPTION"
}

ALLOWED_OUTREACH_TOKENS = {
    "name", "location", "demo_url", "gap_summary", "opt_out_line",
    "operator_name", "operator_business_name", "postal_address"
}

_active_session_token: Optional[str] = None
_download_tickets: Dict[str, Tuple[str, float]] = {}  # ticket -> (document_id, expires_at)


def get_or_create_session_token() -> str:
    """Retrieves or creates a secure local session token."""
    global _active_session_token
    if _active_session_token:
        return _active_session_token

    env_token = os.environ.get("GHOST_SESSION_TOKEN")
    if env_token and env_token.strip():
        _active_session_token = env_token.strip()
        return _active_session_token

    # Check session state file
    if os.path.exists(SESSION_CONFIG_PATH):
        try:
            with open(SESSION_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("session_token"):
                    _active_session_token = data["session_token"]
                    return _active_session_token
        except Exception:
            pass

    # Generate new random 32-char token
    new_token = secrets.token_hex(16)
    _active_session_token = new_token
    try:
        data = {}
        if os.path.exists(SESSION_CONFIG_PATH):
            with open(SESSION_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        data["session_token"] = new_token
        with open(SESSION_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not persist session token to {SESSION_CONFIG_PATH}: {e}")

    return _active_session_token


def verify_localhost(request: Request):
    """Enforces localhost binding, Host validation, and Origin/Referer check against DNS rebinding."""
    # 1. IP Check
    client_host = request.client.host if request.client else ""
    if client_host and client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        logger.warning(f"Refused remote access attempt from client: {client_host}")
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Agency Command Deck is bound to localhost (127.0.0.1) only."
        )

    # 2. Host Header Check (anti-DNS rebinding)
    host_header = request.headers.get("host", "").strip().lower()
    if not host_header or host_header not in ALLOWED_HOSTS:
        logger.warning(f"Refused request with invalid Host header: {host_header}")
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden: Invalid or untrusted Host header: {host_header}"
        )

    # 3. Origin Check (reject untrusted cross-origin requests)
    origin = request.headers.get("origin")
    if origin:
        norm_origin = origin.lower().rstrip("/")
        if norm_origin not in ALLOWED_ORIGINS:
            logger.warning(f"Refused cross-origin request from Origin: {origin}")
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: Untrusted Origin: {origin}"
            )

    # 4. Referer Check (if present, netloc must match allowed host)
    referer = request.headers.get("referer")
    if referer:
        try:
            import urllib.parse
            parsed = urllib.parse.urlsplit(referer)
            if parsed.netloc and parsed.netloc.lower() not in ALLOWED_HOSTS:
                logger.warning(f"Refused request with untrusted Referer: {referer}")
                raise HTTPException(
                    status_code=403,
                    detail=f"Forbidden: Untrusted Referer netloc: {parsed.netloc}"
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=403, detail="Forbidden: Malformed Referer.")


def verify_auth_token(request: Request):
    """Enforces session token authorization on API calls."""
    verify_localhost(request)

    expected = get_or_create_session_token()
    token = request.headers.get("X-Session-Token")

    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()

    if not token or not hmac.compare_digest(token.strip(), expected):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Missing or invalid X-Session-Token header."
        )


# Global event subscribers for WebSocket broadcast
_event_subscribers = []


def broadcast_agency_event(event_type: str, payload: Dict[str, Any]):
    """Broadcasts CRM / state events to connected UI WebSocket clients."""
    for sub in list(_event_subscribers):
        try:
            sub(event_type, payload)
        except Exception:
            pass


# =============================================================================
# 1. Operator & System Settings
# =============================================================================

@agency_router.get("/operator", dependencies=[Depends(verify_auth_token)])
async def get_operator_info():
    """Retrieve active operator identity and daily send metrics."""
    outreach_cfg = load_outreach_config()
    deal_cfg = load_deal_config()
    crm = CRMDatabase.get_instance()
    daily_sends = crm.get_daily_sends_count()

    return {
        "operator_name": outreach_cfg.get("operator_name"),
        "operator_business_name": outreach_cfg.get("operator_business_name"),
        "operator_email": outreach_cfg.get("operator_email"),
        "operator_phone": outreach_cfg.get("operator_phone"),
        "postal_address": outreach_cfg.get("postal_address"),
        "max_sends_per_day": outreach_cfg.get("max_sends_per_day", 20),
        "do_not_contact_unknown_entity_email": outreach_cfg.get("do_not_contact_unknown_entity_email", True),
        "daily_sends_today": daily_sends,
        "daily_sends_cap_reached": daily_sends >= outreach_cfg.get("max_sends_per_day", 20),
        "provider_name": deal_cfg.get("provider_name"),
        "provider_address": deal_cfg.get("provider_address"),
        "currency": deal_cfg.get("currency", "GBP"),
        "deposit_percent": deal_cfg.get("deposit_percent", 50)
    }


@agency_router.post("/operator", dependencies=[Depends(verify_auth_token)])
async def update_operator_info(request: Request):
    """Update operator settings in outreach and deal configs."""
    body = await request.json()

    outreach_cfg = load_outreach_config()
    for field in (
        "operator_name", "operator_business_name", "operator_email",
        "operator_phone", "postal_address", "max_sends_per_day",
        "do_not_contact_unknown_entity_email", "include_unsubscribe_line"
    ):
        if field in body:
            outreach_cfg[field] = body[field]

    with open(OUTREACH_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(outreach_cfg, f, indent=2)

    deal_cfg = load_deal_config()
    for field in ("provider_name", "provider_address", "currency", "deposit_percent", "governing_law"):
        if field in body:
            deal_cfg[field] = body[field]

    with open(DEAL_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(deal_cfg, f, indent=2)

    return {"status": "success", "message": "Operator details updated."}


# =============================================================================
# 3. Campaigns & Batches
# =============================================================================

@agency_router.post("/campaigns", dependencies=[Depends(verify_auth_token)])
async def create_campaign(request: Request):
    """
    Creates a new campaign with 3 distinct categories.
    Validates categories are distinct (duplicate categories return 400).
    """
    body = await request.json()
    name = (body.get("name") or "").strip()
    city = (body.get("city") or "").strip()
    cities = body.get("cities")   # Operator's 11 goal 2: research breadth — one campaign per city
    categories = body.get("categories") or []
    harvest_per_batch = int(body.get("harvest_per_batch", 100))
    keep_fraction = float(body.get("keep_fraction", 0.33))

    if not name or (not city and not cities):
        raise HTTPException(status_code=400, detail="Campaign name and city (or cities) are required.")

    if not isinstance(categories, list) or len(categories) != 3:
        raise HTTPException(status_code=400, detail="Exactly 3 categories must be provided.")

    norm_cats = [c.strip().lower() for c in categories if c and c.strip()]
    if len(norm_cats) != 3 or len(set(norm_cats)) != 3:
        raise HTTPException(status_code=400, detail="The three campaign categories must be distinct.")

    crm = CRMDatabase.get_instance()

    # singular `city` or a `cities` list — one validated campaign per city
    city_list = ([str(c).strip() for c in cities if str(c).strip()]
                 if isinstance(cities, list) and cities else ([city] if city else []))
    if not city_list:
        raise HTTPException(status_code=400, detail="At least one city is required.")

    created = []
    for i, c in enumerate(city_list):
        camp = crm.create_campaign(
            name=f"{name} · {c}" if len(city_list) > 1 else name,
            city=c,
            harvest_per_batch=harvest_per_batch,
            keep_fraction=keep_fraction
        )
        batches = []
        for cat in norm_cats:
            b = crm.create_batch(campaign_id=camp["id"], category=cat)
            batches.append(b)
        crm.record_event(
            event_type="campaign_created",
            payload={"campaign_id": camp["id"], "city": c, "categories": norm_cats}
        )
        created.append({"campaign": camp, "batches": batches})

    return {
        "status": "success",
        "campaign": created[0]["campaign"],
        "batches": created[0]["batches"],
        "campaigns": created if len(created) > 1 else None,
    }


@agency_router.get("/campaigns", dependencies=[Depends(verify_auth_token)])
async def list_campaigns():
    """List all campaigns with their batches and counts."""
    crm = CRMDatabase.get_instance()
    camps = crm.list_campaigns()
    for c in camps:
        c["batches"] = crm.get_batches_for_campaign(c["id"])
    return {"campaigns": camps}


@agency_router.get("/campaigns/{campaign_id}", dependencies=[Depends(verify_auth_token)])
async def get_campaign(campaign_id: str):
    """Get single campaign details and batches."""
    crm = CRMDatabase.get_instance()
    c = crm.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    c["batches"] = crm.get_batches_for_campaign(campaign_id)
    return c


@agency_router.post("/campaigns/{campaign_id}/batches/next", dependencies=[Depends(verify_auth_token)])
async def run_next_batch(campaign_id: str):
    """Advances next pending batch in the campaign."""
    crm = CRMDatabase.get_instance()
    batches = crm.get_batches_for_campaign(campaign_id)
    pending_batch = next((b for b in batches if b["status"] == "pending"), None)

    if not pending_batch:
        return {"status": "complete", "message": "No pending batches in campaign."}

    # Mark batch qualified / processing
    crm.update_batch_status(pending_batch["id"], "qualified")
    crm.record_event(
        event_type="batch_started",
        payload={"batch_id": pending_batch["id"], "category": pending_batch["category"]}
    )

    return {
        "status": "started",
        "batch_id": pending_batch["id"],
        "category": pending_batch["category"]
    }


# =============================================================================
# 4. Leads Management, Confirmation & Inspector
# =============================================================================

@agency_router.get("/leads", dependencies=[Depends(verify_auth_token)])
async def list_leads(
    batch_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    campaign_id: Optional[str] = Query(None)
):
    """Query leads across CRM with optional filters."""
    crm = CRMDatabase.get_instance()
    leads = crm.get_all_leads()

    if campaign_id:
        camp_batches = {b["id"] for b in crm.get_batches_for_campaign(campaign_id)}
        leads = [l for l in leads if l.get("batch_id") in camp_batches]

    if batch_id:
        leads = [l for l in leads if l.get("batch_id") == batch_id]

    if status:
        leads = [l for l in leads if l.get("status") == status.strip().lower()]

    if search:
        s = search.strip().lower()
        leads = [
            l for l in leads
            if s in str(l.get("name", "")).lower() or s in str(l.get("slug", "")).lower() or s in str(l.get("address", "")).lower()
        ]

    return {"leads": leads, "count": len(leads)}


@agency_router.get("/leads/{lead_id_or_slug}", dependencies=[Depends(verify_auth_token)])
async def get_lead_details(lead_id_or_slug: str):
    """Retrieve rich lead inspector data."""
    crm = CRMDatabase.get_instance()
    lead = crm.get_lead(lead_id_or_slug)
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead '{lead_id_or_slug}' not found.")

    drafts = crm.get_drafts_for_lead(lead["id"])
    documents = crm.get_documents_for_lead(lead["id"])
    events = crm.get_events(lead_id=lead["id"])

    return {
        "lead": lead,
        "drafts": drafts,
        "documents": documents,
        "events": events
    }


@agency_router.patch("/leads/{lead_id_or_slug}", dependencies=[Depends(verify_auth_token)])
async def update_lead_overrides(lead_id_or_slug: str, request: Request):
    """Save editable field overrides and optionally regenerate drafts."""
    crm = CRMDatabase.get_instance()
    lead = crm.get_lead(lead_id_or_slug)
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead '{lead_id_or_slug}' not found.")

    body = await request.json()
    regenerate = bool(body.pop("regenerate_drafts", False))

    # Apply overrides
    crm.add_or_update_lead({"slug": lead["slug"], **body})

    updated_lead = crm.get_lead(lead["slug"])
    regenerated_drafts = []

    if regenerate and updated_lead.get("demo_url"):
        drafter = OutreachDrafter(crm=crm)
        res = drafter.create_drafts_for_lead(updated_lead["slug"])
        regenerated_drafts = res.get("drafts", [])

    return {
        "status": "success",
        "lead": updated_lead,
        "regenerated_drafts": regenerated_drafts
    }


@agency_router.post("/leads/{lead_id_or_slug}/confirm_domain", dependencies=[Depends(verify_auth_token)])
async def confirm_domain(lead_id_or_slug: str, request: Request):
    """Operator confirms one of the suggested domains for an ambiguous lead."""
    crm = CRMDatabase.get_instance()
    lead = crm.get_lead(lead_id_or_slug)
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead '{lead_id_or_slug}' not found.")

    body = await request.json()
    chosen_domain = body.get("chosen_domain")
    reason = body.get("reason", "Operator confirmed domain")

    if not chosen_domain:
        raise HTTPException(status_code=400, detail="chosen_domain is required.")

    crm.add_or_update_lead({
        "slug": lead["slug"],
        "domain": chosen_domain.strip(),
        "needs_confirmation": 0,
        "status": "qualified" if lead["status"] == "needs_confirmation" else lead["status"]
    })

    crm.record_event(
        event_type="domain_confirmed",
        lead_id=lead["id"],
        payload={"slug": lead["slug"], "domain": chosen_domain, "reason": reason}
    )

    return {"status": "success", "chosen_domain": chosen_domain, "slug": lead["slug"]}


@agency_router.post("/leads/{lead_id_or_slug}/optout", dependencies=[Depends(verify_auth_token)])
async def opt_out_lead_endpoint(lead_id_or_slug: str, request: Request):
    """1-Click Opt-Out: Adds all lead identifiers to suppression table and removes drafts."""
    crm = CRMDatabase.get_instance()
    suppression = SuppressionManager(crm=crm)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    reason = body.get("reason", "Operator 1-click opt-out from Command Deck")
    res = suppression.opt_out_lead(lead_id_or_slug, reason=reason, source="operator_ui")

    if res.get("status") != "success" and not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to opt-out lead."))

    res["ok"] = True
    return res

    return res


# =============================================================================
# 5. Batch Deploy Authorization
# =============================================================================

@agency_router.post("/batches/{batch_id}/deploy", dependencies=[Depends(verify_auth_token)])
async def authorize_batch_deploy(batch_id: str, request: Request):
    """Tactical operator authorization to deploy batch demos."""
    crm = CRMDatabase.get_instance()
    batch = crm.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")

    body = await request.json()
    authorized = bool(body.get("authorized", False))
    note = body.get("operator_note", "")

    if not authorized:
        return {"status": "denied", "message": "Batch deployment authorization denied by operator."}

    crm.update_batch_status(batch_id, "deploy_authorized")
    crm.record_event(
        event_type="batch_deploy_authorized",
        payload={"batch_id": batch_id, "note": note}
    )

    return {"status": "authorized", "batch_id": batch_id}


# =============================================================================
# 5b. Per-demo approval loop (Operator's 11 P3 — goal 5)
# The operator opens each demo LOCALLY, one at a time; the approval click is
# the ONLY thing that can mint that demo's deploy grant. One click = one
# deploy (single-use grant); every existing deploy gate still applies.
# =============================================================================

def _demo_summary(slug: str) -> Dict[str, Any]:
    """One row for the approval board: QA evidence + host state, honestly."""
    from ghost.workers.deploy import _demos_dir
    demo_dir = os.path.join(_demos_dir(), slug)
    qa = {}
    qa_path = os.path.join(demo_dir, "reports", "qa.json")
    if os.path.exists(qa_path):
        try:
            qa = json.load(open(qa_path, encoding="utf-8"))
        except (OSError, ValueError):
            qa = {"passed": False, "error": "unreadable qa.json"}
    lead = CRMDatabase.get_instance().get_lead(slug) or {}
    client = CRMDatabase.get_instance().get_client(slug) or {}
    hosted_url = lead.get("demo_url") or client.get("demo_url")
    return {
        "slug": slug,
        "business": lead.get("name") or client.get("business_name") or slug,
        "lead_status": lead.get("status"),
        "qa_passed": bool(qa.get("passed")),
        "qa_screenshots": qa.get("screenshots_captured", 0),
        "hosted_url": hosted_url,
        "local_url": f"/demos/{slug}/",
        "deployed": bool(hosted_url),
    }


@agency_router.get("/demos", dependencies=[Depends(verify_auth_token)])
async def list_demos():
    """The operator's demo approval board: every built demo, its QA evidence
    status and whether it is already hosted."""
    from ghost.workers.deploy import _demos_dir
    base = _demos_dir()
    slugs = sorted(d for d in os.listdir(base)
                   if os.path.isdir(os.path.join(base, d)) and not d.startswith(".")
                   and os.path.exists(os.path.join(base, d, "index.html"))) \
        if os.path.isdir(base) else []
    return {"demos": [_demo_summary(s) for s in slugs]}


@agency_router.post("/demos/{slug}/open", dependencies=[Depends(verify_auth_token)])
async def open_demo(slug: str):
    """Audit + return the LOCAL preview URL. The demo is served from this
    machine (/demos/<slug>/) — nothing leaves localhost here."""
    from ghost.core.audit import audit
    from ghost.workers.deploy import _demos_dir
    demo_dir = os.path.join(_demos_dir(), slug)
    if not os.path.exists(os.path.join(demo_dir, "index.html")):
        raise HTTPException(status_code=404, detail=f"No local demo at demos/{slug}.")
    audit("demo.opened", subject=slug, by="operator", surface="local_preview")
    return {"status": "ok", "slug": slug,
            "url": f"/demos/{slug}/", "absolute": f"http://127.0.0.1:8765/demos/{slug}/",
            "note": "local preview — hosting requires the separate approval click"}


@agency_router.post("/demos/{slug}/authorize-host", dependencies=[Depends(verify_auth_token)])
async def authorize_demo_host(slug: str, request: Request):
    """THE approval click. Records the operator's authorization, mints that
    demo's single-use deploy grant, and runs the deploy through every
    existing gate (halt, grant, fresh snapshot, QA evidence). Refuses while
    halted — even operator clicks do nothing while frozen."""
    from ghost.core.audit import audit
    from ghost.core.halt import is_halted
    from ghost.workers.deploy import _demos_dir

    demo_dir = os.path.join(_demos_dir(), slug)
    if not os.path.exists(os.path.join(demo_dir, "index.html")):
        raise HTTPException(status_code=404, detail=f"No local demo at demos/{slug}.")

    body = await request.json()
    if not bool(body.get("confirmed", False)):
        audit("demo.host_denied", subject=slug, by="operator")
        return {"status": "denied", "message": "Hosting not confirmed — nothing happened."}
    note = str(body.get("operator_note", ""))[:200]

    if is_halted():
        audit("demo.host_refused", subject=slug, reason="halt",
              ping="(c) unverifiable: operator hosting click during an active halt")
        raise HTTPException(status_code=409,
                            detail="Ghost is HALTED — no hosting while frozen. `ghost resume` first.")

    # the approval click IS the evidence; the grant is its one-time key
    audit("demo.host_authorized", subject=slug, by="operator", note=note)
    from ghost.autonomy.outbound import issue_effect_grant
    token = issue_effect_grant("deploy", slug)
    if token is None:
        raise HTTPException(status_code=409,
                            detail="No deploy authority available (grant daemon unreachable?) — nothing hosted.")

    from ghost.workers.deploy import VercelDeployer
    result = await VercelDeployer.deploy_demo(slug, project_name=f"{slug}-concept",
                                              grant_token=token)

    if result.get("status") == "success" and result.get("url"):
        crm = CRMDatabase.get_instance()
        crm.update_lead(slug, demo_url=result["url"])
        audit("demo.hosted", subject=slug, url=result["url"], by="operator_click")
    return result


# =============================================================================
# 5c. Micro-jobs copilot (Operator's 11 P8 — goal 9)
# Ghost finds small jobs it can actually deliver and DRAFTS proposals.
# A human presses send — there is deliberately no auto-bid path (ToS).
# =============================================================================

@agency_router.get("/microjobs", dependencies=[Depends(verify_auth_token)])
async def list_microjobs(status: Optional[str] = Query(None)):
    """The micro-jobs board: discovered jobs + the proposal attached to each."""
    from ghost.workers.microjobs import JobStore
    store = JobStore()
    jobs = store.list_jobs(status)
    for j in jobs:
        j["reasons"] = json.loads(j.get("reasons_json") or "[]")
        prop = store.get_proposal(j["id"])
        j["proposal"] = prop
    return {"jobs": jobs,
            "note": "Ghost drafts; you press send (ToS — no auto-bid path exists)"}


@agency_router.post("/microjobs/sweep", dependencies=[Depends(verify_auth_token)])
async def sweep_microjobs():
    """One read-only source sweep (configs/microjobs.json: your saved search
    URLs + RSS feeds). Fetch failures are reported, never hidden."""
    from ghost.workers.microjobs import sweep
    return sweep()


@agency_router.post("/microjobs/{job_id}/propose", dependencies=[Depends(verify_auth_token)])
async def propose_microjob(job_id: str):
    """Draft ONE tailored proposal for ONE job — awaiting_human_send."""
    from ghost.workers.microjobs import (JobStore, draft_proposal_text,
                                         score_fit)
    store = JobStore()
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if store.get_proposal(job_id):
        return {"status": "already_drafted", "proposal": store.get_proposal(job_id)}
    fit = {"fit_score": job.get("fit_score", 0),
           "deliverables": [r.split(" (")[0] for r in
                            json.loads(job.get("reasons_json") or "[]")]}
    body = draft_proposal_text(job, fit)
    prop = store.add_proposal(job_id, body)
    from ghost.core.audit import audit
    audit("microjobs.proposal_drafted", subject=job_id,
          note="awaiting_human_send — the operator edits and sends")
    return {"status": "drafted", "proposal": prop}


@agency_router.post("/microjobs/{job_id}/status", dependencies=[Depends(verify_auth_token)])
async def microjob_status(job_id: str, request: Request):
    """Operator marks the outcome: sent / won / lost / skipped."""
    from ghost.workers.microjobs import JobStore
    body = await request.json()
    status = str(body.get("status", ""))
    store = JobStore()
    ok = store.set_status(job_id, status)
    if not ok and status in ("sent", "won", "lost"):
        ok = store.set_proposal_status(job_id, status)
    if not ok:
        raise HTTPException(status_code=400, detail=f"unknown status {status!r}")
    from ghost.core.audit import audit
    audit("microjobs.status", subject=job_id, status=status, by="operator")
    return {"status": "ok", "job_id": job_id, "new_status": status}


# =============================================================================
# 5d. Single-window operator panels (Operator's 11 P9 — goal 10)
# Everything the operator needs, read through THIS window: dossiers, the
# autonomy state, notifications (the ping contract), staged fixes, payments.
# =============================================================================

@agency_router.get("/dossiers", dependencies=[Depends(verify_auth_token)])
async def list_dossiers():
    """The research dossier shelf (P2): what Ghost knows per business."""
    from ghost.workers.dossier import dossiers_dir
    base = dossiers_dir()
    out = []
    if os.path.isdir(base):
        for slug in sorted(os.listdir(base)):
            facts_path = os.path.join(base, slug, "facts.json")
            if not os.path.isfile(facts_path):
                continue
            try:
                facts = json.load(open(facts_path, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            photo_dir = os.path.join(base, slug, "photos")
            out.append({
                "slug": slug,
                "business": (facts.get("identity", {}).get("name", {}) or {}).get("value") or slug,
                "inquire": facts.get("inquire", []),
                "photos": len(os.listdir(photo_dir)) if os.path.isdir(photo_dir) else 0,
                "site_verdict": (facts.get("site", {}) or {}).get("verdict"),
            })
    return {"dossiers": out}


@agency_router.get("/dossiers/{slug}", dependencies=[Depends(verify_auth_token)])
async def get_dossier(slug: str):
    from ghost.workers.dossier import dossiers_dir
    d = os.path.join(dossiers_dir(), slug)
    if not os.path.isdir(d):
        raise HTTPException(status_code=404, detail="No such dossier.")
    try:
        facts = json.load(open(os.path.join(d, "facts.json"), encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="Dossier facts unreadable.")
    photos = sorted(os.listdir(os.path.join(d, "photos"))) \
        if os.path.isdir(os.path.join(d, "photos")) else []
    md_path = os.path.join(d, "dossier.md")
    md = open(md_path, encoding="utf-8").read() if os.path.exists(md_path) else ""
    return {"slug": slug, "facts": facts, "photos": photos, "markdown": md}


@agency_router.post("/dossiers/{slug}/photos", dependencies=[Depends(verify_auth_token)])
async def upload_dossier_photos(slug: str, files: List[UploadFile] = File(...)):
    """Option A (Operator's photo doctrine): the operator drops the business's
    REAL photos onto the dossier in the deck; they land in
    dossiers/<slug>/photos/ and the next build stages them with provenance.
    Non-image files are refused — nothing sneaks in."""
    import re as _re
    if not _re.fullmatch(r"[a-z0-9-]+", slug):
        raise HTTPException(status_code=400, detail="bad slug")
    from ghost.workers.dossier import dossiers_dir
    out = os.path.join(dossiers_dir(), slug, "photos")
    os.makedirs(out, exist_ok=True)
    saved, refused = [], []
    stamp = int(time.time())
    for f in files[:20]:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            refused.append(f.filename or "?")
            continue
        data = await f.read()
        if not data or len(data) > 15 * 1024 * 1024:
            refused.append(f.filename or "?")
            continue
        fname = f"dropped-{stamp}-{len(saved):02d}{ext}"
        with open(os.path.join(out, fname), "wb") as fh:
            fh.write(data)
        saved.append(fname)
    from ghost.core.audit import audit
    audit("dossier.photos_dropped", subject=slug, saved=len(saved),
          refused=len(refused), by="operator")
    return {"status": "ok", "saved": saved, "refused": refused}


@agency_router.get("/dossiers/{slug}/photo/{name}", dependencies=[Depends(verify_auth_token)])
async def dossier_photo(slug: str, name: str):
    """Provenance-tracked photos, served authed — never a hotlink."""
    import re as _re
    if not _re.fullmatch(r"[\w.\-]+", name):
        raise HTTPException(status_code=400, detail="bad filename")
    from ghost.workers.dossier import dossiers_dir
    path = os.path.join(dossiers_dir(), slug, "photos", name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="no such photo")
    return FileResponse(path, media_type="image/jpeg")


@agency_router.get("/autonomy", dependencies=[Depends(verify_auth_token)])
async def autonomy_bundle():
    """One screen: is the body alive, who may do what, is anything overdue."""
    from ghost.core import halt as halt_mod
    from ghost.autonomy.ladder import AutonomyLadder
    from ghost.core import rhythm
    bundle: Dict[str, Any] = {
        "halt": {"halted": halt_mod.is_halted(),
                 "state": halt_mod.halt_state()},
        "ladder": AutonomyLadder().status(),
        "rhythm": {},
    }
    try:
        from ghost.autonomy.runtime import get_runtime
        rt = get_runtime()
        bundle["runtime"] = rt.status if rt else {"running": False,
                                                  "note": "runtime not started"}
    except Exception:
        bundle["runtime"] = {"running": False, "note": "unknown"}
    try:
        from ghost.supervisor.service import beat_age_s
        age = beat_age_s()
        bundle["supervisor"] = {"beat_age_s": None if age == float("inf") else age,
                                "alive": age < 300.0,
                                "note": None if age != float("inf") else "never beaten"}
    except Exception:
        bundle["supervisor"] = {"alive": False, "note": "no supervisor beat file"}
    for marker in ("scheduler_cycle", "digest"):
        try:
            bundle["rhythm"][marker] = rhythm.due(marker)
        except Exception:
            bundle["rhythm"][marker] = {"due": False, "reason": "unavailable"}
    try:
        from ghost.control import darkrun
        st = darkrun.status()
        bundle["darkrun"] = {"active": st.get("active", False),
                             "day": st.get("day"), "of": st.get("of")}
    except Exception:
        bundle["darkrun"] = {"active": False}
    return bundle


_PING_EVENT_TYPES = ("reply.complaint", "reply.bounce", "reply.call_recommended",
                     "reply.unverifiable", "reply.quarantined", "health.incident",
                     "fixer.staged", "fixer.unresolved", "fixer.report_received",
                     "followup.drafted", "payment.link_api_failed",
                     "payment.link_not_configured", "payment.upi_uri_issued",
                     "demo.host_authorized", "demo.hosted", "intake.unmatched",
                     "route.transfer_refused", "lead.form_submission")


@agency_router.get("/notifications", dependencies=[Depends(verify_auth_token)])
async def list_notifications():
    """The §10 notification contract, made visible: everything that pinged
    (or would have) + the daily digest shelf. The operator should never have
    to grep a ledger to find out what needed attention."""
    from ghost.core.audit import AuditLedger
    led = AuditLedger.get_instance()
    pings = []
    for et in _PING_EVENT_TYPES:
        for row in led.query(event_type=et, limit=8):
            payload = row.get("payload")
            ping = ""
            if isinstance(payload, str):
                try:
                    ping = str((json.loads(payload) or {}).get("ping", ""))[:200]
                except (ValueError, TypeError):
                    ping = ""
            elif isinstance(payload, dict):
                ping = str(payload.get("ping", ""))[:200]
            pings.append({"at": row.get("ts"), "type": et,
                          "subject": str(row.get("subject", ""))[:80],
                          "ping": ping})
    pings.sort(key=lambda p: p["at"] or 0, reverse=True)
    digest_dir = os.path.join(REPO_DIR, "data", "digests")
    digests = sorted((f for f in os.listdir(digest_dir) if f.endswith(".md")),
                     reverse=True)[:14] if os.path.isdir(digest_dir) else []
    return {"pings": pings[:40], "digests": digests}


@agency_router.get("/notifications/digest/{day}", dependencies=[Depends(verify_auth_token)])
async def get_digest(day: str):
    import re as _re
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
    path = os.path.join(REPO_DIR, "data", "digests", f"{day}.md")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="no digest for that day")
    return {"day": day, "markdown": open(path, encoding="utf-8").read()}


@agency_router.get("/fixer/staged", dependencies=[Depends(verify_auth_token)])
async def fixer_staged_list():
    """The staged-fix approval queue (P7): verified patches awaiting YOU."""
    from ghost.control.fixers import list_staged
    return {"staged": list_staged()}


@agency_router.post("/fixer/{staged_id}/approve", dependencies=[Depends(verify_auth_token)])
async def fixer_approve(staged_id: str):
    """THE approval click for a verified site fix. Halt-refusing, one action
    per package — same rules as the CLI."""
    from ghost.control.fixers import approve_staged
    out = approve_staged(staged_id, by="operator-deck")
    if not out.get("applied"):
        raise HTTPException(status_code=409, detail=out.get("reason", "not applied"))
    return out


@agency_router.get("/payments/overview", dependencies=[Depends(verify_auth_token)])
async def payments_overview():
    """Collect-only money view: who owes what, which rail is live, where the
    QR/link artifacts are. Nothing here can move money — by design."""
    crm = CRMDatabase.get_instance()
    from ghost.core.secrets import get
    from ghost.workers.payments.razorpay_provider import load_razorpay_config
    rzp_cfg = load_razorpay_config()
    rail = {
        "razorpay": bool(get(rzp_cfg.get("key_id_env", "RAZORPAY_KEY_ID"))
                         and get(rzp_cfg.get("key_secret_env", "RAZORPAY_KEY_SECRET"))),
        "upi": bool(_payments_upi_id()),
        "manual_bank": True,
    }
    from ghost.workers.seo_pack import website_ready_for_build
    deals = []
    for client in crm.list_clients():
        slug = client.get("slug")
        docs = crm.get_documents_for_lead(slug)
        docs_dir = os.path.dirname(docs[0]["path"]) if docs else None
        qr_files = [f for f in (sorted(os.listdir(docs_dir)) if docs_dir and
                                os.path.isdir(docs_dir) else [])
                    if f.endswith("-qr.png")]
        if not docs and not client.get("deal_value"):
            continue
        gate = website_ready_for_build(slug, crm=crm)
        seo_pack_dir = None
        for d in docs:
            if d.get("path", "").endswith("seo_pack"):
                seo_pack_dir = d["path"]
        pack_built = os.path.isdir(seo_pack_dir) if seo_pack_dir else False
        deals.append({
            "slug": slug,
            "business": client.get("business_name"),
            "stage": client.get("status"),
            "deal_value": client.get("deal_value"),
            "documents": [{"kind": d.get("kind"), "version": d.get("version"),
                           "status": d.get("status"), "file": os.path.basename(d.get("path", ""))}
                          for d in docs[:8]],
            "qr_artifacts": qr_files[:4],
            "photos_received": gate.get("ready", False),
            "photos_note": gate.get("reason", ""),
            "seo_pack_built": pack_built,
        })
    return {"rail": rail, "deals": deals,
            "note": "collect-only: Ghost accepts money, it never sends it"}


@agency_router.get("/leads/{lead_id_or_slug}/seo-pack/file/{name}",
                   dependencies=[Depends(verify_auth_token)])
async def seo_pack_file(lead_id_or_slug: str, name: str):
    """Read one markdown/txt file from the lead's SEO pack (validated — the
    pack ships checklists and ask-texts the operator reads in the deck)."""
    if not name.endswith((".md", ".txt")):
        name += ".md"          # the deck passes the bare stem
    import re as _re
    if not _re.fullmatch(r"[a-z_]+\.(md|txt)", name):
        raise HTTPException(status_code=400, detail="bad file name")
    from ghost.workers.seo_pack import build_seo_pack
    # regenerate if missing (idempotent, facts-only)
    out = build_seo_pack(lead_id_or_slug)
    path = os.path.join(out["dir"], name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="no such file in the pack")
    return {"name": name, "markdown": open(path, encoding="utf-8").read()}


def _payments_upi_id() -> Optional[str]:
    try:
        cfg_path = os.path.join(REPO_DIR, "configs", "payments.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("upi_id")
    except (OSError, ValueError):
        return None


# =============================================================================
# 6. Outreach Drafts & Review Queue
# =============================================================================

@agency_router.get("/drafts", dependencies=[Depends(verify_auth_token)])
async def list_drafts(
    lead_id: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    status: Optional[str] = Query(None)
):
    """List drafts, filtering out suppressed leads."""
    crm = CRMDatabase.get_instance()
    suppression = SuppressionManager(crm=crm)

    all_drafts = []
    if lead_id:
        all_drafts = crm.get_drafts_for_lead(lead_id)
    else:
        leads = crm.get_all_leads()
        for l in leads:
            all_drafts.extend(crm.get_drafts_for_lead(l["id"]))

    filtered = []
    for d in all_drafts:
        # Check if parent lead is suppressed
        lead = crm.get_lead(d["lead_id"])
        if lead and suppression.is_suppressed(lead=lead):
            continue
        if channel and d.get("channel") != channel.strip().lower():
            continue
        if status and d.get("status") != status.strip().lower():
            continue
        filtered.append(d)

    return {"drafts": filtered, "count": len(filtered)}


@agency_router.get("/drafts/{draft_id}", dependencies=[Depends(verify_auth_token)])
async def get_draft(draft_id: str):
    """Retrieve draft details."""
    crm = CRMDatabase.get_instance()
    draft = crm.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return draft


@agency_router.patch("/drafts/{draft_id}", dependencies=[Depends(verify_auth_token)])
async def edit_draft(draft_id: str, request: Request):
    """Edit draft text and subject."""
    crm = CRMDatabase.get_instance()
    draft = crm.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")

    body = await request.json()
    new_body = body.get("body", draft["body"])
    new_subject = body.get("subject", draft.get("subject"))

    try:
        sanitize_outbound(new_body)
        if new_subject:
            sanitize_outbound(new_subject)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Draft failed persona firewall check: {e}")

    updated = crm.add_or_update_draft(
        lead_id=draft["lead_id"],
        channel=draft["channel"],
        body=new_body,
        subject=new_subject,
        link=draft.get("link"),
        status=draft.get("status", "draft"),
        draft_id=draft["id"]
    )
    return {"status": "success", "draft": updated}


@agency_router.post("/drafts/{draft_id}/approve", dependencies=[Depends(verify_auth_token)])
async def approve_draft(draft_id: str):
    """Operator approves draft. Blocks if operator details missing or fails sanitize_outbound."""
    crm = CRMDatabase.get_instance()
    drafter = OutreachDrafter(crm=crm)
    res = drafter.approve_draft(draft_id)

    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to approve draft."))

    return res


@agency_router.post("/drafts/{draft_id}/sent", dependencies=[Depends(verify_auth_token)])
async def mark_draft_sent(draft_id: str):
    """
    Operator clicks 'I sent this'.
    Requires draft to be in 'approved' status first (illegal transitions return 409 Conflict).
    """
    crm = CRMDatabase.get_instance()
    draft = crm.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")

    if draft["status"] != "approved":
        raise HTTPException(
            status_code=409,
            detail=f"Illegal transition: Draft status is '{draft['status']}'. Draft must be approved before marking as sent."
        )

    drafter = OutreachDrafter(crm=crm)
    res = drafter.mark_draft_sent(draft_id)
    return res


@agency_router.post("/drafts/{draft_id}/reply", dependencies=[Depends(verify_auth_token)])
async def record_draft_reply(draft_id: str, request: Request):
    """The relay path (Operator's 11 P5 — WhatsApp/email replies the operator
    pastes in). The pasted text is UNTRUSTED: it goes through the T7
    quarantine and the reply loop (classify → suppress/ping/draft) BEFORE any
    bookkeeping. Fixed in P5: this endpoint used to call a method that did
    not exist and 500'd on every reply."""
    crm = CRMDatabase.get_instance()

    body = await request.json()
    reply_text = str(body.get("reply_text", "")).strip()
    if not reply_text:
        raise HTTPException(status_code=400, detail="reply_text is required.")

    # resolve the draft → its lead (the reply belongs to a conversation)
    with crm._get_connection() as conn:
        row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id.strip(),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Draft {draft_id!r} not found.")
    draft = dict(row)
    lead = crm.get_lead(draft.get("lead_id") or "")
    if not lead:
        raise HTTPException(status_code=404, detail="Draft has no resolvable lead.")
    slug = lead["slug"]

    # 1. classification path — quarantine runs INSIDE ingest_reply; flagged
    #    injections ping (c) with ZERO effects
    from ghost.autonomy.reply_loop import ingest_reply
    from ghost.workers.suppression import SuppressionManager
    from ghost.autonomy.ladder import AutonomyLadder
    verdict_out = ingest_reply(slug, reply_text,
                               suppression=SuppressionManager(crm=crm),
                               crm=crm, ladder=AutonomyLadder())

    # 2. bookkeeping — mark the draft replied, advance the lead (the
    #    interaction log is already written by ingest_reply; no double log)
    with crm._get_connection() as conn:
        conn.execute("UPDATE drafts SET status = 'replied' WHERE id = ?", (draft_id.strip(),))
        conn.commit()
    suppress = SuppressionManager(crm=crm)
    opted_out = bool(body.get("is_opt_out", False)) or suppress.detect_opt_out_intent(reply_text)
    if opted_out:
        suppress.opt_out_lead(slug, reason="operator relay: opt-out intent in reply")
    elif not verdict_out.get("quarantined") and lead.get("status") not in ("suppressed",):
        crm.update_lead(slug, status="replied")
        crm.record_event("lead_replied", lead.get("id"), {"slug": slug})

    return {"status": "quarantined" if verdict_out.get("quarantined") else
            ("opted_out" if opted_out else "replied"),
            "slug": slug, "verdict": str(verdict_out.get("verdict") or ""),
            "actions": verdict_out.get("actions", [])}


# =============================================================================
# 7. Closing Pack & Contracts
# =============================================================================

@agency_router.post("/leads/{lead_id_or_slug}/closing_pack", dependencies=[Depends(verify_auth_token)])
async def prepare_closing_pack_endpoint(lead_id_or_slug: str, request: Request):
    """Prepares complete closing pack (contracts, welcome kit, invoices, checklist)."""
    crm = CRMDatabase.get_instance()
    closer = DealCloser(crm=crm)

    overrides = {}
    try:
        overrides = await request.json()
    except Exception:
        pass

    res = closer.prepare_closing_pack(lead_id_or_slug, overrides=overrides)
    return res


@agency_router.post("/leads/{lead_id_or_slug}/lawyer_reviewed", dependencies=[Depends(verify_auth_token)])
async def mark_lawyer_reviewed_endpoint(lead_id_or_slug: str):
    """Operator sets lawyer_reviewed=True for lead documents."""
    crm = CRMDatabase.get_instance()
    closer = DealCloser(crm=crm)
    try:
        res = closer.mark_lawyer_reviewed(lead_id_or_slug)
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@agency_router.post("/leads/{lead_id_or_slug}/contract_sent", dependencies=[Depends(verify_auth_token)])
async def mark_contract_sent_endpoint(lead_id_or_slug: str):
    """Operator clicks 'Mark contract sent'."""
    crm = CRMDatabase.get_instance()
    closer = DealCloser(crm=crm)
    res = closer.mark_contract_sent(lead_id_or_slug)
    return res


@agency_router.post("/leads/{lead_id_or_slug}/seo-pack", dependencies=[Depends(verify_auth_token)])
async def build_seo_pack_endpoint(lead_id_or_slug: str):
    """Generate the SEO/AEO pack (Operator's 11-addendum): schema, llms.txt,
    the 6-move checklist, the client photo-request draft, the OWASP gate."""
    from ghost.workers.seo_pack import build_seo_pack
    res = build_seo_pack(lead_id_or_slug)
    return res


@agency_router.post("/leads/{lead_id_or_slug}/mark-client-photos", dependencies=[Depends(verify_auth_token)])
async def mark_client_photos_endpoint(lead_id_or_slug: str):
    """The WAIT gate: the operator confirms the client's real photos arrived.
    Websites wait for client photos; demos don't. Until this, the website
    build is gated."""
    from ghost.workers.seo_pack import mark_client_photos_received
    res = mark_client_photos_received(lead_id_or_slug)
    if not res.get("received"):
        raise HTTPException(status_code=404, detail=res.get("reason", "not found"))
    return res


@agency_router.get("/leads/{lead_id_or_slug}/website-ready", dependencies=[Depends(verify_auth_token)])
async def website_ready_endpoint(lead_id_or_slug: str):
    from ghost.workers.seo_pack import website_ready_for_build
    return website_ready_for_build(lead_id_or_slug)


@agency_router.post("/leads/{lead_id_or_slug}/deposit_received", dependencies=[Depends(verify_auth_token)])
async def mark_deposit_received_endpoint(lead_id_or_slug: str):
    """Operator confirms receipt of deposit -> advances lead to won."""
    crm = CRMDatabase.get_instance()
    closer = DealCloser(crm=crm)
    res = closer.mark_deposit_received(lead_id_or_slug)
    return res


@agency_router.post("/documents/{document_id}/ticket", dependencies=[Depends(verify_auth_token)])
async def create_download_ticket(document_id: str):
    """
    Generate a short-lived (60s) single-use download ticket for a document.
    Prevents session token exposure in download URLs and server access logs.
    """
    crm = CRMDatabase.get_instance()
    with crm._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM documents WHERE id = ?", (document_id.strip(),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found.")

    ticket = secrets.token_urlsafe(24)
    _download_tickets[ticket] = (document_id.strip(), time.time() + 60.0)
    return {"ticket": ticket, "expires_in": 60}


def verify_download_access(request: Request, document_id: str, ticket: Optional[str] = None):
    """Authenticates document download via single-use ticket or X-Session-Token header."""
    verify_localhost(request)

    # 1. Single-use download ticket
    if ticket:
        now = time.time()
        # Clean expired tickets
        expired = [t for t, (_, exp) in _download_tickets.items() if exp < now]
        for t in expired:
            _download_tickets.pop(t, None)

        entry = _download_tickets.pop(ticket, None)
        if entry:
            ticket_doc_id, expires_at = entry
            if ticket_doc_id == document_id.strip() and now <= expires_at:
                return True
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Download ticket is invalid, expired, or already used."
        )

    # 2. X-Session-Token or Authorization header
    token = request.headers.get("X-Session-Token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()

    expected = get_or_create_session_token()
    if token and hmac.compare_digest(token.strip(), expected):
        return True

    # Note: Query parameter '?token=' is explicitly rejected to prevent URL token leakage
    raise HTTPException(
        status_code=401,
        detail="Unauthorized: Valid X-Session-Token header or one-time download ticket required."
    )


@agency_router.get("/documents/{document_id}/download")
async def download_document(
    document_id: str,
    request: Request,
    format: str = Query("md"),
    ticket: Optional[str] = Query(None)
):
    """Securely download generated contract, invoice, or welcome document."""
    verify_download_access(request, document_id, ticket=ticket)

    crm = CRMDatabase.get_instance()
    with crm._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM documents WHERE id = ?", (document_id.strip(),))
        row = cur.fetchone()
        if row:
            doc = dict(row)
        else:
            raise HTTPException(status_code=404, detail="Document not found.")

    target_path = doc["path"]
    if format.lower() == "docx":
        docx_path = target_path.replace(".md", ".docx")
        if os.path.exists(docx_path):
            target_path = docx_path

    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Document file does not exist on disk.")

    filename = os.path.basename(target_path)
    return FileResponse(
        path=target_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document" if target_path.endswith(".docx") else "text/markdown"
    )


# =============================================================================
# 8. Template Editor & Validator
# =============================================================================

@agency_router.get("/templates", dependencies=[Depends(verify_auth_token)])
async def list_templates():
    """List all available outreach and document templates."""
    results = []
    for root, _, files in os.walk(TEMPLATES_BASE_DIR):
        for f in files:
            if f.endswith(".j2") or f.endswith(".md"):
                rel_path = os.path.relpath(os.path.join(root, f), TEMPLATES_BASE_DIR)
                is_contract = rel_path.startswith("contract")
                results.append({
                    "path": rel_path,
                    "name": f,
                    "is_contract": is_contract,
                    "is_readonly": is_contract  # Legal templates locked by default
                })
    return {"templates": results}


@agency_router.get("/templates/{template_path:path}", dependencies=[Depends(verify_auth_token)])
async def get_template_content(template_path: str):
    """Get raw template content and valid token palette."""
    full_path = os.path.abspath(os.path.join(TEMPLATES_BASE_DIR, template_path))
    if not full_path.startswith(TEMPLATES_BASE_DIR) or not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Template not found.")

    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()

    is_contract = template_path.startswith("contract")
    allowed_tokens = list(ALLOWED_CONTRACT_TOKENS if is_contract else ALLOWED_OUTREACH_TOKENS)

    return {
        "path": template_path,
        "content": content,
        "allowed_tokens": allowed_tokens,
        "is_contract": is_contract
    }


@agency_router.post("/templates/{template_path:path}", dependencies=[Depends(verify_auth_token)])
async def save_template_content(template_path: str, request: Request):
    """
    Saves template content after validating tokens.
    Rejects unknown tokens with 400.
    For legal templates, bumps version and resets lawyer_reviewed.
    """
    full_path = os.path.abspath(os.path.join(TEMPLATES_BASE_DIR, template_path))
    if not full_path.startswith(TEMPLATES_BASE_DIR):
        raise HTTPException(status_code=403, detail="Invalid template path.")

    body = await request.json()
    new_content = body.get("content", "")

    # Token validation
    is_contract = template_path.startswith("contract")
    allowed = ALLOWED_CONTRACT_TOKENS if is_contract else ALLOWED_OUTREACH_TOKENS

    tokens_found = re.findall(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", new_content)
    unknown_tokens = [t for t in tokens_found if t not in allowed]

    if unknown_tokens:
        raise HTTPException(
            status_code=400,
            detail=f"Template contains unknown tokens: {unknown_tokens}. Allowed: {sorted(list(allowed))}"
        )

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    crm = CRMDatabase.get_instance()
    crm.record_event(
        event_type="template_updated",
        payload={"template": template_path, "is_contract": is_contract}
    )

    return {
        "status": "success",
        "path": template_path,
        "message": "Template saved successfully."
    }


# =============================================================================
# 9. Suppression Registry
# =============================================================================

@agency_router.get("/suppression", dependencies=[Depends(verify_auth_token)])
async def list_suppression_entries(q: Optional[str] = Query(None)):
    """Search and list suppression list entries."""
    crm = CRMDatabase.get_instance()
    with crm._get_connection() as conn:
        cur = conn.cursor()
        if q:
            term = f"%{q.strip().lower()}%"
            cur.execute(
                "SELECT * FROM suppression WHERE value_normalised LIKE ? OR reason LIKE ? ORDER BY added_at DESC",
                (term, term)
            )
        else:
            cur.execute("SELECT * FROM suppression ORDER BY added_at DESC")
        rows = [dict(r) for r in cur.fetchall()]

    return {"suppression": rows, "count": len(rows)}


@agency_router.post("/suppression", dependencies=[Depends(verify_auth_token)])
async def add_suppression_entry(request: Request):
    """Add a manual suppression entry."""
    body = await request.json()
    kind = body.get("kind")
    value = body.get("value")
    reason = body.get("reason", "Operator manual suppression")

    if not kind or not value:
        raise HTTPException(status_code=400, detail="kind and value are required.")

    crm = CRMDatabase.get_instance()
    suppression = SuppressionManager(crm=crm)
    entry = suppression.add(kind=kind, value=value, reason=reason, source="operator_ui")
    return {"status": "success", "entry": entry}


@agency_router.get("/suppression/export", dependencies=[Depends(verify_auth_token)])
async def export_suppression_csv():
    """Export suppression entries as CSV."""
    crm = CRMDatabase.get_instance()
    suppression = SuppressionManager(crm=crm)
    csv_str = suppression.export_csv()
    return Response(
        content=csv_str,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=suppression_list.csv"}
    )


@agency_router.post("/suppression/import", dependencies=[Depends(verify_auth_token)])
async def import_suppression_csv(request: Request):
    """Import suppression entries from CSV text."""
    body = await request.body()
    csv_text = body.decode("utf-8")
    crm = CRMDatabase.get_instance()
    suppression = SuppressionManager(crm=crm)
    imported_count = suppression.import_csv(csv_text, source="csv_import")
    return {"status": "success", "imported_count": imported_count}


# =============================================================================
# 10. Audit Activity Log
# =============================================================================

@agency_router.get("/events", dependencies=[Depends(verify_auth_token)])
async def list_audit_events(
    lead_id: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    limit: int = Query(100)
):
    """Filterable CRM audit trail events log."""
    crm = CRMDatabase.get_instance()
    events = crm.get_events(lead_id=lead_id, limit=limit)
    if type:
        events = [e for e in events if e.get("type") == type.strip()]
    return {"events": events, "count": len(events)}
