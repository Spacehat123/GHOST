"""The Throat-Cut Handle — Ghost's master halt switch (Roadmap §0.5).

One flag, checked by every outbound-capable worker. When tripped:
- outbound channels (email, WhatsApp, deploy-to-client, payments) freeze in <5s
- in-flight jobs finish their current step then stop at the next yield point
- everything is logged with a reason and an operator trail

Implementation: a sentinel file + a row in the audit ledger. File-based so it
survives process crashes and can be tripped from a shell with `touch`, from
Python via `trip()`, or (later) from the GUI. No DB lock contention in the
hot path — senders call `assert_not_halted()` which is one stat() call.
"""
import json
import os
import time
import uuid
from typing import Optional

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
HALT_FILE = os.environ.get("GHOST_HALT_FILE") or os.path.join(REPO_DIR, "data", "HALTED")
HALT_META = os.environ.get("GHOST_HALT_META") or HALT_FILE + ".json"

REASON_OPERATOR = "operator"
REASON_HEARTBEAT = "heartbeat_lost"   # watchdog teeth: sick agent must not talk to customers
REASON_REPUTATION = "domain_reputation"
REASON_COMPLAINT = "client_complaint"


class HaltActive(RuntimeError):
    """Raised by assert_not_halted() so callers fail closed, never open."""


def is_halted() -> bool:
    return os.path.exists(HALT_FILE)


def halt_state() -> Optional[dict]:
    """Returns halt metadata if halted, else None. Never raises."""
    if not is_halted():
        return None
    try:
        with open(HALT_META, "r", encoding="utf-8") as f:
            meta = json.load(f)
        meta["halted_for_s"] = round(time.time() - meta.get("tripped_at", time.time()), 1)
        return meta
    except Exception:
        return {"reason": "unknown", "tripped_at": None, "by": None}


def trip(reason: str = REASON_OPERATOR, by: str = "operator") -> dict:
    """Trip the halt. Idempotent; records who/why/when."""
    os.makedirs(os.path.dirname(HALT_FILE), exist_ok=True)
    meta = {
        "halt_id": str(uuid.uuid4()),
        "reason": reason,
        "by": by,
        "tripped_at": time.time(),
        "tripped_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    }
    with open(HALT_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    # Sentinel last: existence of HALT_FILE is the switch; meta is the trail.
    with open(HALT_FILE, "w", encoding="utf-8") as f:
        f.write(meta["halt_id"])
    try:
        from ghost.core.audit import audit
        audit("halt.tripped", **meta)
    except Exception:
        pass
    return meta


def resume(by: str = "operator") -> Optional[dict]:
    """Clear the halt. Records the resume in the audit ledger."""
    prev = halt_state()
    for path in (HALT_FILE, HALT_META):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    if prev:
        try:
            from ghost.core.audit import audit
            audit("halt.resumed", previous=prev.get("halt_id"), by=by)
        except Exception:
            pass
    return prev


def assert_not_halted(action: str = "outbound_action") -> None:
    """Guard for every outbound-capable code path. Fails CLOSED."""
    if is_halted():
        state = halt_state() or {}
        raise HaltActive(
            f"HALTED — refusing '{action}' "
            f"(reason={state.get('reason')}, by={state.get('by')}). "
            "Resume with ghost.core.halt.resume()."
        )
