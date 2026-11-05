"""Model Routing + Per-Intent Token Budgets (Roadmap §5).

'Cheap-first model routing: classification/qualification/routing on a local
or small model; only copywriting and codegen hit the big model.' This module
is the deterministic router the policy engine's grants feed:

- `route(intent)` -> the model id for a task class, from the operator's knob
  file (configs/model_routing.json). Falls back DOWN to cheaper models when
  the local runtime is down — never UP without an explicit knob.
- `check_local_model()` probes Ollama honestly (not_configured is a state,
  not an error).
- `token_budget_for(intent)` — per-intent daily budgets from the same knob
  file; `budget_remaining(intent)` answers against the day's ledger spend
  (budget.overrun events), so the policy engine can throttle expensive work.

No LLM calls here: routing is a table lookup, not a decision.
"""
import json
import logging
import os
import time
import urllib.request
from typing import Any, Dict, Optional

from ghost.core.audit import audit

logger = logging.getLogger("ghost.model_routing")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
ROUTING_PATH = os.environ.get("GHOST_MODEL_ROUTING_PATH") or os.path.join(REPO_DIR, "configs", "model_routing.json")

DEFAULT_ROUTING: Dict[str, Any] = {
    "local_ollama_url": "http://127.0.0.1:11434",
    "local_model": "qwen2.5:7b",
    "routes": {
        # §5: classification/qualification/routing cheap-first
        "classify": {"primary": "local:qwen2.5:7b", "fallback": "gemini-2.0-flash"},
        "qualify": {"primary": "local:qwen2.5:7b", "fallback": "gemini-2.0-flash"},
        "reply_triage": {"primary": "local:qwen2.5:7b", "fallback": "gemini-2.0-flash"},
        # copy + code earn the big model
        "copywriting": {"primary": "gemini-1.5-pro", "fallback": "gpt-4o"},
        "codegen": {"primary": "gemini-1.5-pro", "fallback": "gpt-4o"},
        # voice fluency: small and fast
        "voice_fluency": {"primary": "gemini-2.0-flash", "fallback": "gpt-4o-mini"},
    },
    "daily_token_budgets": {   # per intent; None = unlimited (operator knob)
        "classify": 200000, "qualify": 200000, "reply_triage": 100000,
        "copywriting": 150000, "codegen": 400000, "voice_fluency": 100000,
    },
}


def load_routing() -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_ROUTING))
    if os.path.exists(ROUTING_PATH):
        try:
            with open(ROUTING_PATH, "r", encoding="utf-8") as f:
                overrides = json.load(f)
            cfg["routes"].update(overrides.get("routes", {}))
            cfg["daily_token_budgets"].update(overrides.get("daily_token_budgets", {}))
            cfg["local_model"] = overrides.get("local_model", cfg["local_model"])
            cfg["local_ollama_url"] = overrides.get("local_ollama_url", cfg["local_ollama_url"])
        except Exception as e:
            logger.warning("model routing config unreadable (%s) — defaults", e)
    return cfg


def check_local_model(base_url: Optional[str] = None, timeout: float = 1.5) -> Dict[str, Any]:
    """Probe the Ollama runtime. Honest states: available | not_configured | error."""
    url = (base_url or os.environ.get("OLLAMA_BASE_URL")
           or load_routing()["local_ollama_url"]).rstrip("/")
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        models = [m.get("name", "") for m in data.get("models", [])]
        audit("models.local_probed", available=True, models=models[:10])
        return {"status": "available", "models": models, "url": url}
    except Exception as e:
        state = "not_configured" if "ConnectionRefused" in type(e).__name__ or "URLError" in type(e).__name__ else "error"
        audit("models.local_probed", available=False, state=state)
        return {"status": state, "models": [], "url": url, "error": str(e)[:150]}


def route(intent: str, force_probe: bool = False) -> Dict[str, Any]:
    """Resolve the model for an intent. If the route's primary is local and
    Ollama is down, fall back DOWN to the small cloud model — cheap-first
    holds even in degraded mode."""
    cfg = load_routing()
    entry = cfg["routes"].get(intent) or {"primary": "gemini-2.0-flash", "fallback": "gpt-4o-mini"}
    primary = entry["primary"]
    if primary.startswith("local:"):
        probe = check_local_model(cfg["local_ollama_url"]) if force_probe else _cached_probe(cfg["local_ollama_url"])
        if probe["status"] == "available" and any(
                primary.split("local:", 1)[1] in m for m in probe["models"]):
            audit("models.routed", intent=intent, model=primary)
            return {"model": primary, "backend": "ollama", "intent": intent}
        model = entry["fallback"]
        audit("models.routed", intent=intent, model=model, degraded=True,
              reason="local_unavailable")
        return {"model": model, "backend": "cloud", "intent": intent, "degraded": True}
    audit("models.routed", intent=intent, model=primary)
    return {"model": primary, "backend": "cloud", "intent": intent}


_probe_cache: Dict[str, Any] = {"ts": 0.0, "result": None}


def _cached_probe(base_url: str) -> Dict[str, Any]:
    """60s probe cache — routing happens per task; don't hammer Ollama."""
    if time.time() - _probe_cache["ts"] > 60.0 or _probe_cache["result"] is None:
        _probe_cache["result"] = check_local_model(base_url)
        _probe_cache["ts"] = time.time()
    return _probe_cache["result"]


def token_budget_for(intent: str) -> Optional[int]:
    return load_routing()["daily_token_budgets"].get(intent)


def budget_remaining(intent: str, ledger=None) -> Optional[int]:
    """Today's remaining tokens for an intent. None = no budget set (unlimited).
    Counts tokens the runtime has logged against this intent today."""
    budget = token_budget_for(intent)
    if budget is None:
        return None
    from ghost.core.audit import AuditLedger
    ledger = ledger or AuditLedger.get_instance()
    day = time.strftime("%Y-%m-%d")
    spent = 0
    for row in ledger.query(day=day, event_type="tokens.spent", limit=1000):
        if (row.get("payload") or {}).get("intent") == intent:
            spent += int((row.get("payload") or {}).get("tokens", 0))
    return max(0, budget - spent)


def record_spend(intent: str, tokens: int, ledger=None) -> None:
    """Call from any LLM invocation (token_tracker hook) to feed the budgets."""
    if ledger is not None:
        ledger.record("tokens.spent", intent=intent, tokens=int(tokens))
    else:
        audit("tokens.spent", intent=intent, tokens=int(tokens))
