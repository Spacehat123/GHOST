"""Secrets Vault — minimal, honest, fail-closed (Roadmap §3.5).

Resolution order: environment variable first (12-factor), then
configs/secrets.json (chmod 0600, gitignored). Nothing is ever logged or
placed in an LLM context: `redact()` scrubs secret-shaped strings from any
payload before it reaches a model.

`require()` raises instead of returning None for capability-critical secrets —
callers fail closed with a clear message instead of silently misconfiguring.
"""
import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("ghost.secrets")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
SECRETS_PATH = os.environ.get("GHOST_SECRETS_PATH") or os.path.join(REPO_DIR, "configs", "secrets.json")

_SECRET_CACHE: Optional[dict] = None
_SECRET_CACHE_KEY: Optional[str] = None  # path+mtime — env changes are honored

_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9]{16,})"),                      # openai-style keys
    re.compile(r"(rzp_(live|test)_[A-Za-z0-9]{12,})"),         # razorpay keys
    re.compile(r"(ghp_[A-Za-z0-9]{30,})"),                     # github tokens
    re.compile(r"(xox[baprs]-[A-Za-z0-9-]{10,})"),             # slack tokens
]


def _vault_path() -> str:
    """Re-resolve per call (like the audit ledger) so long-lived processes and
    test harnesses that scope paths get consistent behavior."""
    return os.environ.get("GHOST_SECRETS_PATH") or SECRETS_PATH


def _vault() -> dict:
    global _SECRET_CACHE, _SECRET_CACHE_KEY
    path = _vault_path()
    if not os.path.exists(path):
        return {}
    stamp = f"{path}:{os.path.getmtime(path)}"
    if _SECRET_CACHE is None or stamp != _SECRET_CACHE_KEY:
        try:
            with open(path, "r", encoding="utf-8") as f:
                _SECRET_CACHE = json.load(f)
            _SECRET_CACHE_KEY = stamp
            # Enforce restrictive perms (best-effort).
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception as e:
            logger.warning("secrets vault unreadable: %s", e)
            _SECRET_CACHE = {}
            _SECRET_CACHE_KEY = stamp
    return _SECRET_CACHE or {}


def get(name: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve a secret: env var of the same name wins, then the vault file."""
    env_val = os.environ.get(name)
    if env_val:
        return env_val
    return _vault().get(name, default)


def require(name: str) -> str:
    """Capability-critical resolution: raises with setup instructions if absent."""
    val = get(name)
    if not val:
        raise RuntimeError(
            f"required secret '{name}' is not configured. "
            f"Set env {name} or add it to {SECRETS_PATH} (chmod 600, gitignored). "
            "Ghost fails closed rather than guessing.")
    return val


def configured(name: str) -> bool:
    return bool(get(name))


def redact(text: str) -> str:
    """Scrub secret-shaped strings before anything reaches an LLM context or log."""
    out = text or ""
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    # Scrub any vault values directly (exact match).
    for key, val in _vault().items():
        if isinstance(val, str) and len(val) >= 8 and val in out:
            out = out.replace(val, f"[REDACTED:{key}]")
    # Scrub credential-shaped ENV values too — secrets often live in env only.
    for key, val in os.environ.items():
        if (any(p in key.upper() for p in ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL"))
                and isinstance(val, str) and len(val) >= 8 and val in out):
            out = out.replace(val, f"[REDACTED:{key}]")
    return out


def vault_key_names() -> list:
    return sorted(_vault().keys())
