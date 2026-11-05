"""Growth Desk — §8 overflow + §15 extras, all codeable parts (P5 completion).

- ScopeGuard (§8): incoming client message vs original scope -> in_scope |
  change_order (with price band) | ping. Change orders are DRAFTED and pinged;
  Ghost never reprices alone.
- BidCopilot (§8 Phase A): Upwork/Fiverr ToS forbid auto-bidding, so this
  monitors feeds, scores jobs against capabilities, drafts bids into a review
  queue — a human presses send. There is deliberately no auto-bid path.
- ReferralEngine (§15.1): post-close referral nudge, drafted at L2.
- RenewalConcierge (§15.3): domain expiry (from the monitor's RDAP check)
  30+ days out -> proposal job.
- ChurnPredictor (§15.7): deterministic at-risk score from reply latency
  trend + report engagement + complaint history.
"""
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ghost.core.audit import audit

logger = logging.getLogger("ghost.growth")


# ------------------------------------------------------------------ scope guard

_EXPANSION_MARKERS = [
    r"\balso\b.*\b(add|build|make|include)\b", r"\bcould you also\b", r"\bone more (thing|page|feature)\b",
    r"\bwhile you.?re at it\b", r"\badd(ing)? (a |an |the )?(new )?(page|section|feature|menu|gallery|blog|shop|payment)\b",
    r"\bextra (page|feature|section)\b", r"\bchange (the|our) (logo|branding|name)\b",
    r"\be-commerce\b", r"\bonline (ordering|booking|payment)s?\b",
]
_SCOPE_WORDS = [r"\bmenu\b", r"\bphotos?\b", r"\bhours\b", r"\bcontact\b", r"\btext\b",
                r"\bcontent\b", r"\btypos?\b", r"\bcolors?\b", r"\bfix\b"]


@dataclass
class ScopeVerdict:
    in_scope: bool
    change_order: Optional[Dict[str, Any]]  # {kind, price_band_inr, reason}
    ping_operator: bool
    reason: str


class ScopeGuard:
    def __init__(self, price_bands: Optional[Dict[str, List[float]]] = None):
        # (low, high) INR bands per expansion kind — operator's knob file can override.
        self.bands = price_bands or {
            "new_page": [8000, 25000],
            "online_booking": [25000, 80000],
            "ecommerce": [60000, 200000],
            "rebrand": [15000, 60000],
            "content_addition": [3000, 12000],
        }

    def check(self, message: str, deal_closed: bool = False) -> ScopeVerdict:
        text = (message or "").lower()
        for p in _SCOPE_WORDS:
            if re.search(p, text) and not re.search("|".join(_EXPANSION_MARKERS), text):
                return ScopeVerdict(True, None, False, "original scope adjustment")
        for marker in _EXPANSION_MARKERS:
            if re.search(marker, text):
                kind = self._kind_for(text)
                band = self.bands.get(kind, [5000, 30000])
                verdict = ScopeVerdict(
                    in_scope=False,
                    change_order={"kind": kind, "price_band_inr": band,
                                  "reason": f"matched: {marker}"},
                    ping_operator=True, reason="scope expansion detected")
                audit("scope.change_order", kind=kind, band=band,
                      ping="(a) money" if deal_closed else "(c) unverifiable")
                return verdict
        return ScopeVerdict(True, None, False, "no scope markers")

    def _kind_for(self, text: str) -> str:
        if re.search(r"e-?commerce|shop|payment", text):
            return "ecommerce"
        if re.search(r"booking|reservation|appointment", text):
            return "online_booking"
        if re.search(r"logo|branding|name", text):
            return "rebrand"
        if re.search(r"(new |extra )?(page|section|gallery|blog|menu)", text):
            return "new_page"
        return "content_addition"


# ------------------------------------------------------------------ bid copilot

