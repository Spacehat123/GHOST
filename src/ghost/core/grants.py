"""Policy Grant Tokens — outbound authority you can't bypass (Roadmap §11).

§11: 'Outbound actions signed by policy engine grants (a worker without a
grant token can't send mail — enforced at the mail provider wrapper, not at
the LLM prompt).' This is that mechanism:

- The OutboundGateway, after ALL gates pass, issues an HMAC-signed token
  binding: capability, lead, contact cap, facts count, expiry.
- The mail wrapper (autonomy/sender.py) VERIFIES the token before touching
  SMTP. No token, wrong capability, expired, or already-consumed -> refuse.
- Tokens are single-use; consumption is recorded in the audit ledger, so a
  captured token can't be replayed for a second send.

The signing key lives in the secrets vault (GHOST_GRANT_SECRET). If it is
absent, issue() fails closed — no authority can be minted at all.
"""
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from ghost.core.audit import audit

_DEFAULT_TTL_S = 300.0  # grants are for immediate dispatch, not storage


def _key() -> Optional[str]:
    from ghost.core.secrets import get
    return get("GHOST_GRANT_SECRET")


def issue(capability: str, subject: str, bounds: Dict[str, Any],
          ttl_s: float = _DEFAULT_TTL_S) -> Optional[str]:
    """Mint a grant token. Returns None if no signing key is configured —
    meaning the deployment cannot authorize outbound at all (fail closed)."""
    key = _key()
    if not key:
        audit("grant.issue_refused", subject=subject, capability=capability,
              reason="no GHOST_GRANT_SECRET configured")
        return None
    now = time.time()
    body = {
        "capability": capability,
        "subject": subject,
        "bounds": bounds,
        "iat": now,
        "exp": now + ttl_s,
        "nonce": hashlib.sha256(f"{now}-{subject}-{capability}".encode()).hexdigest()[:12],
    }
    body_b64 = _b64(json.dumps(body, sort_keys=True))
    sig = hmac.new(key.encode(), body_b64.encode(), hashlib.sha256).hexdigest()
    token = f"gt.{body_b64}.{sig}"
    audit("grant.issued", subject=subject, capability=capability,
          exp=body["exp"], nonce=body["nonce"])
    return token


def verify(token: str, capability: str, subject: Optional[str] = None,
           ledger=None) -> Optional[Dict[str, Any]]:
    """Verify + consume a grant. Returns bounds if valid, else None.

    Single-use: a token whose nonce already appears in a grant.consumed
    ledger event is refused (replay protection)."""
    key = _key()
    if not key or not token or not token.startswith("gt."):
        return None
    try:
        body_b64, sig = token[3:].split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(key.encode(), body_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        audit("grant.verify_failed", reason="bad_signature", capability=capability)
        return None
    try:
        body = json.loads(_unb64(body_b64))
    except Exception:
        return None
    if body.get("capability") != capability:
        audit("grant.verify_failed", reason="capability_mismatch",
              expected=capability, got=body.get("capability"))
        return None
    if subject is not None and body.get("subject") != subject:
        audit("grant.verify_failed", reason="subject_mismatch")
        return None
    if time.time() > body.get("exp", 0):
        audit("grant.verify_failed", reason="expired", capability=capability)
        return None
    if _consumed(body.get("nonce"), ledger):
        audit("grant.verify_failed", reason="replayed", nonce=body.get("nonce"))
        return None
    # Consume: record BEFORE returning so a crash can't double-send.
    audit("grant.consumed", subject=body.get("subject"), capability=capability,
          nonce=body.get("nonce"))
    return body.get("bounds", {})


def _consumed(nonce: Optional[str], ledger=None) -> bool:
    if not nonce:
        return False
    try:
        from ghost.core.audit import AuditLedger
        rows = (ledger or AuditLedger.get_instance()).query(
            event_type="grant.consumed", limit=500)
        return any((r.get("payload") or {}).get("nonce") == nonce for r in rows)
    except Exception:
        # If we cannot check consumption, treat as consumed (fail closed).
        return True


# ------------------------------------------------------------- base64 helpers

def _b64(s: str) -> str:
    import base64
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _unb64(s: str) -> str:
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()
