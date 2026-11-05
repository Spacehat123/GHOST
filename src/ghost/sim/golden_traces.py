"""SIM — golden traces (T6c): recorded legacy outcomes replayed through the
proposal path; same inputs -> equivalent observable outcomes.

The sim proves the pipeline HANDLES a proposal; traces prove the strangler
didn't CHANGE what a capability does. A trace is the legacy call's observable
result, normalized (volatile fields stripped, lists sorted) and committed as
a fixture; the test replays the same inputs through the dispatcher and
compares. A trace that changes without a conscious commit fails the suite.
"""
import json
import os
from typing import Any, Dict, List, Optional

# fields that legitimately differ run-to-run (timestamps, ids, provider noise)
VOLATILE_KEYS = {"url", "output", "raw", "message_id", "elapsed_ms", "ts", "at",
                 "id", "halt_id", "tripped_at", "tripped_at_iso", "created_at",
                 "updated_at", "decision_id", "internal_preview_path",
                 "config_path", "detail"}

TRACES_DIR = os.environ.get("GHOST_GOLDEN_TRACES_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "golden_traces")


def normalize(result: Any, depth: int = 0) -> Any:
    """Strip volatile fields recursively; sort lists for order-insensitivity."""
    if depth > 6:
        return "..."
    if isinstance(result, dict):
        return {k: normalize(v, depth + 1) for k, v in sorted(result.items())
                if k not in VOLATILE_KEYS}
    if isinstance(result, (list, tuple)):
        items = [normalize(v, depth + 1) for v in result]
        try:
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))
        except (TypeError, ValueError):
            return items
    return result


def equivalent(legacy: Any, proposal: Any) -> bool:
    return normalize(legacy) == normalize(proposal)


def trace_path(name: str) -> str:
    os.makedirs(TRACES_DIR, exist_ok=True)
    return os.path.join(TRACES_DIR, f"{name}.json")


def record(name: str, tool: str, args: Dict[str, Any], legacy_observable: Any) -> str:
    """Commit a golden trace (the legacy call's observable shape)."""
    fixture = {"tool": tool, "args": args, "legacy_observable": normalize(legacy_observable)}
    with open(trace_path(name), "w", encoding="utf-8") as f:
        json.dump(fixture, f, indent=2, sort_keys=True)
    return trace_path(name)


def load(name: str) -> Optional[Dict[str, Any]]:
    path = trace_path(name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
