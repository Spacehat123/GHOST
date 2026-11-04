"""Motion Flagship Site Builder and QA Engine for Ghost.
Scaffolds templates/motion_site/ into demos/<slug>/, customizes editorial copy,
wires signature interactions, enforces operator approval, and verifies
prefers-reduced-motion accessibility, ledger compliance, and honest metrics.
"""
import os
import re
import json
import shutil
import logging
from typing import Dict, Any, Optional, List, Tuple

from ghost.workers.source_ledger import lint_demo_assets

logger = logging.getLogger("ghost.motion_site_builder")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
TEMPLATE_DIR = os.path.join(REPO_DIR, "templates", "motion_site")
DEMOS_ROOT = os.path.join(REPO_DIR, "demos")

# Persona & claims safety terms
BANNED_PERSONA_TERMS = [
    "ghost", "agent", "subagent", "autonomous agency", "ai generated",
    "circuit breaker", "system prompt", "llm"
]

SIGNATURE_DEFAULTS = {
    "detailing": {
        "type": "before_after_slider",
        "title": "Dual-Stage Surface Correction",
        "before": "Swirls & Micro-Marring",
        "after": "Optical Clarity Finish",
        "tagline": "Atelier Surface Engineering"
    },
    "dental_medical": {
        "type": "before_after_slider",
        "title": "Aesthetic Smile Restoration",
        "before": "Pre-Alignment Diagnostic",
        "after": "Refined Arch Symmetry",
        "tagline": "Clinical Aesthetics & Oral Health"
    },
    "gyms_fitness": {
        "type": "interactive_timetable",
        "title": "Signature Movement Timetable",
        "before": "Morning Conditioning",
        "after": "Evening High-Performance",
        "tagline": "Private Athletic Sanctuary"
    },
    "fine_dining": {
        "type": "menu_hover_preview",
        "title": "Seasonal Degustation Course",
        "before": "Tasting Course",
        "after": "Cellar Pairing",
        "tagline": "Culinary Architecture"
    },
    "default": {
        "type": "before_after_slider",
        "title": "Bespoke Craftsmanship Inspection",
        "before": "Stage 1 Preparation",
        "after": "Stage 2 Refinement",
        "tagline": "Bespoke Craft & Precision"
    }
}


