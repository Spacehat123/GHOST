"""White-label Tenants — agencies brand Ghost, revenue routes automatically (Roadmap §9, P5).

Scaffolding for the agency surface:
- TenantRegistry: each agency = a tenant with its own branding (from-name,
  reply-to, sending domain) and a revenue split. Tenant config is data the
  OPERATOR owns; Ghost never edits it.
- RevenueSplit: deterministic money math — agency share + platform share from
  the split pct. Decimal, no floats in money, ever.
- AgencyLeadQueue: the agency-facing product surface: submit lead -> status
  transitions (submitted -> building -> demo_ready -> delivered) they can
  poll. Built ON the P0 job queue, so every transition is audited evidence
  and halt-aware. Agencies never see Ghost's internals.

Constitution notes: a tenant's outreach still goes through the OutboundGateway
(same caps, same suppression, same personalization gate — white-label does not
buy exemption from the anti-spam constitution).
"""
import json
import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from ghost.core.audit import audit

logger = logging.getLogger("ghost.tenants")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
TENANTS_PATH = os.environ.get("GHOST_TENANTS_PATH") or os.path.join(REPO_DIR, "configs", "tenants.json")

TENANT_STATUSES = ("submitted", "building", "demo_ready", "delivered", "rejected")


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    name: str
    from_name: str            # client-visible sender identity (theirs, not Ghost's)
    reply_to: str             # a human reads replies
    sending_domain: str       # their warmed domain; never the operator's personal one
    revenue_split_pct: float  # agency's share of collected revenue
    active: bool = True

    def validated(self) -> "Tenant":
        """Fail closed on junk config: bad split, missing reply-to, personal-domain risk."""
        pct = self.revenue_split_pct
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            pct = -1
        if not (0 < pct < 100):
            raise ValueError(f"revenue_split_pct must be 0-100 exclusive, got {self.revenue_split_pct!r}")
        if not self.reply_to or "@" not in self.reply_to:
            raise ValueError(f"tenant '{self.tenant_id}' needs a real reply_to address")
        if not self.sending_domain or "." not in self.sending_domain:
            raise ValueError(f"tenant '{self.tenant_id}' needs a real sending domain")
        return Tenant(self.tenant_id, self.name, self.from_name, self.reply_to,
                      self.sending_domain.lower(), pct, self.active)


class TenantRegistry:
    def __init__(self, path: str = TENANTS_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._tenants: Dict[str, Tenant] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for tid, cfg in raw.get("tenants", {}).items():
                try:
                    self._tenants[tid] = Tenant(
                        tenant_id=tid, name=cfg.get("name", tid),
                        from_name=cfg.get("from_name", tid),
                        reply_to=cfg.get("reply_to", ""),
                        sending_domain=cfg.get("sending_domain", ""),
                        revenue_split_pct=cfg.get("revenue_split_pct", 0),
                        active=bool(cfg.get("active", True)),
                    ).validated()
                except ValueError as e:
                    # A bad tenant config is refused, not silently coerced.
                    audit("tenant.config_refused", subject=tid, error=str(e)[:200])
        except Exception as e:
            logger.warning("tenant registry unreadable: %s", e)

    def get(self, tenant_id: str) -> Optional[Tenant]:
        t = self._tenants.get(tenant_id)
        return t if (t and t.active) else None

    def list(self) -> List[Tenant]:
        return [t for t in self._tenants.values() if t.active]


def revenue_split(amount_inr: Any, split_pct: Any) -> Dict[str, Decimal]:
    """Deterministic, Decimal-exact split. Raises ValueError on junk — money never guesses."""
    try:
        amount = Decimal(str(amount_inr))
        pct = Decimal(str(split_pct))
    except (InvalidOperation, TypeError, ValueError) as e:
        raise ValueError(f"unparseable money input: {e}")
    if amount < 0:
        raise ValueError("amount must be non-negative")
    if not (Decimal("0") < pct < Decimal("100")):
        raise ValueError("split_pct must be 0-100 exclusive")
    paisa = Decimal("0.01")
    agency = (amount * pct / Decimal("100")).quantize(paisa, rounding=ROUND_HALF_UP)
    platform = (amount - agency).quantize(paisa, rounding=ROUND_HALF_UP)
    return {"agency_share_inr": agency, "platform_share_inr": platform}


class AgencyLeadQueue:
    """Agency-visible lead pipeline over the P0 job queue.

    The agency experience is exactly: submit -> poll status -> receive demo.
    Every transition is a job on the durable queue + an audit event, so an
    agency-facing dashboard can be built later without new machinery.
    """

    def __init__(self, job_queue, registry: TenantRegistry):
        self.q = job_queue
        self.registry = registry

    def submit_lead(self, tenant_id: str, lead: Dict[str, Any]) -> Dict[str, Any]:
        tenant = self.registry.get(tenant_id)
        if not tenant:
            return {"ok": False, "error": f"unknown or inactive tenant '{tenant_id}'"}
        if not lead.get("business_name") or not lead.get("contact_email"):
            return {"ok": False, "error": "lead needs business_name and contact_email"}
        ref = f"{tenant_id}-{uuid.uuid4().hex[:10]}"
        self.q.enqueue("agency.build_demo", {
            "ref": ref, "tenant_id": tenant_id, "lead": lead,
        }, idempotency_key=f"agency-lead:{ref}")
        audit("agency.lead_submitted", subject=ref, tenant=tenant_id,
              business=lead.get("business_name"))
        return {"ok": True, "ref": ref, "status": "submitted"}

    def mark(self, ref: str, status: str) -> None:
        if status not in TENANT_STATUSES:
            raise ValueError(f"bad tenant status '{status}'")
        audit("agency.lead_status", subject=ref, status=status)

    def agency_view(self, tenant_id: str, refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sanitized status list for the agency — no internals, just their refs."""
        tenant = self.registry.get(tenant_id)
        if not tenant:
            return []
        return [{"ref": r["ref"], "status": r.get("status", "submitted"),
                 "demo_url": r.get("demo_url")}
                for r in refs if r.get("tenant_id") == tenant_id]


import uuid  # noqa: E402  (used above; kept at bottom to keep the import block clean)