class BidCopilot:
    """§8 Phase A: monitor feeds, draft bids into a review queue. A HUMAN sends.
    There is deliberately no auto-bid code path — ToS honesty (§0.6)."""

    def __init__(self, feed_url: Optional[str] = None,
                 capability_keywords: Optional[List[str]] = None,
                 drafts: Optional[List[Dict[str, Any]]] = None):
        self.feed_url = feed_url
        self.keywords = [k.lower() for k in (capability_keywords or [
            "website", "landing page", "wordpress", "three.js", "webflow",
            "restaurant website", "portfolio site"])]
        self.drafts = drafts if drafts is not None else []

    def fetch_feed(self, fetch: Optional[Callable[[str], str]] = None) -> List[Dict[str, Any]]:
        if not self.feed_url:
            return []
        try:
            if fetch:
                xml = fetch(self.feed_url)
            else:
                import urllib.request
                req = urllib.request.Request(self.feed_url, headers={"User-Agent": "Ghost-BidCopilot/1.0"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    xml = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            audit("bidcopilot.feed_error", error=str(e)[:150])
            return []
        import re
        items = []
        for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
            block = m.group(1)
            title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.S)
            link = re.search(r"<link>(.*?)</link>", block)
            desc = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", block, re.S)
            items.append({"title": (title.group(1).strip() if title else ""),
                          "link": (link.group(1).strip() if link else ""),
                          "description": (desc.group(1).strip() if desc else "")})
        return items

    def score_job(self, job: Dict[str, Any]) -> float:
        text = f"{job.get('title', '')} {job.get('description', '')}".lower()
        hits = sum(1 for k in self.keywords if k in text)
        budget_boost = 1 if re.search(r"\$\d{3,}", text) else 0
        return float(hits) + budget_boost

    def draft_bid(self, job: Dict[str, Any], min_score: float = 2.0) -> Optional[Dict[str, Any]]:
        score = self.score_job(job)
        if score < min_score:
            return None
        draft = {
            "job_title": job.get("title"), "job_link": job.get("link"),
            "score": score,
            "bid_text": self._bid_text(job),
            "status": "awaiting_human_send",   # THE contract: human presses send
            "at": time.time(),
        }
        self.drafts.append(draft)
        audit("bidcopilot.drafted", job=job.get("title", "")[:100], score=score,
              note="human must press send (ToS §0.6)")
        return draft

    @staticmethod
    def _bid_text(job: Dict[str, Any]) -> str:
        return (f"Hi — we build exactly this kind of site and can show you two live "
                f"examples this week. Fixed price, mobile-first, delivered in 2 weeks. "
                f"Re: '{(job.get('title') or '')[:60]}' — a quick scope question before we "
                f"quote precisely: {job.get('link', '')}")


# ------------------------------------------------------------- §15 extras

class ReferralEngine:
    """§15.1: post-close referral nudge. Drafted for the operator's identity,
    sent at L2 sampling through the normal outbound gates."""

    def draft_referral_ask(self, client_slug: str, finder_discount_pct: float = 10.0) -> Dict[str, Any]:
        draft = {
            "client_slug": client_slug,
            "body": (f"Hi — it's been a pleasure building for you. If you know another "
                     f"business owner who needs a website, introduce us and they get "
                     f"{finder_discount_pct:.0f}% off their first project, and you get the "
                     f"same off your next month of care. No pressure at all."),
            "status": "drafted_L2", "at": time.time(),
        }
        audit("referral.drafted", subject=client_slug, discount_pct=finder_discount_pct)
        return draft


class RenewalConcierge:
    """§15.3: domain/hosting renewals — propose savings 30+ days out, never
    let a client domain lapse (also fed by monitor.check_domain_expiry)."""

    def proposal_due(self, domain_days_left: float) -> bool:
        return 0 <= domain_days_left <= 30

    def draft_proposal(self, client_slug: str, domain: str, days_left: float,
                       current_cost_inr: float, cheaper_cost_inr: float) -> Dict[str, Any]:
        savings = round(current_cost_inr - cheaper_cost_inr, 2)
        draft = {
            "client_slug": client_slug, "domain": domain,
            "body": (f"Your domain {domain} renews in {days_left:.0f} days. We found the "
                     f"same registration at ₹{cheaper_cost_inr:,.0f}/yr vs your current "
                     f"₹{current_cost_inr:,.0f}/yr — saving ₹{savings:,.0f}/yr. "
                     f"Reply 'switch' and we handle the transfer end-to-end."),
            "savings_inr": savings, "status": "drafted_L2", "at": time.time(),
        }
        audit("renewal.proposal_drafted", subject=client_slug, domain=domain,
              savings_inr=savings, days_left=days_left)
        return draft


class ChurnPredictor:
    """§15.7: deterministic at-risk score (0-100) — reply latency trend +
    report engagement + complaint history. >= 70 -> proactive sweetener draft."""

    def score(self, days_since_last_reply: float, report_opened: bool = True,
              complaints_last_90d: int = 0, payment_failures: int = 0) -> Dict[str, Any]:
        score = 0.0
        reasons = []
        if days_since_last_reply > 30:
            score += 30
            reasons.append(f"no reply in {days_since_last_reply:.0f} days")
        elif days_since_last_reply > 14:
            score += 15
            reasons.append(f"reply latency rising ({days_since_last_reply:.0f}d)")
        if not report_opened:
            score += 25
            reasons.append("monthly report unopened")
        score += min(30, complaints_last_90d * 15)
        if complaints_last_90d:
            reasons.append(f"{complaints_last_90d} complaint(s) in 90d")
        score += min(20, payment_failures * 10)
        if payment_failures:
            reasons.append(f"{payment_failures} payment failure(s)")
        at_risk = score >= 70
        audit("churn.scored", score=score, at_risk=at_risk, reasons=reasons,
              ping="(c) unverifiable: churn risk" if at_risk else None)
        return {"score": round(score, 1), "at_risk": at_risk, "reasons": reasons}
