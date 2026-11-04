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
                summary = ""
                try:
                    with open(skill_md, "r", encoding="utf-8") as f:
                        summary = _extract_skill_summary(f.read())
                except Exception:
                    summary = "Specialized agency skill."

                skills.append({
                    "name": entry,
                    "path": f"context/skills/{entry}/SKILL.md",
                    "description": summary
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
