"""Directive & Core Memory tracking system for Ghost.
Maintains persistent user directives, preferences, and facts in `configs/directives.json`
with sub-millisecond in-memory caching and compilation to a compact prompt snapshot.
"""
import os
import json
import time
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("ghost.follow")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
DIRECTIVES_PATH = os.environ.get("GHOST_DIRECTIVES_CONFIG", os.path.join(REPO_DIR, "configs/directives.json"))
USER_PROFILE_PATH = os.environ.get("GHOST_USER_PROFILE", os.path.join(REPO_DIR, "data/user_profile.json"))

class DirectiveManager:
    """
    Manages live directives, instructions, and user facts.
    Caches the parsed directives in memory and re-reads only when file mtime changes.
    """

    _cached_data: Optional[Dict[str, Any]] = None
    _last_mtime: float = 0.0
    _compiled_snapshot: str = ""

    @classmethod
    def get_default_data(cls) -> Dict[str, Any]:
        """Load default data seeded from user_profile.json if available."""
        default_data = {
            "version": 1,
            "user_name": "Pranc",
            "environment": {
                "os": "Arch Linux",
                "wm": "Hyprland",
                "browser": "Brave (port 9222)"
            },
            "directives": [
                {"id": "d1", "text": "Deliver spoken replies in 1-3 crisp conversational sentences max.", "created_at": time.time()},
                {"id": "d2", "text": "Zero pleasantries, preamble, or fluff. Jump straight to the point.", "created_at": time.time()},
                {"id": "d3", "text": "Never output markdown asterisks, backticks, or code blocks in spoken dialogue.", "created_at": time.time()}
            ],
            "preferences": {
                "concise_tone": True,
                "dark_mode": True,
                "primary_reverse_engineering_tool": "REA"
            },
            "facts": [
                {"id": "f1", "text": "User name is Pranc.", "created_at": time.time()},
                {"id": "f2", "text": "Primary workspace is /home/pranc/projects.", "created_at": time.time()}
            ]
        }

        # Try to pull user facts if user_profile.json exists
        if os.path.exists(USER_PROFILE_PATH):
            try:
                with open(USER_PROFILE_PATH, "r", encoding="utf-8") as f:
                    prof = json.load(f)
                    if "user_name" in prof:
                        default_data["user_name"] = prof["user_name"]
                    if "environment" in prof:
                        default_data["environment"].update(prof["environment"])
                    if "preferences" in prof:
                        default_data["preferences"].update(prof["preferences"])
                    if "facts" in prof and isinstance(prof["facts"], list):
                        for idx, fact_text in enumerate(prof["facts"]):
                            fid = f"f_init_{idx+1}"
                            if not any(f["text"] == fact_text for f in default_data["facts"]):
                                default_data["facts"].append({"id": fid, "text": fact_text, "created_at": time.time()})
            except Exception as e:
                logger.warning(f"Could not seed from user_profile.json: {e}")

        return default_data

    @classmethod
    def load(cls, force_reload: bool = False) -> Dict[str, Any]:
        """
        Load directives, using in-memory cache if file has not changed.
        """
        if not os.path.exists(DIRECTIVES_PATH):
            data = cls.get_default_data()
            cls.save(data)
            return data

        try:
            mtime = os.path.getmtime(DIRECTIVES_PATH)
            if not force_reload and cls._cached_data is not None and mtime <= cls._last_mtime:
                return cls._cached_data

            with open(DIRECTIVES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            cls._cached_data = data
            cls._last_mtime = mtime
            cls._recompile_snapshot(data)
            return data

        except Exception as e:
            logger.error(f"Error loading directives from {DIRECTIVES_PATH}: {e}")
            if cls._cached_data is not None:
                return cls._cached_data
            data = cls.get_default_data()
            return data

    @classmethod
    def save(cls, data: Dict[str, Any]):
        """Persist data to DIRECTIVES_PATH and update in-memory cache."""
        try:
            os.makedirs(os.path.dirname(DIRECTIVES_PATH), exist_ok=True)
            with open(DIRECTIVES_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            cls._cached_data = data
            cls._last_mtime = os.path.getmtime(DIRECTIVES_PATH)
            cls._recompile_snapshot(data)
            logger.info("Directives successfully saved and cache recompiled.")
        except Exception as e:
            logger.error(f"Error saving directives: {e}", exc_info=True)
            raise

    @classmethod
    def _recompile_snapshot(cls, data: Dict[str, Any]):
        """
        Compiles directives into a compact snapshot string (< 250 tokens).
        """
        user_name = data.get("user_name", "Pranc")
        env_items = [f"{k}: {v}" for k, v in data.get("environment", {}).items()]
        env_str = ", ".join(env_items)

        directives = [d.get("text", "") for d in data.get("directives", []) if d.get("text")]
        facts = [f.get("text", "") for f in data.get("facts", []) if f.get("text")]

        lines = [
            f"[CORE MEMORY & ACTIVE DIRECTIVES]",
            f"User: {user_name} | Environment: {env_str}",
        ]
        if directives:
            lines.append("Behavioral Directives:")
            for d in directives:
                lines.append(f"- {d}")
        if facts:
            lines.append("Known Facts:")
            for f in facts:
                lines.append(f"- {f}")

        cls._compiled_snapshot = "\n".join(lines)

    @classmethod
    def get_snapshot(cls) -> str:
        """Returns the pre-baked compact snapshot string."""
        if cls._cached_data is None:
            cls.load()
        return cls._compiled_snapshot

    @classmethod
    def format_for_system_prompt(cls) -> str:
        """Alias for get_snapshot for backwards-compatibility."""
        return cls.get_snapshot()

    @classmethod
    def add_directive(cls, text: str, category: str = "directives") -> Dict[str, Any]:
        """
        Add an instruction, preference, or fact.
        category: 'directives' or 'facts'
        """
        text_clean = text.strip()
        data = cls.load()

        if category not in ["directives", "facts"]:
            category = "directives"

        items = data.setdefault(category, [])
        prefix = "d" if category == "directives" else "f"
        new_id = f"{prefix}_{int(time.time() * 1000) % 1000000}"

        # Prevent exact duplicates
        for item in items:
            if item.get("text", "").lower() == text_clean.lower():
                return {"success": True, "id": item.get("id"), "action": "already_exists", "text": text_clean}

        new_entry = {
            "id": new_id,
            "text": text_clean,
            "created_at": time.time()
        }
        items.append(new_entry)
        cls.save(data)
        return {"success": True, "id": new_id, "action": "added", "category": category, "text": text_clean}

    @classmethod
    def remove_directive(cls, identifier: str) -> Dict[str, Any]:
        """
        Remove a directive or fact by ID or matching text substring.
        """
        target = identifier.strip().lower()
        data = cls.load()
        removed = []

        for cat in ["directives", "facts"]:
            items = data.get(cat, [])
            remaining = []
            for item in items:
                item_id = item.get("id", "").lower()
                item_text = item.get("text", "").lower()
                if item_id == target or target in item_text:
                    removed.append(item)
                else:
                    remaining.append(item)
            data[cat] = remaining

        if removed:
            cls.save(data)
            return {"success": True, "removed": removed}
        return {"success": False, "error": f"No directive or fact found matching '{identifier}'"}

    @classmethod
    def list_all(cls) -> Dict[str, Any]:
        """Return formatted structure of all active directives and facts."""
        return cls.load()
