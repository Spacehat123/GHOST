"""Skill Management Engine for Ghost Autonomous Agency.
Enforces non-negotiable architectural invariant:
Skills are modular instructions living in context/skills/<name>/SKILL.md.
The system prompt only lists skill names and summaries; full skill texts
are loaded strictly on demand via get_skill(name) to preserve tokens.
"""
import os
import re
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("ghost.skills")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
SKILLS_DIR = os.path.join(REPO_DIR, "context", "skills")


def _extract_skill_summary(content: str) -> str:
    """Extract first informative paragraph or subtitle from SKILL.md."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("#"):
            continue
        if len(line) > 15:
            return line[:160]
    return "Specialized workflow instructions."


def list_skills() -> List[Dict[str, str]]:
    """List all available skills in context/skills/."""
    skills = []
    if not os.path.exists(SKILLS_DIR) or not os.path.isdir(SKILLS_DIR):
        return skills

    for entry in sorted(os.listdir(SKILLS_DIR)):
        entry_path = os.path.join(SKILLS_DIR, entry)
        if os.path.isdir(entry_path):
            skill_md = os.path.join(entry_path, "SKILL.md")
            if os.path.exists(skill_md) and os.path.isfile(skill_md):
                cap: Dict[str, Any] = {}
                summary = ""
                try:
                    with open(skill_md, "r", encoding="utf-8") as f:
                        content = f.read()
                    summary = _extract_skill_summary(content)
                    cap = _parse_capability(content)
                except Exception:
                    summary = "Specialized agency skill."

                skills.append({
                    "name": entry,
                    "path": f"context/skills/{entry}/SKILL.md",
                    "description": cap.get("summary") or summary,
                    "triggers": cap.get("triggers", []),
                })

    return skills


def get_skill(name: str) -> Dict[str, Any]:
    """Load and return the full markdown text and metadata of a skill by name."""
    clean_name = str(name).strip().lower()
    skill_dir = os.path.join(SKILLS_DIR, clean_name)
    skill_md = os.path.join(skill_dir, "SKILL.md")

    if not os.path.exists(skill_md) or not os.path.isfile(skill_md):
        available = [s["name"] for s in list_skills()]
        return {
            "ok": False,
            "status": "not_found",
            "tool": "get_skill",
            "error": f"Skill '{name}' not found. Available skills: {available}",
            "available_skills": available
        }

    try:
        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read()

        examples_json = os.path.join(skill_dir, "stages.example.json")
        examples = None
        if os.path.exists(examples_json):
            try:
                import json
                with open(examples_json, "r", encoding="utf-8") as ef:
                    examples = json.load(ef)
            except Exception:
                pass

        return {
            "ok": True,
            "status": "success",
            "tool": "get_skill",
            "name": clean_name,
            "path": f"context/skills/{clean_name}/SKILL.md",
            "content": content,
            "examples": examples
        }
    except Exception as e:
        logger.error(f"Failed to read skill '{clean_name}': {e}")
        return {
            "ok": False,
            "status": "failed",
            "tool": "get_skill",
            "error": str(e)
        }


# ===========================================================================
# CAPABILITY INJECTION (Operator's directive, Nov 2026): "we need a system to
# tell Ghost what to use." Skills sitting in files don't get used — the model
# must be HANDED the right capability at planning time. Deterministic, three
# pieces: a parsed registry (headers above), a relevance matcher, and a
# directive block that rides mission planning.
# ===========================================================================

import re as _re


def _parse_capability(content: str) -> Dict[str, Any]:
    """Parse the <!-- capability ... --> header block."""
    cap: Dict[str, Any] = {}
    m = _re.search(r"<!--\s*capability(.*?)-->", content, _re.S)
    if not m:
        return cap
    for line in m.group(1).strip().splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        cap[key.strip()] = val.strip()
    if "triggers" in cap:
        cap["triggers"] = [t.strip().lower() for t in cap["triggers"].split(",") if t.strip()]
    if "applies_to" in cap:
        cap["applies_to"] = [t.strip() for t in cap["applies_to"].split(",") if t.strip()]
    return cap


def capabilities_index() -> List[Dict[str, Any]]:
    """The machine-readable registry: every skill with its summary, triggers
    and applies_to. The index is the model's map of its own toolbox."""
    index = []
    for s in list_skills():
        try:
            with open(os.path.join(REPO_DIR, s["path"]), "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        cap = _parse_capability(content)
        index.append({
            "name": s["name"],
            "summary": cap.get("summary") or s["description"],
            "triggers": cap.get("triggers", []),
            "applies_to": cap.get("applies_to", []),
        })
    return index


def relevant_skills(task_text: str, min_score: int = 1) -> List[Dict[str, Any]]:
    """Deterministic match: which capabilities does THIS task need? Scored by
    trigger keyword hits in the task text. No model involved."""
    text = str(task_text or "").lower()
    if not text.strip():
        return []
    scored = []
    for cap in capabilities_index():
        hits = [t for t in cap["triggers"] if t and t in text]
        if len(hits) >= min_score:
            scored.append({"name": cap["name"], "score": len(hits),
                           "hits": hits, "summary": cap["summary"]})
    scored.sort(key=lambda s: (-s["score"], s["name"]))
    return scored


# ===========================================================================
# EXECUTION ROUTES (the other half of the operator's directive): knowing the
# STYLE is not enough — the model must know WHICH MACHINE builds what.
# Hand-writing artifacts via system_run bypasses recon/photos/QA/deploy gates.
# ===========================================================================

EXECUTION_ROUTES: List[Dict[str, Any]] = [
    {"applies_to": "build_demo",
     "match": ("demo", "concept site", "prospect site", "pitch site"),
     "route": ("EXECUTION ROUTE (binding): demos are built BY THE DEMO STATE "
               "MACHINE via build_luxury_demo — never hand-written HTML through "
               "system_run/cat. The machine performs Google/Maps recon and REAL "
               "photo staging (assets/photos), writes DESIGN_BRIEF.md with the "
               "house style + vendored svg.js, runs CDP visual QA at two "
               "viewports, and gates the deploy. Hand-building skips photos, "
               "QA and deploy gates — forbidden.")},
    {"applies_to": "build_website",
     "match": ("client website", "production site", "build the website",
               "their website", "new website"),
     "route": ("EXECUTION ROUTE (binding): client websites go through the "
               "website flow — build_seo_pack (schema/llms.txt/6-move "
               "checklist), the client-photos gate (photos_received), the "
               "editorial + svg_decor + motion skills, and verify_ship_gate "
               "before launch. Production HTML is never hand-written.")},
    {"applies_to": "fix_site",
     "match": ("fix the site", "site broken", "customer report", "broken link",
               "repair", "500", "down"),
     "route": ("EXECUTION ROUTE (binding): site repairs go through the sidecar "
               "fixer — diagnose_and_fix stages a verified patch; the operator "
               "applies it. Never hand-edit live client files directly.")},
]


def route_for(task_text: str) -> str:
    """The binding execution route for a goal, or '' when no route matches."""
    text = str(task_text or "").lower()
    for r in EXECUTION_ROUTES:
        if any(m in text for m in r["match"]):
            return r["route"]
    return ""


def capability_directive(task_text: str, max_skills: int = 4) -> str:
    """The injectable block for mission planning: which skills this task MUST
    load (get_skill) before proposing a plan. Empty string when nothing
    matches — the directive never invents relevance."""
    matches = relevant_skills(task_text)[:max_skills]
    route = route_for(task_text)
    if not matches and not route:
        return ""
    parts = []
    if matches:
        lines = [f"- get_skill('{m['name']}') — {m['summary']} "
                 f"(matched: {', '.join(m['hits'][:3])})" for m in matches]
        parts.append("CAPABILITY DIRECTIVE — load these BEFORE planning; they "
                     "are binding for this task:\n" + "\n".join(lines))
    if route:
        parts.append(route)
    return "\n\n".join(parts)


def capability_index_block() -> str:
    """The compact index for the system prompt: the model's standing map of
    its own toolbox, generated from the registry (never hand-maintained)."""
    caps = capabilities_index()
    if not caps:
        return ""
    lines = [f"- {c['name']}: {c['summary']} (when: {', '.join(c['triggers'][:6]) or 'any'})"
             for c in caps]
    return ("CAPABILITY INDEX — your own toolbox. Before planning ANY mission, "
            "match the goal against this index and get_skill() every match; "
            "a plan that ignores a matched capability is an incomplete plan:\n"
            + "\n".join(lines))
