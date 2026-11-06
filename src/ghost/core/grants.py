"""Policy Grant Tokens — outbound authority you can't bypass (Roadmap §11).

T-review refactor (findings #4 + #5): the main process is now a CLIENT of
the grant authority.

1. PRODUCTION PATH — `ghost grantd` (separate process) holds GHOST_GRANT_SECRET.
   Issue/verify go over a local unix socket; the signing key never exists in
   the brain/effects process, so code execution there cannot mint authority.
2. FALLBACK PATH — daemon unreachable AND GHOST_GRANT_SECRET in-process
   (dev/tests only): local signing through ghost.authority.protocol, whose
   replay protection is a dedicated O(1) nonce table (the old 500-event
   ledger scan is gone).

Fail closed in both paths: no daemon + no secret = no authority. Halts and
epoch bumps are honored by BOTH paths (file reads at every operation).
"""
import logging
import time
from typing import Any, Dict, Optional

from ghost.core.audit import audit
from ghost.authority import protocol

logger = logging.getLogger("ghost.core.grants")

_DEFAULT_TTL_S = protocol.DEFAULT_TTL_S  # grants are for immediate dispatch


def _authority_call(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Daemon RPC. None = unreachable (not an error — the caller falls back)."""
    try:
        from ghost.authority.service import call
        return call(request)
    except Exception:
        return None


def _in_process_possible() -> bool:
    """Fallback signing is allowed ONLY with the secret in-process — which is
    exactly what production avoids by running grantd."""
    return bool(protocol._key())


def issue(capability: str, subject: str, bounds: Dict[str, Any],
          ttl_s: float = _DEFAULT_TTL_S) -> Optional[str]:
    """Mint a grant token. Daemon-first; in-process fallback for dev/tests.
    None = no authority at all (fail closed)."""
    resp = _authority_call({"op": "issue", "capability": capability,
                            "subject": subject, "bounds": bounds, "ttl_s": ttl_s})
    if resp is not None:
        if resp.get("ok"):
            audit("grant.issued", subject=subject, capability=capability,
                  via="authority_daemon")
            return resp.get("token")
        audit("grant.issue_refused", subject=subject, capability=capability,
              reason=str(resp.get("error", "authority refused"))[:120], via="authority_daemon")
        return None
    # fallback: local signing — same protocol, same epoch/halt gates
    if not _in_process_possible():
        audit("grant.issue_refused", subject=subject, capability=capability,
              reason="no authority daemon and no in-process secret")
        return None
    from ghost.core.halt import is_halted
    if is_halted():
        audit("grant.issue_refused", subject=subject, capability=capability,
              reason="system halted — no authority minted", via="in_process_fallback")
        return None
    from ghost.core.revocation import current_epoch
    token = protocol.sign(capability, subject, bounds, ttl_s)
    if token is None:
        audit("grant.issue_refused", subject=subject, capability=capability,
              reason="no GHOST_GRANT_SECRET configured")
        return None
    audit("grant.issued", subject=subject, capability=capability,
          epoch=current_epoch(), via="in_process_fallback")
    return token


def verify(token: str, capability: str, subject: Optional[str] = None,
           ledger=None) -> Optional[Dict[str, Any]]:
    """Verify + consume. Daemon-first (the nonce lives in the daemon's own
    store); fallback uses the same pure protocol locally. None on any failure."""
    resp = _authority_call({"op": "verify", "token": token,
                            "capability": capability, "subject": subject})
    if resp is not None:
        if resp.get("ok"):
            audit("grant.consumed", subject=subject, capability=capability,
                  via="authority_daemon")
            return resp.get("bounds", {})
        audit("grant.verify_failed", capability=capability,
              reason=str(resp.get("error", "authority refused"))[:120], via="authority_daemon")
        return None
    # fallback: local protocol.verify — O(1) nonce table, epoch/halt honored
    if not _in_process_possible():
        audit("grant.verify_failed", capability=capability,
              reason="no authority daemon and no in-process secret")
        return None
    bounds = protocol.verify(token, capability, subject)
    if bounds is None:
        audit("grant.verify_failed", capability=capability,
              reason="invalid, halted, revoked, expired or replayed",
              via="in_process_fallback")
        return None
    audit("grant.consumed", subject=subject, capability=capability,
          via="in_process_fallback")
    return bounds
