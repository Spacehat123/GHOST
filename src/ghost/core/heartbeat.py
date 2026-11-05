"""Watchdog Heartbeat — the teeth behind heartbeat_age_s (Roadmap §3.3).

The policy engine refuses outbound when the heartbeat goes stale ("a sick
agent must not talk to customers"). P3 hard-coded the age to 1.0 so the check
could never trip — this module makes it real:

- `beat()` writes a timestamp marker; the scheduler calls it every cycle and
  the server loop beats every HEARTBEAT_INTERVAL_S
- `age_s()` reads the marker; missing/corrupt marker = infinite age (fail closed)
- `enforce_stale()` trips the halt switch when the heartbeat is stale, so ALL
  outbound freezes even if the poller itself is the sick part
"""
import json
import os
import time
from typing import Optional

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
HEARTBEAT_PATH = os.environ.get("GHOST_HEARTBEAT_PATH") or os.path.join(REPO_DIR, "data", "heartbeat.json")

HEARTBEAT_INTERVAL_S = 60.0     # §3.3: beat every 60s
STALE_AFTER_S = 180.0           # 3 missed beats = sick


def beat(note: str = "scheduler") -> float:
    os.makedirs(os.path.dirname(HEARTBEAT_PATH), exist_ok=True)
    now = time.time()
    tmp = HEARTBEAT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts": now, "note": note}, f)
    os.replace(tmp, HEARTBEAT_PATH)
    return now


def age_s() -> float:
    """Seconds since the last beat. Missing marker -> huge age (fail closed)."""
    try:
        with open(HEARTBEAT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return max(0.0, time.time() - float(data.get("ts", 0)))
    except Exception:
        return float("inf")


def is_stale(max_age_s: float = STALE_AFTER_S) -> bool:
    age = age_s()
    return age > max_age_s  # inf > anything -> True when never beaten


def enforce_stale(max_age_s: float = STALE_AFTER_S) -> Optional[dict]:
    """If stale and not already halted, trip the halt (reason=heartbeat_lost).
    Returns the halt metadata if it tripped now, else None. Idempotent."""
    from ghost.core import halt as halt_mod
    if not is_stale(max_age_s):
        return None
    if halt_mod.is_halted():
        return None
    meta = halt_mod.trip(reason=halt_mod.REASON_HEARTBEAT, by="watchdog")
    from ghost.core.audit import audit
    audit("watchdog.heartbeat_stale", age_s=age_s(), halted=True,
          ping="(c) unverifiable: heartbeat lost — outbound frozen, operator needed")
    return meta
