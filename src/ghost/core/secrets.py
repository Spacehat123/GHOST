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


# operator-settable secret names (the T9 settings UI writes ONLY these)
SETTABLE_SECRETS = (
    "GHOST_GRANT_SECRET",
    "GHOST_OUTREACH_SMTP_HOST", "GHOST_OUTREACH_SMTP_PORT",
    "GHOST_OUTREACH_SMTP_USER", "GHOST_OUTREACH_SMTP_PASSWORD",
    "GHOST_OUTREACH_FROM", "GHOST_OUTREACH_REPLY_TO",
    "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET",
    "GHOST_SENDING_DOMAIN",
    # inbox reading (Operator's 11 P5): the reply-intake IMAP account —
    # configured from the UI, never a config file the operator must edit
    "GHOST_INBOX_EMAIL", "GHOST_IMAP_HOST", "GHOST_IMAP_PORT",
    "GHOST_IMAP_USER", "GHOST_IMAP_PASSWORD",
    # Physical Intelligence / Smart Home
    "HOMEASSISTANT_URL", "HOMEASSISTANT_TOKEN",
    "MQTT_BROKER_HOST", "MQTT_BROKER_PORT", "MQTT_USERNAME", "MQTT_PASSWORD",
    # Creative Intelligence Tooling Paths
    "OPENSCAD_BINARY_PATH", "BLENDER_BINARY_PATH",
)


def set_secret(name: str, value: str) -> bool:
    """Operator settings UI path (T9): write ONE secret into the vault.
    Only SETTABLE_SECRETS names are accepted — the UI cannot invent
    credential-shaped env entries. Atomic rename, chmod 600."""
    if name not in SETTABLE_SECRETS:
        logger.warning("set_secret refused: %s is not operator-settable", name)
        return False
    path = _vault_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    vault = dict(_vault())
    vault[name] = str(value)
    tmp = path + f".tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(vault, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    global _SECRET_CACHE, _SECRET_CACHE_KEY
    _SECRET_CACHE = None   # invalidate; next get() re-reads
    _SECRET_CACHE_KEY = None
    try:
        from ghost.core.audit import audit
        audit("operator.secret_set", name=name)
    except Exception:
        pass
    return True


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
