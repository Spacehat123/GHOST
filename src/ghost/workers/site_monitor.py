"""Site Monitor — the retainer fleet's eyes (Roadmap §2 Stage 7).

Checks per client site, each producing EVIDENCE (measured values, not vibes):
  uptime       — HTTP status + latency
  ssl          — cert expiry (alert 21 days out)
  broken_links — sampled link check against the homepage
  seo_drift    — title/meta present, robots.txt + sitemap.xml reachable

Failures become incidents in the retainer store. Each incident gets an
auto-fix disposition by tier (§2 Stage 7):
  T1 (L4): safe mechanical fixes from the injected fixer registry
  T2 (L3): restore from a drill-verified snapshot
  T3 (L2): staged — nothing touched, operator pinged with a one-click approve
Unknown fix / no snapshot / level too low -> ping (c). NEVER touch a live
site without a snapshot on file.
"""
import json
import logging
import os
import socket
import ssl
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from ghost.core.audit import audit
from ghost.core.halt import assert_not_halted

logger = logging.getLogger("ghost.site_monitor")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
MONITOR_DB_PATH = os.environ.get("GHOST_MONITOR_DB_PATH") or os.path.join(REPO_DIR, "data", "monitor.db")
SSL_ALERT_DAYS = 21
LINK_SAMPLE = 8


# ------------------------------------------------------------------- checks
# Each check returns {"ok": bool, "detail": str, "evidence": {...}}.

def check_uptime(url: str, fetch: Callable = None, timeout: float = 8.0) -> Dict[str, Any]:
    fetch = fetch or _default_fetch
    t0 = time.time()
    try:
        status, _body = fetch(url, timeout=timeout)
        latency = round(time.time() - t0, 3)
        ok = 200 <= status < 400
        return {"ok": ok, "detail": f"HTTP {status} in {latency}s",
                "evidence": {"status": status, "latency_s": latency}}
    except Exception as e:
        return {"ok": False, "detail": f"unreachable: {e}",
                "evidence": {"error": str(e)[:200]}}


def check_ssl(url: str, host: Optional[str] = None) -> Dict[str, Any]:
    host = host or url.split("//")[-1].split("/")[0]
    try:
        cert = ssl.get_server_cert((host, 443), timeout=6) if False else _get_cert(host)
        not_after = ssl.cert_time_to_seconds(cert["notAfter"])
        days_left = round((not_after - time.time()) / 86400, 1)
        return {"ok": days_left >= SSL_ALERT_DAYS,
                "detail": f"cert expires in {days_left} days",
                "evidence": {"days_left": days_left, "not_after": cert["notAfter"]}}
    except Exception as e:
        return {"ok": False, "detail": f"SSL check failed: {e}",
                "evidence": {"error": str(e)[:200]}}


def _get_cert(host: str):
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=6) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            return ssock.getpeercert()


def check_seo_drift(url: str, fetch: Callable = None, timeout: float = 8.0) -> Dict[str, Any]:
    fetch = fetch or _default_fetch
    problems, evidence = [], {}
    try:
        status, body = fetch(url, timeout=timeout)
    except Exception as e:
        return {"ok": False, "detail": f"fetch failed: {e}", "evidence": {"error": str(e)[:200]}}
    evidence["status"] = status
    lower = (body or "").lower()
    import re as _re
    has_title = "<title" in lower and "</title>" in lower
    has_desc = bool(_re.search(r'name=["\']description["\']', lower))
    evidence.update({"title": has_title, "meta_description": has_desc})
    if not has_title:
        problems.append("missing <title>")
    if not has_desc:
        problems.append("missing meta description")
    for path, name in (("/robots.txt", "robots.txt"), ("/sitemap.xml", "sitemap.xml")):
        try:
            s, _ = fetch(url.rstrip("/") + path, timeout=timeout)
            evidence[name] = (s == 200)
            if s != 200:
                problems.append(f"{name} not reachable (HTTP {s})")
        except Exception as e:
            evidence[name] = False
            problems.append(f"{name} unreachable: {str(e)[:80]}")
    return {"ok": not problems, "detail": "; ".join(problems) or "SEO intact",
            "evidence": evidence}


def check_broken_links(url: str, fetch: Callable = None, sample: int = LINK_SAMPLE,
                       timeout: float = 8.0) -> Dict[str, Any]:
    fetch = fetch or _default_fetch
    try:
        status, body = fetch(url, timeout=timeout)
    except Exception as e:
        return {"ok": False, "detail": f"fetch failed: {e}", "evidence": {"error": str(e)[:200]}}
    import re
    hrefs = re.findall(r'href=["\'](https?://[^"\']+)["\']', body or "")
    internal = [h for h in hrefs if url.split("//")[-1].split("/")[0] in h][:sample]
    broken = []
    for link in internal:
        try:
            s, _ = fetch(link, timeout=timeout)
            if s >= 400:
                broken.append({"link": link, "status": s})
        except Exception as e:
            broken.append({"link": link, "status": str(e)[:60]})
    return {"ok": not broken, "detail": f"{len(broken)} broken of {len(internal)} checked",
            "evidence": {"checked": len(internal), "broken": broken}}


def _default_fetch(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers={"User-Agent": "Ghost-SiteMonitor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


CHECKS = {"uptime": check_uptime, "ssl": check_ssl,
          "seo_drift": check_seo_drift, "broken_links": check_broken_links}
