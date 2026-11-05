"""CONTROL PLANE — human-reserved act packages (T9).

The five reserved acts (sign contracts, send money out/refund, delete client
data, change own rules, the unverifiable) are NEVER performed by Ghost. For
the first three, Ghost prepares a FINISHED PACKAGE: everything pre-filled,
its recommendation attached, one action for the human — and keeps working on
everything else while it waits.

Lifecycle: ready → (operator approve) → executed   |   dismissed   |   blocked
Approval is the operator's click: the endpoint mints an OPERATOR grant (the
operator is the authority) and the effect executes once through the proper
plane. Nothing here is reachable from LLM proposals — approval is an
operator-only server route.

Delete semantics (working rule 4 — never delete paid-client data): a delete
package EXECUTES AS ARCHIVE — snapshot proof + the site/data moved to the
archive dir. There is no rm path in this module.
"""
import json
import os
import shutil
import time
import uuid
from typing import Any, Dict, List, Optional

from ghost.core.audit import audit

PACKAGE_KINDS = ("contract", "refund", "delete")


def _conn():
    from ghost.store.facade import ghost_db_path
    from ghost.store.schema import connect_target, create_target
    conn = connect_target(ghost_db_path())
    create_target(conn)
    return conn


def create_package(kind: str, subject: str, payload: Dict[str, Any],
                   recommendation: str = "") -> Dict[str, Any]:
    """Ghost prepares a package. `payload` carries everything the human needs:
    pre-filled fields, evidence, the exact one-click effect."""
    if kind not in PACKAGE_KINDS:
        return {"error": f"unknown package kind {kind!r}"}
    pid = f"pkg_{kind}_{uuid.uuid4().hex[:10]}"
    now = time.time()
    with _conn() as conn, conn:
        conn.execute(
            "INSERT INTO reserved_packages (id, kind, subject, state, payload_json, "
            "recommendation, created_at) VALUES (?, ?, ?, 'ready', ?, ?, ?)",
            (pid, kind, subject, json.dumps(payload, default=str),
             recommendation[:500], now))
    audit("reserved.created", subject=pid, kind=kind, target=subject)
    return {"id": pid, "kind": kind, "subject": subject, "state": "ready",
            "payload": payload, "recommendation": recommendation}


