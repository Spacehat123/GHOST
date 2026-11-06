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

from fastapi import APIRouter, Request, HTTPException, Depends, Header, Query
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
    """
    Records reply from prospect.
    If opt-out intent detected or manual opt-out requested, triggers suppression immediately.
    """
    crm = CRMDatabase.get_instance()
    drafter = OutreachDrafter(crm=crm)

    body = await request.json()
    reply_text = body.get("reply_text", "")
    is_opt_out = bool(body.get("is_opt_out", False))

    res = drafter.handle_incoming_reply(draft_id, reply_text=reply_text, is_manual_opt_out=is_opt_out)
    return res


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
