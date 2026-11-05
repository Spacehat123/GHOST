"""VERIFY PLANE — reconciliation (T5): Ghost's records vs reality, daily.

Three independent checks, each honest about what it CAN check:

1. payments  — provider payments (fetched by an injected/real fetcher) vs
               ledger `payment.verified` events. A captured provider payment
               with no ledger event, or a ledger event with no provider
               payment, is a mismatch.
2. sends     — outbox rows (kind=outreach_email, sent/verified) vs the mail
               provider's log, matched by Message-ID (T2 made the Message-ID
               the outbox idempotency key on purpose). A 'sent' row absent
               from the provider log is a phantom send.
3. deploys   — outbox rows (kind=deploy, sent) vs the live URL: is the
               deployed site actually up?

Nothing here trusts the brain or the effects plane: it re-derives truth from
independent sources. Every mismatch becomes an audit event (→ digest) and a
ladder-visible incident for the operator.

Fetchers are injected. Real ones (razorpay REST / provider mail logs) are
wired when the operator's keys exist; absent keys = honest `not_configured`,
never a fake green.
"""
import json
import logging
import os
import sqlite3
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from ghost.core.audit import audit

logger = logging.getLogger("ghost.verify.reconciliation")

SNAPSHOT_MAX = 500


def _outbox_rows(kind: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    from ghost.store.facade import ghost_db_path
    import sqlite3
    conn = sqlite3.connect(f"file:{db_path or ghost_db_path()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT id, kind, idempotency_key, payload, state, created_at "
            "FROM outbox WHERE kind=? LIMIT ?", (kind, SNAPSHOT_MAX))]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _sent_outbox_rows(kind: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Only rows whose effect CLAIMED to happen (sent/verified). Pending rows
    are the worklist, not claims — reconciling them would fake phantoms."""
    return [r for r in _outbox_rows(kind, db_path) if r.get("state") in ("sent", "verified")]


# ----------------------------------------------------------------- payments

def reconcile_payments(fetch_provider_payments: Optional[Callable[[], List[Dict[str, Any]]]],
                       ledger=None) -> Dict[str, Any]:
    """Every provider 'captured' payment must exist in the ledger and vice
    versa. Matching key: invoice number (provider notes.invoice) + amount."""
    if fetch_provider_payments is None:
        return {"status": "not_configured", "note": "no provider payment fetcher configured"}
    from ghost.core.audit import AuditLedger
    ledger = ledger or AuditLedger.get_instance()
    try:
        provider_payments = fetch_provider_payments() or []
    except Exception as e:
        audit("reconcile.error", section="payments", error=str(e)[:200])
        return {"status": "error", "error": str(e)[:200]}
    ledger_rows = ledger.query(event_type="payment.verified", limit=SNAPSHOT_MAX)
    ledger_by_invoice = {}
    for r in ledger_rows:
        p = r.get("payload") or {}
        ledger_by_invoice[str(p.get("invoice_number"))] = str(p.get("amount"))

    provider_by_invoice: Dict[str, str] = {}
    for pay in provider_payments:
        if str(pay.get("status", "")).lower() not in ("captured", "paid", "settled"):
            continue
        inv = str((pay.get("notes") or {}).get("invoice") or pay.get("invoice") or "")
        provider_by_invoice[inv] = str(pay.get("amount"))

    missing_in_ledger = [inv for inv in provider_by_invoice if inv not in ledger_by_invoice]
    missing_at_provider = [inv for inv, amt in ledger_by_invoice.items()
                           if inv not in provider_by_invoice and inv != "None"]
    amount_mismatch = [inv for inv in provider_by_invoice
                       if inv in ledger_by_invoice
                       and _amt(provider_by_invoice[inv]) != _amt(ledger_by_invoice[inv])]

    status = "ok" if not (missing_in_ledger or missing_at_provider or amount_mismatch) else "mismatch"
    report = {"status": status, "provider_payments": len(provider_by_invoice),
              "ledger_events": len(ledger_by_invoice),
              "missing_in_ledger": missing_in_ledger[:20],
              "missing_at_provider": missing_at_provider[:20],
              "amount_mismatch": amount_mismatch[:20]}
    if status == "mismatch":
        audit("reconcile.mismatch", section="payments", **{k: v for k, v in report.items() if k != "status"})
    return report


def _amt(x: Any) -> str:
    try:
        from decimal import Decimal
        # 'f' format avoids Decimal.normalize()'s scientific notation (25000 -> 2.5E+4)
        return format(Decimal(str(x)).normalize(), "f")
    except Exception:
        return str(x)


# -------------------------------------------------------------------- sends

def message_id_for_outbox_key(key: str) -> str:
    """Mirror of the sender's provider-facing idempotency key (T2)."""
    return f"<{key.replace(':', '-')}@outreach.ghost>"


def reconcile_sends(fetch_provider_log: Optional[Callable[[], List[Dict[str, Any]]]],
                    db_path: Optional[str] = None) -> Dict[str, Any]:
    """Every outreach row we marked 'sent' must appear in the provider's own
    log (matched by Message-ID). Missing = phantom send (we claim what the
    provider never accepted)."""
    if fetch_provider_log is None:
        return {"status": "not_configured", "note": "no provider mail-log fetcher configured"}
    try:
        log_entries = fetch_provider_log() or []
    except Exception as e:
        audit("reconcile.error", section="sends", error=str(e)[:200])
        return {"status": "error", "error": str(e)[:200]}
    logged_ids = {str(e.get("message_id", "")).strip().strip("<>")
                  for e in log_entries}
    rows = _sent_outbox_rows("outreach_email", db_path)
    phantoms = []
    for r in rows:
        mid = message_id_for_outbox_key(r["idempotency_key"]).strip("<>")
        if mid not in logged_ids:
            phantoms.append({"key": r["idempotency_key"], "message_id": mid})
    status = "ok" if not phantoms else "mismatch"
    report = {"status": status, "outbox_sent_rows": len(rows),
              "provider_log_entries": len(log_entries),
              "phantom_sends": phantoms[:20]}
    if status == "mismatch":
        audit("reconcile.mismatch", section="sends", phantoms=len(phantoms))
    return report


# ------------------------------------------------------------------ deploys

def reconcile_deploys(fetch_url: Optional[Callable[[str], int]] = None,
                      db_path: Optional[str] = None) -> Dict[str, Any]:
    """Every deploy we marked 'sent' must still be live: the CRM's demo_url
    for that slug answers HTTP 200."""
    if fetch_url is None:
        fetch_url = _default_fetch_url
    from ghost.store.facade import ghost_db_path
    try:
        conn = sqlite3.connect(f"file:{db_path or ghost_db_path()}?mode=ro", uri=True)
    except sqlite3.Error:
        return {"status": "ok", "note": "unified store absent — nothing to reconcile",
                "checked": 0}
    conn.row_factory = sqlite3.Row
    rows: List[Dict[str, Any]] = []
    targets = []
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT idempotency_key, payload, state FROM outbox "
            "WHERE kind='deploy' AND state IN ('sent','verified') LIMIT ?", (SNAPSHOT_MAX,))]
        # resolve URLs while the connection is open, THEN fetch
        for r in rows:
            payload = json.loads(r.get("payload") or "{}")
            slug = payload.get("slug")
            url = _demo_url_for(slug, conn)
            if url:
                targets.append((slug, url))
    finally:
        conn.close()
    if not rows:
        return {"status": "ok", "note": "no deploys to reconcile", "checked": 0}
    dead = []
    for slug, url in targets:
        try:
            code = fetch_url(url)
            if code != 200:
                dead.append({"slug": slug, "url": url, "http": code})
        except Exception as e:
            dead.append({"slug": slug, "url": url, "error": str(e)[:120]})
    status = "ok" if not dead else "mismatch"
    report = {"status": status, "checked": len(rows), "dead_deploys": dead[:20]}
    if status == "mismatch":
        audit("reconcile.mismatch", section="deploys", dead=len(dead))
    return report