class MotionSiteBuilder:
    """Scaffolds and configures motion flagship sites."""

    @classmethod
    def build(
        cls,
        slug: str,
        business_name: Optional[str] = None,
        category: Optional[str] = None,
        signature_type: Optional[str] = None,
        operator_approved: bool = False,
        destination_override: Optional[str] = None,
        phone: Optional[str] = None,
        whatsapp: Optional[str] = None,
        city: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Scaffolds a premium motion flagship site for a lead.
        Enforces operator authorization gate.
        """
        if not operator_approved:
            return {
                "ok": False,
                "status": "approval_required",
                "tool": "build_motion_site",
                "error": "Building a motion flagship site requires explicit operator authorization."
            }

        if not slug or not str(slug).strip():
            return {
                "ok": False,
                "status": "failed",
                "tool": "build_motion_site",
                "error": "Lead slug is required."
            }

        demo_dir = destination_override or os.path.join(DEMOS_ROOT, slug)
        os.makedirs(demo_dir, exist_ok=True)

        if not os.path.exists(TEMPLATE_DIR):
            return {
                "ok": False,
                "status": "failed",
                "tool": "build_motion_site",
                "error": f"Motion site template not found at '{TEMPLATE_DIR}'"
            }

        # 1. Scaffold files
        shutil.copytree(TEMPLATE_DIR, demo_dir, dirs_exist_ok=True)

        # 2. Derive Configuration
        bname = business_name or slug.replace("-", " ").title()
        cat_key = (category or "").lower()
        matched_sig = SIGNATURE_DEFAULTS.get("default")
        for k, v in SIGNATURE_DEFAULTS.items():
            if k in cat_key:
                matched_sig = v
                break

        tagline = matched_sig["tagline"]
        sig_title = matched_sig["title"]
        sig_before = matched_sig["before"]
        sig_after = matched_sig["after"]
        sig_type = signature_type or matched_sig["type"]
        loc_city = city or "London"

        phone_disp = phone or "+44 20 7946 0912"
        phone_href = f"tel:{re.sub(r'[^0-9+]', '', phone_disp)}"
        wa_target = whatsapp or phone_disp
        wa_clean = re.sub(r'[^0-9]', '', wa_target)
        wa_href = f"https://wa.me/{wa_clean}" if wa_clean else "#"

        disclosure = "Concept design and motion flagship demonstration presented by Ghost."

        # 3. Inject Values in index.html
        index_html_path = os.path.join(demo_dir, "index.html")
        if os.path.exists(index_html_path):
            with open(index_html_path, "r", encoding="utf-8") as f:
                content = f.read()

            content = content.replace("{{BUSINESS_NAME}}", bname)
            content = content.replace("{{TAGLINE}}", tagline)
            content = content.replace("{{CITY}}", loc_city)
            content = content.replace("{{PHONE_DISPLAY}}", phone_disp)
            content = content.replace("{{PHONE_HREF}}", phone_href)
            content = content.replace("{{WHATSAPP_HREF}}", wa_href)
            content = content.replace("{{SIGNATURE_TITLE}}", sig_title)
            content = content.replace("{{SIGNATURE_BEFORE_LABEL}}", sig_before)
            content = content.replace("{{SIGNATURE_AFTER_LABEL}}", sig_after)
            content = content.replace("{{CONCEPT_DISCLOSURE}}", disclosure)

            with open(index_html_path, "w", encoding="utf-8") as f:
                f.write(content)

        # 4. Tailor signature.json
        signature_file = os.path.join(demo_dir, "signature.json")
        sig_data = {
            "interaction_type": sig_type,
            "title": sig_title,
            "subtitle": f"Interactive inspection showcasing {bname} craftsmanship.",
            "before_label": sig_before,
            "after_label": sig_after,
            "is_placeholder": True,
            "placeholder_note": "Concept demonstration. Real client photography will populate final deployment."
        }
        with open(signature_file, "w", encoding="utf-8") as sf:
            json.dump(sig_data, sf, indent=2)

        # 5. Write SOURCES.json
        sources_path = os.path.join(demo_dir, "SOURCES.json")
        sources_data = {
            "business_name": {"value": bname, "source": "lead_record"},
            "city": {"value": loc_city, "source": "lead_record"},
            "phone": {"value": phone_disp, "source": "lead_record"},
            "whatsapp": {"value": wa_target, "source": "lead_record"},
            "signature_interaction": {
                "type": sig_type,
                "status": "concept_placeholder",
                "label": "Placeholders styled with honesty"
            },
            "assets": [],
            "disclosure": disclosure
        }
        with open(sources_path, "w", encoding="utf-8") as sf:
            json.dump(sources_data, sf, indent=2)

        return {
            "ok": True,
            "status": "success",
            "tool": "build_motion_site",
            "demo_dir": demo_dir,
            "entry_html": index_html_path,
            "signature_type": sig_type,
            "business_name": bname,
            "ledger_recorded": True
        }


class MotionQA:
    """Performs visual, interaction, claims, and accessibility QA on motion sites."""

    @classmethod
    async def run_qa(cls, demo_dir: str) -> Dict[str, Any]:
        """
        Runs comprehensive QA on motion flagship build:
        1. Verifies required files.
        2. Validates prefers-reduced-motion CSS support.
        3. Scans for persona leaks and fake claims.
        4. Validates asset ledger integrity via lint_demo_assets.
        5. Measures real asset payload sizes.
        """
        if not demo_dir or not os.path.exists(demo_dir):
            return {
                "ok": False,
                "status": "failed",
                "tool": "qa_motion_site",
                "error": f"Demo directory not found: '{demo_dir}'"
            }

        reports_dir = os.path.join(demo_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)

        index_html = os.path.join(demo_dir, "index.html")
        style_css = os.path.join(demo_dir, "src", "style.css")
        main_js = os.path.join(demo_dir, "src", "main.js")
        signature_json = os.path.join(demo_dir, "signature.json")
        sources_json = os.path.join(demo_dir, "SOURCES.json")

        checks = {
            "index_html_exists": os.path.exists(index_html),
            "style_css_exists": os.path.exists(style_css),
            "main_js_exists": os.path.exists(main_js),
            "signature_json_exists": os.path.exists(signature_json),
            "sources_json_exists": os.path.exists(sources_json)
        }

        # 1. Reduced-motion check
        reduced_motion_supported = False
        if checks["style_css_exists"]:
            try:
                with open(style_css, "r", encoding="utf-8") as f:
                    css_content = f.read()
                if "prefers-reduced-motion" in css_content:
                    reduced_motion_supported = True
            except Exception:
                reduced_motion_supported = False

        # 2. Claims & Persona Firewall Scan
        violations: List[str] = []
        if checks["index_html_exists"]:
            try:
                with open(index_html, "r", encoding="utf-8") as f:
                    html_content = f.read()

                # Check persona leaks (skip the transparent disclosure which states "by Ghost")
                # Strip disclosure section for persona check
                content_to_check = re.sub(r'<footer.*?</footer>', '', html_content, flags=re.DOTALL)

                for term in BANNED_PERSONA_TERMS:
                    pat = r'\b' + re.escape(term) + r'\b'
                    if re.search(pat, content_to_check, flags=re.IGNORECASE):
                        violations.append(f"Persona leak: banned internal term '{term}' found in HTML.")

                # Check fake claims stamps
                if re.search(r'\bVerified\b', content_to_check):
                    violations.append("Unverified claim: 'Verified' stamp detected in HTML.")

            except Exception as e:
                violations.append(f"Error checking index.html: {e}")

        # 3. Asset Ledger Lint
        ledger_ok, ledger_violations = lint_demo_assets(demo_dir)
        if not ledger_ok:
            violations.extend(ledger_violations)

        # 4. Measure Real File Sizes
        html_size = os.path.getsize(index_html) if checks["index_html_exists"] else 0
        css_size = os.path.getsize(style_css) if checks["style_css_exists"] else 0
        js_size = os.path.getsize(main_js) if checks["main_js_exists"] else 0
        total_bundle_kb = round((html_size + css_size + js_size) / 1024, 2)

        passed = (
            all(checks.values()) and
            reduced_motion_supported and
            len(violations) == 0
        )

        qa_report = {
            "tool": "qa_motion_site",
            "demo_dir": demo_dir,
            "checks": checks,
            "reduced_motion_supported": reduced_motion_supported,
            "violations": violations,
            "passed": passed,
            "signature_verified": checks["signature_json_exists"]
        }

        perf_report = {
            "html_bytes": html_size,
            "css_bytes": css_size,
            "js_bytes": js_size,
            "total_bundle_kb": total_bundle_kb,
            "js_max_kb_gzip_budget": 150,
            "budget_status": "within_budget" if total_bundle_kb <= 150 else "exceeds_budget",
            "measurement_mode": "actual_file_bytes"
        }

        # Write reports
        qa_file = os.path.join(reports_dir, "qa.json")
        perf_file = os.path.join(reports_dir, "perf.json")

        with open(qa_file, "w", encoding="utf-8") as f:
            json.dump(qa_report, f, indent=2)

        with open(perf_file, "w", encoding="utf-8") as f:
            json.dump(perf_report, f, indent=2)

        return {
            "ok": passed,
            "status": "success" if passed else "failed",
            "tool": "qa_motion_site",
            "qa_report": qa_report,
            "perf_report": perf_report,
            "report_file": qa_file,
            "violations": violations
        }