def list_packages(state: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    conn = _conn()
    conn.row_factory = None
    try:
        sql = ("SELECT id, kind, subject, state, payload_json, recommendation, created_at "
               "FROM reserved_packages")
        args: list = []
        if state:
            sql += " WHERE state=?"
            args.append(state)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        out = []
        for pid, kind, subject, st, payload, rec, created in conn.execute(sql, args):
            out.append({"id": pid, "kind": kind, "subject": subject, "state": st,
                        "payload": json.loads(payload or "{}"),
                        "recommendation": rec, "created_at": created})
        return out
    finally:
        conn.close()


def get_package(pid: str) -> Optional[Dict[str, Any]]:
    for p in list_packages(limit=500):
        if p["id"] == pid:
            return p
    return None


def _mark(pid: str, state: str, outcome: Optional[Dict[str, Any]] = None,
          decided_by: str = "operator") -> None:
    with _conn() as conn, conn:
        conn.execute(
            "UPDATE reserved_packages SET state=?, decided_at=?, decided_by=?, "
            "outcome_json=? WHERE id=?",
            (state, time.time(), decided_by, json.dumps(outcome or {}, default=str), pid))


# ------------------------------------------------------------- the one click

def approve(pid: str, decided_by: str = "operator") -> Dict[str, Any]:
    """THE one action. Operator-only (the server route is the only caller).
    Mints an operator grant and executes the effect ONCE through the plane."""
    pkg = get_package(pid)
    if not pkg:
        return {"error": "package not found"}
    if pkg["state"] != "ready":
        return {"error": f"package is {pkg['state']}, not ready (one action per package)"}
    from ghost.core import grants
    from ghost.core.halt import is_halted
    if is_halted():
        # even reserved-act execution respects the throat-cut: the operator
        # can resume first — a halted system executes nothing at all
        _mark(pid, "blocked", {"reason": "halt active"})
        return {"id": pid, "state": "blocked",
                "outcome": {"ok": False, "error": "halt active — resume and re-approve"}}

    kind, payload = pkg["kind"], pkg["payload"]
    token = grants.issue(capability=f"reserved_{kind}", subject=pkg["subject"],
                         bounds={"package": pid, "by": decided_by}, ttl_s=120.0)
    if token is None:
        _mark(pid, "blocked", {"reason": "no grant authority"})
        return {"id": pid, "state": "blocked",
                "outcome": {"ok": False,
                            "error": "no grant authority (GHOST_GRANT_SECRET unset)"}}

    outcome: Dict[str, Any]
    if kind == "contract":
        outcome = _execute_contract(payload, token)
    elif kind == "refund":
        outcome = _execute_refund(payload, token)
    else:  # delete → ARCHIVE (never rm)
        outcome = _execute_archive(payload, token)

    state = "executed" if outcome.get("ok") else "blocked"
    _mark(pid, state, outcome, decided_by=decided_by)
    audit("reserved.decided", subject=pid, kind=kind, state=state,
          by=decided_by, outcome=str(outcome)[:200])
    return {"id": pid, "state": state, "outcome": outcome}


def dismiss(pid: str, decided_by: str = "operator") -> Dict[str, Any]:
    pkg = get_package(pid)
    if not pkg or pkg["state"] != "ready":
        return {"error": "package not found or not ready"}
    _mark(pid, "dismissed", {"by": decided_by})
    audit("reserved.dismissed", subject=pid)
    return {"id": pid, "state": "dismissed"}


# ------------------------------------------------------------- executors

def _execute_contract(payload: Dict[str, Any], token: str) -> Dict[str, Any]:
    """Generate the contract from the pre-filled fields and move the CRM
    stage. The contract PDF path is the operator's artifact."""
    try:
        from ghost.workers.close_chain import _default_build_contract
        path = _default_build_contract(payload)
        crm_slug = payload.get("lead_slug")
        if crm_slug:
            from ghost.crm.db import CRMDatabase
            CRMDatabase.get_instance().update_lead(crm_slug, status="contract_sent")
        audit("reserved.contract_executed", path=str(path))
        return {"ok": True, "contract_path": str(path)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _execute_refund(payload: Dict[str, Any], token: str) -> Dict[str, Any]:
    """Refund through the payment provider, fail-closed without keys."""
    try:
        from ghost.workers.payments.razorpay_provider import RazorpayProvider
        provider = RazorpayProvider()
        payment_id = payload.get("payment_id")
        amount = payload.get("amount")
        if not payment_id or amount is None:
            return {"ok": False, "error": "refund package missing payment_id/amount"}
        ref = provider.create_refund(payment_id, amount) if hasattr(provider, "create_refund") \
            else {"ok": False, "error": "provider lacks refund support"}
        if not ref.get("ok"):
            return {"ok": False, "error": str(ref.get("error", "provider refused"))[:200]}
        from ghost.core.audit import AuditLedger
        AuditLedger.get_instance().record("payment.refunded", subject=str(payment_id),
                                          amount=str(amount), by="operator",
                                          package=payload.get("package_id", ""))
        return {"ok": True, "refund": ref}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _execute_archive(payload: Dict[str, Any], token: str) -> Dict[str, Any]:
    """'Delete' executes as ARCHIVE: snapshot proof first, then the site dir
    moves to data/archive/. A paid client's data is never destroyed."""
    from ghost.core.sandbox import path_allowed
    target = str(payload.get("path") or "")
    if not target or not os.path.exists(target) or not path_allowed(target):
        return {"ok": False, "error": f"archive target invalid or outside sandbox: {target}"}
    # snapshot proof FIRST (the rollback exists before anything moves)
    try:
        from ghost.workers.snapshots import SnapshotStore
        snap = SnapshotStore().snapshot(os.path.basename(target), target,
                                        label="pre-archive")
        if not snap.get("snapshot_id"):
            return {"ok": False, "error": "pre-archive snapshot failed — refusing"}
    except Exception as e:
        return {"ok": False, "error": f"snapshot failed: {str(e)[:150]}"}
    archive_root = os.path.join(os.path.dirname(os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".."))), "data", "archive")
    os.makedirs(archive_root, exist_ok=True)
    dest = os.path.join(archive_root, f"{os.path.basename(target)}-{int(time.time())}")
    try:
        shutil.move(target, dest)
    except OSError as e:
        return {"ok": False, "error": f"archive move failed: {e}"}
    audit("reserved.archived", target=target, dest=dest,
          snapshot=snap.get("snapshot_id"))
    return {"ok": True, "archived_to": dest,
            "snapshot_id": snap.get("snapshot_id"),
            "note": "archive, not deletion — restore via snapshots"}
