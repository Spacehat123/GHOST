"""AUTHORITY PLANE — pure grant crypto (no audit, no store, no effects).

This module is importable by BOTH the authority daemon and the main process
WITHOUT dragging in the store — the daemon must never touch ghost.db
(single-writer rule). It contains only the HMAC token format, its signer and
its verifier, plus the halt/epoch file checks that gate every operation.

The token format is unchanged from core/grants.py (gt.<b64>.<sig>) so tokens
minted before the authority daemon existed still verify.
"""
import hashlib
import hmac
import json
import os
import sqlite3
import time
from typing import Any, Dict, Optional

DEFAULT_TTL_S = 300.0


def _key(secret: Optional[str] = None) -> Optional[str]:
    return secret or os.environ.get("GHOST_GRANT_SECRET")


# ---- halt + epoch (pure file reads — identical behavior from any process) ----

def halt_active() -> bool:
    from ghost.core.halt import is_halted
    return is_halted()


def current_epoch() -> int:
    from ghost.core.revocation import current_epoch
    return current_epoch()


# ---- nonce store (the daemon's OWN sqlite — it is that file's sole writer) ----

def nonce_db_path() -> str:
    return os.environ.get("GHOST_AUTHORITY_NONCE_DB") or os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
        "data", "grant_authority.db")


def _nonce_conn(path: Optional[str] = None) -> sqlite3.Connection:
    p = path or nonce_db_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS consumed_nonces ("
        "nonce TEXT PRIMARY KEY, capability TEXT, subject TEXT, at REAL)")
    return conn


def nonce_used(nonce: Optional[str], db_path: Optional[str] = None) -> bool:
    """O(1) replay check — a dedicated table, not a ledger scan. Scales
    forever (finding #5)."""
    if not nonce:
        return False
    try:
        with _nonce_conn(db_path) as conn:
            row = conn.execute("SELECT 1 FROM consumed_nonces WHERE nonce=?",
                               (nonce,)).fetchone()
            return row is not None
    except sqlite3.Error:
        return True   # cannot check -> treated as consumed (fail closed)


def record_nonce(nonce: str, capability: str, subject: str,
                 db_path: Optional[str] = None) -> bool:
    """Record consumption. Returns False when the nonce already exists
    (replay) — the INSERT IS the check, so it scales and is race-free."""
    try:
        with _nonce_conn(db_path) as conn, conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO consumed_nonces (nonce, capability, subject, at) "
                "VALUES (?, ?, ?, ?)", (nonce, capability, subject, time.time()))
            return cur.rowcount > 0
    except sqlite3.Error:
        return False   # cannot record -> treat as replay (fail closed)


# ---- token format -------------------------------------------------------------

def _b64(s: str) -> str:
    import base64
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _unb64(s: str) -> str:
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def sign(capability: str, subject: str, bounds: Dict[str, Any],
         ttl_s: float = DEFAULT_TTL_S, secret: Optional[str] = None) -> Optional[str]:
    """Mint a token. The epoch is stamped — any later halt trip kills it."""
    key = _key(secret)
    if not key:
        return None
    now = time.time()
    body = {
        "capability": capability, "subject": subject, "bounds": bounds,
        "iat": now, "exp": now + ttl_s, "epoch": current_epoch(),
        "nonce": hashlib.sha256(f"{now}-{subject}-{capability}".encode()).hexdigest()[:12],
    }
    body_b64 = _b64(json.dumps(body, sort_keys=True))
    sig = hmac.new(key.encode(), body_b64.encode(), hashlib.sha256).hexdigest()
    return f"gt.{body_b64}.{sig}"


def verify(token: str, capability: str, subject: Optional[str] = None,
           secret: Optional[str] = None, nonce_db: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Verify + consume. Refuses on: bad signature, wrong capability/subject,
    halt active, epoch mismatch, expiry, replay (O(1) nonce table). The
    consume RECORDS before returning — a crash cannot double-spend."""
    key = _key(secret)
    if not key or not token or not token.startswith("gt."):
        return None
    if halt_active():
        return None
    try:
        body_b64, sig = token[3:].split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(key.encode(), body_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        body = json.loads(_unb64(body_b64))
    except Exception:
        return None
    if body.get("capability") != capability:
        return None
    if subject is not None and body.get("subject") != subject:
        return None
    if int(body.get("epoch", 0)) != current_epoch():
        return None
    if time.time() > body.get("exp", 0):
        return None
    # consume atomically: the insert IS the replay check
    if not record_nonce(body.get("nonce", ""), capability, str(subject), nonce_db):
        return None
    return body.get("bounds", {})
