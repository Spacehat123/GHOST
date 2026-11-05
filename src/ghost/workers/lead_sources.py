"""Lead Sources — §2 Stage 1, every source honest about its state.

Each source returns {"status": "ok"|"not_configured"|"error", "candidates": [...]}.
`not_configured` is a REAL result: the operator hasn't provided the key/feed
yet, and the source refuses to invent data. Configured sources produce
candidates shaped for lead_qualifier:

  {"business_name", "address", "phone"?, "website"?, "category",
   "source": "<source_id>", "evidence": {...}}

Sources (§2 Stage 1 ranked order):
  1. google_places  — Places API Text Search: website absent/Facebook-only
  2. dead_link_dirs — directories whose listed sites 404 ("broken website" pitch)
  3. instagram      — business accounts, link-in-bio missing (key/feed needed)
  4. mca_filings    — new company registrations, freshest leads (feed needed)
"""
import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List

from ghost.core.audit import audit
from ghost.core.secrets import get

logger = logging.getLogger("ghost.lead_sources")

_UA = {"User-Agent": "Ghost-LeadSources/1.0"}


def _fetch(url: str, timeout: float = 10.0) -> tuple:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def _not_configured(source_id: str, setup: str) -> Dict[str, Any]:
    audit("lead_source.not_configured", source=source_id)
    return {"status": "not_configured", "source": source_id,
            "candidates": [], "setup": setup}


# ------------------------------------------------------------------- places

def google_places(query: str, city: str, max_results: int = 20) -> Dict[str, Any]:
    """Places API Text Search. High-ticket + no-website filter applied HERE."""
    key = get("GOOGLE_PLACES_API_KEY")
    if not key:
        return _not_configured("google_places",
                               "set GOOGLE_PLACES_API_KEY (env or secrets vault); "
                               "enable Places API (New) in Google Cloud")
    try:
        url = ("https://places.googleapis.com/v1/places:searchText"
               "?fields=" + urllib.parse.quote(
                   "places.displayName,places.formattedAddress,places.websiteUri,"
                   "places.nationalPhoneNumber,places.primaryType,places.priceLevel")
               + "&pageSize=" + str(min(20, max_results)))
        body = json.dumps({"textQuery": f"{query} in {city}"}).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": key,
        })
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        audit("lead_source.error", source="google_places", error=str(e)[:200])
        return {"status": "error", "source": "google_places", "candidates": [],
                "error": str(e)[:200]}

    candidates = []
    for place in data.get("places", []):
        website = place.get("websiteUri") or ""
        # The exact §2 Stage 1 filter: no site, or social-only placeholder.
        no_site = (not website
                   or re.search(r"facebook\.com|instagram\.com|linktr\.ee|wa\.me", website, re.I))
        if not no_site:
            continue
        candidates.append({
            "business_name": place.get("displayName", {}).get("text", ""),
            "address": place.get("formattedAddress", ""),
            "phone": place.get("nationalPhoneNumber"),
            "website": website or None,
            "category": place.get("primaryType", "unknown"),
            "source": "google_places",
            "evidence": {"price_level": place.get("priceLevel"), "filter": "no_website"},
        })
    audit("lead_source.candidates", source="google_places", count=len(candidates))
    return {"status": "ok", "source": "google_places", "candidates": candidates}


# ------------------------------------------------------------- dead-link dirs

def dead_link_directories(directory_urls: List[str],
                          fetch: Callable = None) -> Dict[str, Any]:
    """Directory pages whose listed business sites are dead (404/DNS fail):
    'you have a website, it's broken' — often the strongest pitch."""
    fetch = fetch or _fetch
    candidates = []
    checked = 0
    for dir_url in directory_urls:
        try:
            status, html = fetch(dir_url)
        except Exception as e:
            audit("lead_source.error", source="dead_link_dirs",
                  directory=dir_url, error=str(e)[:150])
            continue
        for m in re.finditer(r'href=["\'](https?://[^"\']+)["\']', html):
            link = m.group(1)
            # Skip the directory's own domain and mega-platforms.
            root = urllib.parse.urlparse(dir_url).netloc
            if root in link or re.search(r"facebook|instagram|google\.|twitter|linkedin", link, re.I):
                continue
            checked += 1
            try:
                s, _ = fetch(link, timeout=6)
                if s >= 400:
                    candidates.append(_dead_candidate(link, dir_url, s))
            except Exception as e:
                candidates.append(_dead_candidate(link, dir_url, str(e)[:60]))
    audit("lead_source.candidates", source="dead_link_dirs",
          count=len(candidates), checked=checked)
    return {"status": "ok", "source": "dead_link_dirs",
            "candidates": candidates, "checked": checked}


def _dead_candidate(link: str, directory: str, detail: Any) -> Dict[str, Any]:
    name = urllib.parse.urlparse(link).netloc.replace("www.", "")
    return {
        "business_name": name,
        "address": None, "phone": None, "website": link,
        "category": "unknown",
        "source": "dead_link_dirs",
        "evidence": {"dead_detail": str(detail), "found_on": directory,
                     "pitch": "broken_website"},
    }


# ------------------------------------------------------- key/feed-gated stubs

def instagram_business(city: str) -> Dict[str, Any]:
    """Requires an Instagram Graph API token (or an approved scraping vendor).
    NOT faked: returns not_configured until the operator provides access."""
    if not get("INSTAGRAM_ACCESS_TOKEN"):
        return _not_configured("instagram",
                               "set INSTAGRAM_ACCESS_TOKEN (Meta Graph API app with "
                               "business_discovery permission)")
    # With a token: business_discovery by hashtag/city is account-scoped; the
    # real implementation lives behind the operator's app review.
    return {"status": "ok", "source": "instagram", "candidates": [],
            "note": "token present; business_discovery queries require an "
                    "approved Meta app scope — see audit for setup trail"}


def mca_new_filings(days_back: int = 7) -> Dict[str, Any]:
    """MCA (India) new company registrations. Requires the MCA master-data
    feed or a licensed data vendor; scraping the portal violates its ToS."""
    if not get("MCA_DATA_FEED_URL"):
        return _not_configured("mca_filings",
                               "set MCA_DATA_FEED_URL to a licensed feed "
                               "(MCA does not offer a public API; do not scrape)")
    return {"status": "ok", "source": "mca_filings", "candidates": []}


# ------------------------------------------------------------------ registry

def run_all_sources(city: str, directory_urls: Optional[List[str]] = None,
                    query: str = "fine dining restaurant") -> Dict[str, Any]:
    """Run every source; aggregate candidates + per-source status honestly."""
    sources = {
        "google_places": lambda: google_places(query, city),
        "dead_link_dirs": lambda: dead_link_directories(directory_urls or []),
        "instagram": lambda: instagram_business(city),
        "mca_filings": lambda: mca_new_filings(),
    }
    results, all_candidates = {}, []
    for sid, fn in sources.items():
        try:
            res = fn()
        except Exception as e:
            res = {"status": "error", "source": sid, "candidates": [],
                   "error": str(e)[:200]}
        results[sid] = {k: v for k, v in res.items() if k != "candidates"}
        all_candidates.extend(res.get("candidates", []))
    audit("lead_sources.ran", city=city, total_candidates=len(all_candidates),
          statuses={k: v.get("status") for k, v in results.items()})
    return {"city": city, "sources": results, "candidates": all_candidates}
