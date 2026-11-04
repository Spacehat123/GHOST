"""Directive & Core Memory tracking system for Ghost.
Maintains persistent user directives, preferences, and facts in `configs/directives.json`
with sub-millisecond in-memory caching and compilation to a compact prompt snapshot (<= 250 tokens).
Delegates core persistence and contradiction management to CoreMemoryStore.
"""
import os
import json
import time
import logging
from typing import Dict, Any, List, Optional
from ghost.core.memory import CoreMemoryStore, DIRECTIVES_PATH

logger = logging.getLogger("ghost.follow")

class DirectiveManager:
    """
    Manages live directives, instructions, and user facts.
    Caches the parsed directives in memory and re-reads only when file mtime changes.
    """

    _store = CoreMemoryStore(DIRECTIVES_PATH)

    @classmethod
    def get_default_data(cls) -> Dict[str, Any]:
        """Load default data seeded from user_profile.json if available."""
        return cls._store._get_initial_data()

    @classmethod
    def load(cls, force_reload: bool = False) -> Dict[str, Any]:
        """Load directives, using in-memory cache if file has not changed."""
        return cls._store.load(force_reload=force_reload)

    @classmethod
    def save(cls, data: Dict[str, Any]):
        """Persist data to DIRECTIVES_PATH and update in-memory cache."""
        cls._store.save(data)

    @classmethod
    def get_snapshot(cls) -> str:
        """Returns the pre-baked compact snapshot string (<= 250 tokens)."""
        return cls._store.get_snapshot()

    @classmethod
    def format_for_system_prompt(cls) -> str:
        """Alias for get_snapshot for backwards-compatibility."""
        return cls._store.get_snapshot()

    @classmethod
    def add_directive(cls, text: str, category: str = "directives") -> Dict[str, Any]:
        """
        Add an instruction, preference, or fact.
        category: 'directives' or 'facts'
        """
        mem_type = "directive" if category == "directives" else "fact"
        res = cls._store.add_memory(
            content=text,
            mem_type=mem_type,
            source="user_confirmed",
            importance=0.9 if mem_type == "directive" else 0.6
        )
        item = res.get("item", {})
        return {
            "success": res.get("success", True),
            "id": item.get("id"),
            "action": res.get("action", "added"),
            "category": category,
            "text": text,
            "conflict_detected": res.get("conflict_detected", False),
            "conflict_with_id": res.get("conflict_with_id")
        }

    @classmethod
    def remove_directive(cls, identifier: str) -> Dict[str, Any]:
        """Remove a directive or fact by ID or matching text substring."""
        target = identifier.strip().lower()
        data = cls.load()
        removed = []

        memories = cls._store._extract_memories(data)
        remaining = []
        for m in memories:
            if m.id.lower() == target or target in m.content.lower():
                removed.append(m.to_dict())
            else:
                remaining.append(m)

        if removed:
            data["memories"] = [m.to_dict() for m in remaining]
            data["directives"] = [{"id": m.id, "text": m.content, "created_at": m.created_at} for m in remaining if m.type == "directive"]
            data["facts"] = [{"id": m.id, "text": m.content, "created_at": m.created_at} for m in remaining if m.type != "directive"]
            cls.save(data)
            return {"success": True, "removed": removed}
        return {"success": False, "error": f"No directive or fact found matching '{identifier}'"}

    @classmethod
    def list_all(cls) -> Dict[str, Any]:
        """Return formatted structure of all active directives and facts."""
        return cls.load()
