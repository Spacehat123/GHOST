"""Autonomy Ladder — capabilities earn levels on evidence (Roadmap §1.2).

Mechanical, not vibes: 50 consecutive evidence-backed successes at level N
makes a capability ELIGIBLE for N+1; one verified incident drops it one level.
The LLM cannot talk its way up — promotion is an operator action that the
ladder merely unlocks, and demotion is automatic.

State: one JSON file (diffable, inspectable, no DB contention). Every change
is written to the audit ledger with before/after levels.
"""
import json
import logging
import os
import time
from typing import Dict, Optional

from ghost.core.audit import audit

logger = logging.getLogger("ghost.autonomy.ladder")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
LADDER_STATE_PATH = os.environ.get("GHOST_LADDER_STATE_PATH") or os.path.join(REPO_DIR, "data", "ladder_state.json")

L0, L1, L2, L3, L4 = 0, 1, 2, 3, 4
LEVEL_NAMES = {L0: "simulate", L1: "shadow", L2: "sampled", L3: "bounded-real", L4: "autonomous"}
PROMOTE_STREAK = 50  # §1.2: 50 consecutive evidence-backed successes

# Roadmap §1.2 initial target levels — the operator's starting trust allocation.
DEFAULT_CAPABILITIES = {
    "lead_hunting": {"level": L3},
    "demo_build_deploy": {"level": L3},
    "email_outreach": {"level": L2},
    "contracts": {"level": L2},
    "payments": {"level": L2},
    "client_calls": {"level": L1},
    "retainer_autofix": {"level": L2},
}


def _default_state() -> dict:
    return {
        "capabilities": {
            name: {
                "level": cfg["level"],
                "success_streak": 0,
                "total_successes": 0,
                "total_incidents": 0,
                "eligible_for": cfg["level"],   # level the ladder has unlocked
                "updated_at": time.time(),
            }
            for name, cfg in DEFAULT_CAPABILITIES.items()
        }
    }


class AutonomyLadder:
    def __init__(self, state_path: str = LADDER_STATE_PATH):
        self.state_path = state_path
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        self._state = self._load()

    # ------------------------------------------------------------- persistence

    def _load(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                # Merge in any new capabilities added since the file was written.
                for name, cfg in DEFAULT_CAPABILITIES.items():
                    state["capabilities"].setdefault(name, {
                        "level": cfg["level"], "success_streak": 0,
                        "total_successes": 0, "total_incidents": 0,
                        "eligible_for": cfg["level"], "updated_at": time.time(),
                    })
                return state
            except Exception:
                logger.warning("ladder state unreadable — starting fresh (audit has history)")
        return _default_state()

    def _save(self) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)
        os.replace(tmp, self.state_path)

    # ----------------------------------------------------------------- queries

    def level(self, capability: str) -> int:
        cap = self._state["capabilities"].get(capability)
        return cap["level"] if cap else L0

    def eligible(self, capability: str) -> int:
        cap = self._state["capabilities"].get(capability)
        return cap["eligible_for"] if cap else L0

    def status(self) -> Dict[str, dict]:
        return {k: dict(v) for k, v in self._state["capabilities"].items()}

    def require_level(self, capability: str, minimum: int) -> Optional[dict]:
        """Gate for any code path that needs a level. Returns None if cleared,
        else a refusal dict (fail closed for unknown capabilities)."""
        lvl = self.level(capability)
        if lvl >= minimum:
            return None
        return {
            "allowed": False,
            "capability": capability,
            "level": lvl,
            "required": minimum,
            "reason": (f"'{capability}' is at L{lvl} ({LEVEL_NAMES.get(lvl)}), "
                       f"requires L{minimum}+ ({LEVEL_NAMES.get(minimum)})"),
        }

    # ---------------------------------------------------------------- mutations

    def record_success(self, capability: str, evidence: str) -> dict:
        """One evidence-backed success. Builds the promotion streak."""
        cap = self._state["capabilities"].setdefault(capability, {
            "level": L0, "success_streak": 0, "total_successes": 0,
            "total_incidents": 0, "eligible_for": L0, "updated_at": time.time(),
        })
        cap["success_streak"] += 1
        cap["total_successes"] += 1
        cap["updated_at"] = time.time()
        unlocked = False
        # Promotion UNLOCKS the next level; an operator action raises `level`.
        if (cap["success_streak"] >= PROMOTE_STREAK
                and cap["eligible_for"] < L4
                and cap["eligible_for"] == cap["level"]):
            cap["eligible_for"] = min(L4, cap["eligible_for"] + 1)
            cap["success_streak"] = 0
            unlocked = True
            audit("ladder.unlock_available", subject=capability,
                  new_eligible_level=cap["eligible_for"],
                  note="streak complete — operator can raise level")
        self._save()
        audit("ladder.success", subject=capability, evidence=str(evidence)[:200],
              streak=cap["success_streak"], level=cap["level"])
        return {"capability": capability, "streak": cap["success_streak"],
                "unlock_available": unlocked}

    def record_incident(self, capability: str, description: str,
                        verified: bool = True) -> dict:
        """A verified incident drops the level one step (never below L0),
        zeroes the streak, and pings via the ledger."""
        cap = self._state["capabilities"].setdefault(capability, {
            "level": L0, "success_streak": 0, "total_successes": 0,
            "total_incidents": 0, "eligible_for": L0, "updated_at": time.time(),
        })
        cap["total_incidents"] += 1
        old_level = cap["level"]
        cap["success_streak"] = 0
        if verified and cap["level"] > L0:
            cap["level"] -= 1
            cap["eligible_for"] = min(cap["eligible_for"], cap["level"])
        cap["updated_at"] = time.time()
        self._save()
        audit("ladder.incident", subject=capability, verified=verified,
              description=str(description)[:300],
              level_before=old_level, level_after=cap["level"],
              ping="(b) client distress" if verified else None)
        return {"capability": capability, "level_before": old_level, "level_after": cap["level"]}

    def raise_level(self, capability: str, by: str = "operator") -> dict:
        """Operator-only promotion. Refuses to exceed the unlocked ceiling —
        the ladder decides what is EARNED; only you decide what is USED."""
        cap = self._state["capabilities"].get(capability)
        if not cap:
            return {"ok": False, "error": f"unknown capability '{capability}'"}
        if cap["level"] >= cap["eligible_for"]:
            return {"ok": False,
                    "error": (f"'{capability}' eligibility is L{cap['eligible_for']}; "
                              f"earn more successes to unlock higher levels")}
        old = cap["level"]
        cap["level"] += 1
        cap["updated_at"] = time.time()
        self._save()
        audit("ladder.raised", subject=capability, by=by,
              level_before=old, level_after=cap["level"])
        return {"ok": True, "capability": capability, "level": cap["level"]}
