"""DNS Deliverability Checker — §6.1 verification, not just configuration.

SPF + DKIM + DMARC are DNS records the OPERATOR sets at their registrar;
this module is the proof half: it looks the records up and reports exactly
what's missing before the warmup schedule is allowed to start. Uses stdlib
subprocess `dig` (present on Arch); honest degradation when dig is absent.
"""
import logging
import subprocess
from typing import Any, Dict, List

from ghost.core.audit import audit

logger = logging.getLogger("ghost.dnscheck")

DKIM_SELECTORS = ["ghost", "default", "google", "zoho", "smtp"]


def _dig(name: str, record_type: str) -> List[str]:
    try:
        res = subprocess.run(["dig", "+short", name, record_type],
                             capture_output=True, text=True, timeout=6)
        if res.returncode != 0:
            return []
        return [line.strip().strip('"') for line in res.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def check_domain_deliverability(domain: str) -> Dict[str, Any]:
    """Full §6.1 readiness report for a sending domain:
    SPF present, DMARC present (p= policy noted), DKIM selectors probed."""
    domain = (domain or "").strip().lower().removeprefix("http://").removeprefix("https://").rstrip("/")
    spf_records = [r for r in _dig(domain, "TXT") if r.lower().startswith("v=spf1")]
    dmarc_records = [r for r in _dig(f"_dmarc.{domain}", "TXT") if r.lower().startswith("v=dmarc1")]
    dkim_hits = {}
    for selector in DKIM_SELECTORS:
        records = _dig(f"{selector}._domainkey.{domain}", "TXT")
        if records:
            dkim_hits[selector] = records[0][:60]

    spf_ok = bool(spf_records)
    dmarc_ok = bool(dmarc_records) and "p=reject" in dmarc_records[0].lower()
    dkim_ok = bool(dkim_hits)
    ready = spf_ok and dmarc_ok and dkim_ok

    missing: List[str] = []
    if not spf_ok:
        missing.append(f"SPF TXT record: 'v=spf1 include:your-provider ~all' on {domain}")
    if not dmarc_ok:
        missing.append(f"DMARC TXT record on _dmarc.{domain} with p=reject (§6.1)")
    if not dkim_ok:
        missing.append(f"DKIM: publish a selector (tried: {', '.join(DKIM_SELECTORS)}) "
                       "from your SMTP provider's settings page")

    report = {
        "domain": domain,
        "spf": {"found": spf_ok, "record": spf_records[0][:100] if spf_records else None},
        "dmarc": {"found": bool(dmarc_records), "policy_reject": dmarc_ok,
                  "record": dmarc_records[0][:100] if dmarc_records else None},
        "dkim": {"found": dkim_ok, "selectors": dkim_hits},
        "ready": ready,
        "missing": missing,
    }
    audit("dnscheck.report", domain=domain, ready=ready, missing=missing)
    return report


def warmup_may_start(domain: str) -> Dict[str, Any]:
    """Gate for the warmup schedule: cold outreach on a domain without
    SPF+DKIM+DMARC is business-death (§6). Fail closed."""
    report = check_domain_deliverability(domain)
    if not report["ready"]:
        return {"allowed": False, "reason": "deliverability records incomplete",
                "missing": report["missing"], "report": report}
    audit("dnscheck.warmup_cleared", domain=domain)
    return {"allowed": True, "report": report}
