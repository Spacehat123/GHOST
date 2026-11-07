"""WORKERS — the business dossier (Operator's 11, goals 2/3/4).

For every qualified lead, Ghost assembles ONE folder that holds everything
known about the business:

    dossiers/<slug>/
      facts.json     every fact, its value, its SOURCE, and what is missing
      photos/        the business's own listing/site photos, downloaded LOCAL
      SOURCES.json   provenance ledger — one row per photo, sha256-stamped
      dossier.md     the human-readable brief for the operator

Honesty rules (same constitution as three_qa / the Source Ledger):
- NO INVENTED FACTS. A fact nobody observed is null in facts.json, listed in
  "inquire", and rendered `[Inquire for quotation]` in the markdown.
- NO HOTLINKING. Every photo is downloaded and provenance-stamped (source
  URL, sha256, date); a photo without its row fails the verifier.
- The verifier recomputes hashes — a dossier can only pass verification by
  actually containing what it claims to contain.

Photos come from the recon layer (ReconScout's Maps harvest, already
venue-validated). Tests inject `recon` + `fetch_binary`; the live default
fetches over HTTP with a bounded size.
"""
import hashlib
import json
import logging
import os
import re
import time
import urllib.request
from typing import Any, Dict, List, Optional

from ghost.core.audit import audit
from ghost.crm.db import CRMDatabase

logger = logging.getLogger("ghost.dossier")

# fact slots the dossier guarantees the SHAPE of (values may be null — the
# shape is the contract; the values must be earned)
_FACT_SLOTS = ("name", "category", "address", "phone", "website", "hours")
_REPUTATION_SLOTS = ("rating", "review_count")

MAX_PHOTO_BYTES = 15 * 1024 * 1024
_UA = "Ghost-Dossier/1.0 (+local research agent; contact: operator)"


def dossiers_dir() -> str:
    """Work product (like demos/), not runtime state — repo-root dossiers/.
    Lazy env resolution so tests can redirect without import-order traps."""
    base = os.environ.get("GHOST_DOSSIERS_DIR")
    if base:
        return base
    from ghost.paths import repo_root
    return os.path.join(repo_root(), "dossiers")