def _demo_url_for(slug: str, conn) -> Optional[str]:
    try:
        row = conn.execute("SELECT demo_url FROM crm_leads WHERE slug=?", (slug,)).fetchone()
        if row and row["demo_url"]:
            return row["demo_url"]
    except sqlite3.Error:
        pass
    return None


def _default_fetch_url(url: str, timeout: float = 8.0) -> int:
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": "ghost-reconcile/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(resp.status)


# --------------------------------------------------------------------- run

def run_all(payment_fetcher=None, mail_log_fetcher=None,
            deploy_fetcher=None, ledger=None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """The daily reconciliation pass. Audits a summary the digest picks up."""
    report = {
        "at": time.time(),
        "payments": reconcile_payments(payment_fetcher, ledger=ledger),
        "sends": reconcile_sends(mail_log_fetcher, db_path=db_path),
        "deploys": reconcile_deploys(deploy_fetcher, db_path=db_path),
    }
    mismatches = [k for k, v in report.items()
                  if isinstance(v, dict) and v.get("status") == "mismatch"]
    report["mismatch_sections"] = mismatches
    audit("reconcile.run", mismatches=mismatches,
          payments=report["payments"].get("status"),
          sends=report["sends"].get("status"),
          deploys=report["deploys"].get("status"))
    return report


# ------------------------------------------------- real fetchers (key-gated)

def razorpay_payment_fetcher() -> Optional[Callable[[], List[Dict[str, Any]]]]:
    """Returns a fetcher when Razorpay keys exist, else None (honest)."""
    from ghost.workers.payments.razorpay_provider import load_razorpay_config
    from ghost.core.secrets import get
    cfg = load_razorpay_config()
    key_id = get(cfg.get("key_id_env", "RAZORPAY_KEY_ID"))
    key_secret = get(cfg.get("key_secret_env", "RAZORPAY_KEY_SECRET"))
    if not (key_id and key_secret):
        return None

    def fetch() -> List[Dict[str, Any]]:
        import base64
        auth = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        req = urllib.request.Request(
            "https://api.razorpay.com/v1/payments?count=100",
            headers={"Authorization": f"Basic {auth}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return [{"id": p.get("id"), "amount": p.get("amount"),
                 "currency": p.get("currency"), "status": p.get("status"),
                 "notes": p.get("notes") or {}} for p in data.get("items", [])]
    return fetch
