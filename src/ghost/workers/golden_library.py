"""Golden Demo Library — the §2 Stage 2 compounding asset (P5 wiring).

Per-vertical flawless templates: each vertical maps to ONE demo directory
that has PASSED real screenshot QA. Building a new demo starts from the
golden template for that vertical instead of a generic scaffold, and every
finished client site (with permission) feeds back into the golden set —
the portfolio flywheel (§15.2).

Demo expiry (§2 Stage 2): unreplied demos auto-expire at 14 days — cost
control plus honest scarcity. Expiry MARKS the demo (marker file + audit +
Vercel project recorded for teardown); it never touches a PAID client's site.
"""
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from ghost.core.audit import audit

logger = logging.getLogger("ghost.golden")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
DEMOS_DIR = os.environ.get("GHOST_DEMOS_DIR") or os.path.join(REPO_DIR, "demos")
LIBRARY_PATH = os.environ.get("GHOST_GOLDEN_LIBRARY_PATH") or os.path.join(REPO_DIR, "configs", "golden_library.json")
DEMO_EXPIRY_DAYS = 14

VERTICALS = ["restaurant", "clinic", "detailing", "boutique", "real_estate", "roastery", "default"]


class GoldenLibrary:
    def __init__(self, library_path: str = LIBRARY_PATH, demos_dir: str = DEMOS_DIR):
        self.library_path = library_path
        self.demos_dir = demos_dir
        self._library: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.library_path):
            try:
                with open(self.library_path, "r", encoding="utf-8") as f:
                    self._library = json.load(f)
            except Exception:
                self._library = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.library_path), exist_ok=True)
        tmp = self.library_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._library, f, indent=2)
        os.replace(tmp, self.library_path)

    def register(self, vertical: str, demo_dir: str) -> Dict[str, Any]:
        """Register a demo as the golden template for a vertical. REQUIRES
        passed screenshot QA — an unverified demo cannot become golden (the
        portfolio flywheel only compounds quality, never unverified work)."""
        qa_path = os.path.join(demo_dir, "reports", "qa.json")
        if not os.path.exists(qa_path):
            return {"ok": False, "error": "no QA report — run three_qa first"}
        with open(qa_path, "r", encoding="utf-8") as f:
            qa = json.load(f)
        if not qa.get("passed") or int(qa.get("screenshots_captured", 0) or 0) <= 0:
            return {"ok": False, "error": "QA not passed with screenshots — cannot be golden"}
        vertical = (vertical or "default").lower()
        self._library[vertical] = {
            "demo_dir": os.path.abspath(demo_dir),
            "registered_at": time.time(),
            "qa_evidence": {"screenshots": qa["screenshots_captured"],
                            "fps": (qa.get("visual_checks") or {})
                            and True,  # details live in qa.json; cited, not copied
                            "decision": "passed"},
        }
        self._save()
        audit("golden.registered", vertical=vertical, demo_dir=demo_dir,
              screenshots=qa["screenshots_captured"])
        return {"ok": True, "vertical": vertical, "demo_dir": os.path.abspath(demo_dir)}

    def pick(self, vertical: str) -> Optional[str]:
        """The golden demo dir for a vertical, or None (honest) — callers
        fall back to the generic template, never to an unverified 'golden'."""
        entry = self._library.get((vertical or "default").lower())
        if entry and os.path.isdir(entry["demo_dir"]):
            return entry["demo_dir"]
        return self._library.get("default", {}).get("demo_dir")

    def status(self) -> Dict[str, Any]:
        return {v: {"demo_dir": e["demo_dir"],
                    "exists": os.path.isdir(e["demo_dir"])}
                for v, e in self._library.items()}

    # ------------------------------------------------------------ demo expiry

    def expire_stale_demos(self, max_age_days: int = DEMO_EXPIRY_DAYS) -> List[Dict[str, Any]]:
        """Mark unreplied demos older than the window. A demo is 'replied'
        when its CRM lead reached 'replied' or later. Expired demos get a
        marker + audit event with the Vercel project name for teardown —
        file deletion is the operator's one-command call, not silent."""
        from ghost.crm.db import CRMDatabase
        try:
            crm = CRMDatabase.get_instance()
        except Exception:
            crm = None
        cutoff = time.time() - max_age_days * 86400
        expired = []
        if not os.path.isdir(self.demos_dir):
            return expired
        for slug in sorted(os.listdir(self.demos_dir)):
            demo_dir = os.path.join(self.demos_dir, slug)
            if not os.path.isdir(demo_dir) or slug.endswith("-expired"):
                continue
            if os.path.exists(os.path.join(demo_dir, "DEMO_EXPIRED")):
                continue
            # Client-facing deployments are exempt: any lead that replied.
            if crm is not None:
                try:
                    lead = crm.get_lead(slug)
                    if lead and lead.get("status") in ("replied", "agreed", "contract_sent",
                                                       "contract_drafted", "deposit_received",
                                                       "won"):
                        continue
                except Exception:
                    pass
            mtime = os.path.getmtime(demo_dir)
            if mtime > cutoff:
                continue
            project_name = f"{slug}-concept"
            with open(os.path.join(demo_dir, "DEMO_EXPIRED"), "w") as f:
                f.write(time.strftime("%Y-%m-%dT%H:%M:%S"))
            audit("demo.expired", subject=slug, project_name=project_name,
                  age_days=round((time.time() - mtime) / 86400, 1),
                  note="marked; Vercel teardown: vercel projects rm " + project_name)
            expired.append({"slug": slug, "project_name": project_name})
        return expired
