"""CONTROL PLANE — revocation epoch (T3): the kill switch behind the kill switch.

A halt must not merely set a flag workers might forget to check — it must
INVALIDATE OUTSTANDING AUTHORITY. Every grant token records the revocation
epoch at issue time; `grants.verify()` refuses any token whose epoch does not
match the CURRENT one. `halt.trip()` bumps the epoch, so every grant minted
before the trip is dead the instant the sentinel lands — and the supervisor
(T4, separate process) can bump the same file without touching the store.

The epoch file is: tiny, atomic-rename written, re-read on every check (two
file reads with the halt sentinel — no cache, no DB, identical behavior from
any process). Missing file = epoch 0 (fresh system).
"""
import os

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def epoch_path() -> str:
    """Re-resolved per call (env redirects / test isolation)."""
    return os.environ.get("GHOST_REVOCATION_EPOCH") or os.path.join(REPO_DIR, "data", "REVOCATION_EPOCH")


def current_epoch() -> int:
    try:
        with open(epoch_path(), "r", encoding="utf-8") as f:
            return max(0, int(f.read().strip() or 0))
    except (OSError, ValueError):
        return 0


def bump_epoch(by: str = "halt") -> int:
    """Advance the epoch monotonically. Atomic rename; safe from any process.
    Returns the new epoch."""
    path = epoch_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = current_epoch() + 1
    tmp = path + f".tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(new))
    os.replace(tmp, path)
    return new