def _default_fetch_binary(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError(f"photo exceeds {MAX_PHOTO_BYTES} bytes — refusing")
    return data


class DossierBuilder:
    """Builds/refreshes the dossier folder for one lead. All inputs are
    evidence the caller already collected; nothing is scraped here twice,
    nothing is invented here at all."""

    def __init__(self, crm: Optional[CRMDatabase] = None,
                 fetch_binary=None, base_dir: Optional[str] = None):
        self.crm = crm or CRMDatabase.get_instance()
        self.fetch_binary = fetch_binary or _default_fetch_binary
        self.base_dir = base_dir or dossiers_dir()

    # ------------------------------------------------------------------ facts

    def _merge_facts(self, lead: Dict[str, Any],
                     recon: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """CRM row is the base; recon (Maps harvest) fills/extends. Every fact
        records where it came from. Missing = None + inquire — never guessed."""
        facts: Dict[str, Any] = {}
        inquire: List[str] = []

        def slot(key, crm_key, recon_key, source_crm="crm", source_recon="recon:google_maps"):
            val = lead.get(crm_key)
            source = source_crm
            if not val and recon:
                val = recon.get(recon_key)
                source = source_recon
            if isinstance(val, str):
                val = val.strip() or None
            facts[key] = {"value": val, "source": source if val else None}
            if not val:
                inquire.append(key)
            return val

        slot("name", "name", "name")
        slot("category", "google_category", "category")
        slot("address", "address", "address")
        slot("phone", "phone", "phone")
        slot("website", "website", "website")
        slot("hours", "hours", "hours")

        rating = lead.get("rating")
        if not rating and recon and recon.get("rating") is not None:
            rating = recon.get("rating")
        review_count = lead.get("review_count")
        if not review_count and recon and recon.get("reviewsCount") is not None:
            review_count = recon.get("reviewsCount")
        facts["rating"] = {"value": rating, "source": "crm" if lead.get("rating") else
                           ("recon:google_maps" if rating else None)}
        facts["review_count"] = {"value": review_count, "source": "crm" if lead.get("review_count")
                                 else ("recon:google_maps" if review_count else None)}
        if rating is None:
            inquire.append("rating")
        if review_count is None:
            inquire.append("review_count")

        # site verdict — evidence the qualifier/verifier plane already produced
        web_verif = lead.get("website_verification") or {}
        audit_data = lead.get("audit_data") or {}
        if web_verif.get("verdict"):
            site = {"verdict": web_verif["verdict"],
                    "evidence": web_verif.get("evidence_summary", ""),
                    "official_url": web_verif.get("official_website_url"),
                    "source": "venue_qualification"}
        elif lead.get("website"):
            site = {"verdict": "site_present_audit_pending",
                    "evidence": f"lead record lists {lead['website']}",
                    "official_url": lead.get("website"), "source": "crm"}
        else:
            site = {"verdict": "no_website_found",
                    "evidence": "no website on the lead record or the Maps listing",
                    "official_url": None, "source": "crm+recon:google_maps"}
        facts["site"] = site
        return {"facts": facts, "inquire": inquire}

    # ----------------------------------------------------------------- photos

    def _download_photos(self, slug: str, recon: Optional[Dict[str, Any]],
                         max_photos: int) -> List[Dict[str, Any]]:
        """Download the business's own listing photos. Re-validate with
        is_venue_photo (avatars/system icons never pass). A photo that fails
        to download is SKIPPED and logged — never replaced with a placeholder
        image or a hotlink."""
        from ghost.workers.recon_scout import is_venue_photo
        rows: List[Dict[str, Any]] = []
        if not recon:
            return rows
        photo_dir = os.path.join(self.base_dir, slug, "photos")
        os.makedirs(photo_dir, exist_ok=True)
        for p in (recon.get("photos") or [])[: max_photos * 2]:
            if len(rows) >= max_photos:
                break
            url = (p.get("url") or p.get("raw_url") or "").strip() if isinstance(p, dict) else str(p)
            if not url or not is_venue_photo(url):
                continue
            try:
                data = self.fetch_binary(url)
                if not data:
                    raise ValueError("empty body")
            except Exception as e:
                logger.warning("dossier %s: photo skipped (%s): %s", slug, url[:80], str(e)[:120])
                audit("dossier.photo_skipped", subject=slug, url=url[:200],
                      error=str(e)[:160])
                continue
            idx = len(rows) + 1
            fname = f"photo-{idx:02d}.jpg"
            with open(os.path.join(photo_dir, fname), "wb") as f:
                f.write(data)
            rows.append({
                "file": f"photos/{fname}",
                "source_url": url,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "retrieved_at": time.time(),
                "kind": "business_listing_photo",
                "license_note": "business's own public listing photo — used only in "
                                "THIS business's demo/dossier; not redistributed",
            })
            audit("dossier.photo_stored", subject=slug, file=fname,
                  sha256=rows[-1]["sha256"][:16], source=url[:200])
        return rows

    # ------------------------------------------------------------------ build

    def build_for_lead(self, slug: str, recon: Optional[Dict[str, Any]] = None,
                       max_photos: int = 8) -> Dict[str, Any]:
        """Build/refresh the dossier for one lead. Returns the honest report
        the verifier's dict feeds back through the proposal plane."""
        lead = self.crm.get_lead(slug)
        if not lead:
            audit("dossier.refused", subject=slug, reason="lead_not_found")
            return {"status": "error", "reason": f"lead {slug!r} not found in CRM"}

        merged = self._merge_facts(lead, recon)
        facts, inquire = merged["facts"], merged["inquire"]

        dossier_dir = os.path.join(self.base_dir, slug)
        photo_dir = os.path.join(dossier_dir, "photos")
        os.makedirs(photo_dir, exist_ok=True)
        photo_rows = self._download_photos(slug, recon, max_photos)

        # facts.json — the machine-readable truth, including what is missing
        facts_doc = {
            "slug": slug,
            "generated_at": time.time(),
            "identity": {k: facts[k] for k in _FACT_SLOTS},
            "reputation": {k: facts[k] for k in _REPUTATION_SLOTS},
            "site": facts["site"],
            "pipeline": {
                "status": lead.get("status"),
                "scores": {k: lead.get(k) for k in
                           ("score_revenue", "score_fit", "score_friction", "score_total")},
                "pitch_type": (json.loads(lead["gaps_json"]).get("pitch_type")
                               if lead.get("gaps_json") else None),
            },
            "inquire": inquire,
        }
        with open(os.path.join(dossier_dir, "facts.json"), "w", encoding="utf-8") as f:
            json.dump(facts_doc, f, indent=2, sort_keys=True)

        # SOURCES.json — the provenance ledger (photos + research provenance)
        sources_doc = {
            "lead_slug": slug,
            "generated_at": time.time(),
            "research": {
                "crm_record": "ghost CRM (operator-approved harvest)",
                "recon": "Google Maps listing harvest (ReconScout via Brave CDP)"
                         if recon else "no recon attached — CRM record only",
            },
            "photos": photo_rows,
            "disclosure": "All facts are observed evidence; missing facts are "
                          "listed in inquire and rendered [Inquire for quotation].",
        }
        with open(os.path.join(dossier_dir, "SOURCES.json"), "w", encoding="utf-8") as f:
            json.dump(sources_doc, f, indent=2, sort_keys=True)

        md = self.render_markdown(facts_doc, photo_rows)
        with open(os.path.join(dossier_dir, "dossier.md"), "w", encoding="utf-8") as f:
            f.write(md)

        verification = verify_dossier(slug, base_dir=self.base_dir)
        audit("dossier.built", subject=slug, photos=len(photo_rows),
              inquire=len(inquire), verified=verification["verified"])
        return {"status": "ok", "slug": slug, "dir": dossier_dir,
                "photos": len(photo_rows), "inquire": inquire,
                "verification": verification}

    # --------------------------------------------------------------- markdown

    def render_markdown(self, facts_doc: Dict[str, Any],
                        photo_rows: List[Dict[str, Any]]) -> str:
        """Human brief. Missing facts render [Inquire for quotation] — the
        operator is asked, never told a guess."""

        def val(key: str) -> str:
            v = facts_doc["identity"].get(key, {}).get("value")
            return str(v) if v not in (None, "") else f"[Inquire for quotation: {key}]"

        lines = [
            f"# Business Dossier: {val('name')}",
            "",
            f"* **Category**: {val('category')}",
            f"* **Address**: {val('address')}",
            f"* **Phone**: {val('phone')}",
            f"* **Website**: {val('website')}",
            f"* **Hours**: {val('hours')}",
        ]
        rep = facts_doc["reputation"]
        rating = rep["rating"]["value"]
        reviews = rep["review_count"]["value"]
        lines.append(f"* **Rating**: {rating if rating is not None else '[Inquire for quotation: rating]'}"
                     f" ({reviews if reviews is not None else '[Inquire for quotation: review count]'} reviews)")
        site = facts_doc["site"]
        lines += ["", "## Site verdict", f"- **`{site['verdict']}`** — {site['evidence']}"]
        pipeline = facts_doc.get("pipeline") or {}
        if pipeline.get("pitch_type"):
            lines.append(f"- pitch type: `{pipeline['pitch_type']}`")

        lines += ["", f"## Photos ({len(photo_rows)}) — local copies, provenance in SOURCES.json"]
        for r in photo_rows:
            lines.append(f"- `{r['file']}` — sha256 {r['sha256'][:16]}… from {r['source_url'][:90]}")
        if not photo_rows:
            lines.append("- none retrieved (recon returned no usable listing photos)")

        if facts_doc["inquire"]:
            lines += ["", "## Inquire (missing facts — never guessed)"]
            for k in facts_doc["inquire"]:
                lines.append(f"- [Inquire for quotation: {k}]")
        lines += ["", "---",
                  "*Every fact above carries its source in facts.json; every photo "
                  "carries its provenance in SOURCES.json.*"]
        return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ verifier

def verify_dossier(slug: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
    """The verify-plane half of dossier.build: the folder must contain what it
    claims — required files present, every photo hash-matched to its
    provenance row, no row without its file. Fail closed."""
    base = base_dir or dossiers_dir()
    d = os.path.join(base, slug)
    reasons: List[str] = []

    facts_path = os.path.join(d, "facts.json")
    sources_path = os.path.join(d, "SOURCES.json")
    md_path = os.path.join(d, "dossier.md")
    for path, label in ((facts_path, "facts.json"), (sources_path, "SOURCES.json"),
                        (md_path, "dossier.md")):
        if not os.path.exists(path):
            reasons.append(f"missing {label}")
    if reasons:
        return {"verified": False, "reasons": reasons}

    try:
        facts_doc = json.load(open(facts_path, encoding="utf-8"))
        sources = json.load(open(sources_path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"verified": False, "reasons": [f"unreadable dossier files: {e}"]}

    if "inquire" not in facts_doc:
        reasons.append("facts.json lacks the honesty field: inquire")
    for k in ("identity", "reputation", "site"):
        if k not in facts_doc:
            reasons.append(f"facts.json missing section: {k}")

    rows = sources.get("photos") or []
    photo_dir = os.path.join(d, "photos")
    on_disk = sorted(os.listdir(photo_dir)) if os.path.isdir(photo_dir) else []
    row_files = set()
    for r in rows:
        rel = r.get("file", "")
        row_files.add(os.path.basename(rel))
        path = os.path.join(d, rel)
        if not os.path.exists(path):
            reasons.append(f"provenance row without file: {rel}")
            continue
        if not r.get("source_url"):
            reasons.append(f"provenance row without source_url: {rel}")
        if not r.get("retrieved_at"):
            reasons.append(f"provenance row without retrieval date: {rel}")
        h = hashlib.sha256(open(path, "rb").read()).hexdigest()
        if h != r.get("sha256"):
            reasons.append(f"sha256 mismatch for {rel} — file does not match its provenance")
    for fname in on_disk:
        if fname not in row_files:
            reasons.append(f"photo without provenance row: {fname}")

    return {"verified": not reasons, "reasons": reasons}


# ------------------------------------------------------- demo photo staging

def stage_photos_into_demo(photos, demo_dir: str, slug: str,
                           max_photos: int = 9,
                           fetch_binary=None) -> Dict[str, Any]:
    """Stage the business's REAL photos into a demo build (Operator's brief:
    demos get good photos from Google/Insta with zero waiting — and never
    hotlinked). Accepts recon photo dicts ({url}|{raw_url}) or local dossier
    file paths. Downloads/copies into <demo_dir>/assets/photos/, fills the
    standard slots in order (hero, bento1, bento2, gallery1-3), and appends
    provenance rows to the demo's SOURCES.json. Stock fallback stays the
    caller's business — staged count tells it the truth."""
    slots = ["hero_bg", "bento_img1", "bento_img2",
             "gallery_1", "gallery_2", "gallery_3"]
    out_dir = os.path.join(demo_dir, "assets", "photos")
    os.makedirs(out_dir, exist_ok=True)
    fetch = fetch_binary or _default_fetch_binary
    staged: List[str] = []
    rows: List[Dict[str, Any]] = []
    from ghost.workers.recon_scout import is_venue_photo
    for p in (photos or [])[: max_photos * 2]:
        if len(staged) >= max_photos:
            break
        try:
            if isinstance(p, dict) and (p.get("path") or p.get("file")):
                src = p.get("path") or p.get("file")
                if not os.path.isfile(src):
                    continue
                with open(src, "rb") as f:
                    data = f.read()
                source_url = p.get("source_url", "dossier_photo")
            else:
                url = (p.get("url") or p.get("raw_url") if isinstance(p, dict)
                       else str(p)) or ""
                url = url.strip()
                if not url or not is_venue_photo(url):
                    continue
                data = fetch(url)
                if not data:
                    raise ValueError("empty body")
                source_url = url
            idx = len(staged) + 1
            fname = f"photo-{idx:02d}.jpg"
            with open(os.path.join(out_dir, fname), "wb") as f:
                f.write(data)
            rel = f"assets/photos/{fname}"
            staged.append(rel)
            rows.append({
                "file": rel, "type": "business_photo",
                "source": source_url if str(source_url).startswith("http")
                          else "dossier_photos",
                "sha256": hashlib.sha256(data).hexdigest(),
                "license_note": "business's own public listing photo — used in "
                                "THIS business's demo only",
                "retrieved_at": time.time(),
            })
        except Exception as e:
            logger.warning("photo staging skipped (%s): %s", str(p)[:60], str(e)[:100])
            continue
    slots_filled = {slot: staged[i] for i, slot in enumerate(slots)
                    if i < len(staged)}
    if rows:
        _append_demo_sources(demo_dir, slug, rows)
    audit("demo.photos_staged", subject=slug, staged=len(staged),
          slots=sorted(slots_filled.keys()))
    return {"staged": len(staged), "paths": staged, "slots": slots_filled}


def _append_demo_sources(demo_dir: str, slug: str, rows: List[Dict[str, Any]]) -> None:
    """Append provenance rows to the demo's SOURCES.json (the Source-Ledger
    rule: a photo without its row is a lie)."""
    path = os.path.join(demo_dir, "SOURCES.json")
    try:
        doc = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        doc.setdefault("assets", []).extend(rows)
        doc["disclosure"] = doc.get("disclosure", "") or (
            "All facts are observed evidence; missing facts are listed as "
            "[Inquire for quotation].")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, sort_keys=True)
        os.replace(path + ".tmp", path)
    except Exception as e:
        logger.warning("SOURCES.json update failed for %s: %s", slug, e)
