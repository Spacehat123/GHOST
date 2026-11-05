"""STORE PLANE — cutover facade (T1c): ONE flag decides where the spine lives.

GHOST_STORE_MODE = legacy (default) | unified

- legacy:  exactly the pre-T1 behavior (data/audit.db, data/queue.db, ...)
- unified: the spine (audit ledger, job queue) reads/writes data/ghost.db
  (events_log / jobs); every other store still resolves as before until its
  own plane moves it

Rollback = unset the flag (or set legacy). Minutes, not data surgery —
the old stores are never deleted during the dual-read window.

An EXPLICIT env path (GHOST_AUDIT_DB_PATH, GHOST_QUEUE_DB_PATH) always wins —
that is what keeps operator redirects and test isolation working in both
modes. GHOST_UNIFIED_DB_PATH scopes the unified store for tests.
"""
import os

MODE_ENV = "GHOST_STORE_MODE"
UNIFIED_DB_ENV = "GHOST_UNIFIED_DB_PATH"
MODES = ("legacy", "unified")

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def mode() -> str:
    value = os.environ.get(MODE_ENV, "legacy").strip().lower()
    return value if value in MODES else "legacy"


def unified() -> bool:
    return mode() == "unified"


def repo_dir() -> str:
    return os.environ.get("GHOST_PROJECT_ROOT") or REPO_DIR


def ghost_db_path() -> str:
    return os.environ.get(UNIFIED_DB_ENV) or os.path.join(repo_dir(), "data", "ghost.db")


def audit_path() -> str:
    """Explicit GHOST_AUDIT_DB_PATH wins in every mode; otherwise the flag."""
    explicit = os.environ.get("GHOST_AUDIT_DB_PATH")
    if explicit:
        return explicit
    return ghost_db_path() if unified() else os.path.join(repo_dir(), "data", "audit.db")


def audit_table() -> str:
    return "events_log" if unified() else "audit_events"


def queue_path() -> str:
    explicit = os.environ.get("GHOST_QUEUE_DB_PATH")
    if explicit:
        return explicit
    return ghost_db_path() if unified() else os.path.join(repo_dir(), "data", "queue.db")
