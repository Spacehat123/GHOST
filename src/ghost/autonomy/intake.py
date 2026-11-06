"""AUTONOMY — the reply intake (Operator's 11 P5, goals 6+11).

Email replies are read directly from the operator's inbox (read-only IMAP)
instead of waiting for the operator to relay them. Every message passes the
T7 quarantine (inside ingest_reply) BEFORE anything acts on it — untrusted
text is DATA.

Honesty rules:
- a sender that matches no lead/client is UNMATCHED — audited and left for
  the operator, never force-matched to some plausible lead
- the same message is never ingested twice (sha256 of sender+subject+body,
  persisted across restarts)
- this worker only ever WRITES drafts/suppressions/pings — it cannot send
  anything (that power lives solely in the effects-plane sender)
"""
import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from ghost.core.audit import audit
from ghost.paths import data_dir

logger = logging.getLogger("ghost.autonomy.intake")


def _seen_path() -> str:
    return os.environ.get("GHOST_INTAKE_SEEN") or os.path.join(data_dir(), "intake_seen.json")


def _load_seen() -> Dict[str, float]:
    try:
        with open(_seen_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _record_seen(seen: Dict[str, float], key: str) -> None:
    seen[key] = time.time()
    # cap: keep the newest 2000 marks — old mail cannot replay (its hash is
    # still here) and the file stays small
    if len(seen) > 2000:
        for k, _ in sorted(seen.items(), key=lambda kv: kv[1])[: len(seen) - 2000]:
            seen.pop(k, None)
    tmp = _seen_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f)
    os.replace(tmp, _seen_path())


def match_sender_to_slug(crm, sender_email: str) -> Optional[str]:
    """A reply only matters if it belongs to a conversation Ghost knows.
    clients.email first (retainers), then leads whose recorded email matches
    (leads carry email in gaps_json). No match -> None (honest)."""
    if not sender_email:
        return None
    email = sender_email.strip().lower()
    try:
        with crm._get_connection() as conn:
            row = conn.execute(
                "SELECT slug FROM clients WHERE lower(email) = ?", (email,)).fetchone()
            if row:
                return row["slug"]
            rows = conn.execute("SELECT slug, gaps_json FROM leads").fetchall()
        for r in rows:
            try:
                gaps = json.loads(r["gaps_json"] or "{}")
            except (TypeError, ValueError):
                continue
            if str(gaps.get("email", "")).strip().lower() == email:
                return r["slug"]
    except Exception as e:
        logger.warning("sender match failed: %s", e)
    return None


def poll_email_replies(crm=None, suppression=None, ladder=None,
                       ping: Optional[Any] = None,
                       email_service=None) -> Dict[str, Any]:
    """One intake pass: read unread mail, ingest what matches, skip what was
    already seen. Returns the honest count dict for the digest."""
    from ghost.comms.email_service import EmailService
    es = email_service or EmailService.get_instance()
    if not es.imap_configured():
        return {"status": "not_configured"}

    crm = crm or _default_crm()
    suppression = suppression or _default_suppression(crm)
    ladder = ladder or _default_ladder()

    try:
        msgs = es.check_inbox(unread_only=True, limit=10)
    except Exception as e:
        audit("intake.email_error", error=str(e)[:200])
        return {"status": "error", "error": str(e)[:160]}

    seen = _load_seen()
    ingested = quarantined = skipped_seen = unmatched = 0
    for m in msgs:
        key = m.get("message_id") or hashlib.sha256(
            f"{m.get('sender_email')}|{m.get('subject')}|{m.get('body')}".encode()).hexdigest()
        if key in seen:
            skipped_seen += 1
            continue
        slug = match_sender_to_slug(crm, m.get("sender_email", ""))
        if not slug:
            unmatched += 1
            audit("intake.unmatched", subject=(m.get("subject") or "")[:120],
                  sender=(m.get("sender_email") or "")[:120],
                  ping="(c) unverifiable: inbox reply from an unknown sender — relay it manually if it matters")
            _record_seen(seen, key)   # do not re-ping every cycle
            continue
        from ghost.autonomy.reply_loop import ingest_reply
        out = ingest_reply(slug, m.get("body", ""), suppression=suppression,
                           crm=crm, ladder=ladder, ping=ping)
        _record_seen(seen, key)
        ingested += 1
        if out.get("quarantined"):
            quarantined += 1

    audit("intake.email_polled", ingested=ingested, quarantined=quarantined,
          unmatched=unmatched, already_seen=skipped_seen)
    return {"status": "ok", "ingested": ingested, "quarantined": quarantined,
            "unmatched": unmatched, "already_seen": skipped_seen}


def _default_crm():
    from ghost.crm.db import CRMDatabase
    return CRMDatabase.get_instance()


def _default_suppression(crm):
    from ghost.workers.suppression import SuppressionManager
    return SuppressionManager(crm=crm)


def _default_ladder():
    from ghost.autonomy.ladder import AutonomyLadder
    return AutonomyLadder()


def draft_due_followups(crm=None, drafter=None) -> Dict[str, Any]:
    """The once-daily follow-up sweep: every conversation we sent to and have
    heard nothing back for `followup_after_days` gets EXACTLY ONE follow-up
    DRAFT (the operator sends it). generate_followup_draft owns the rules
    (window, max-1, state checks); this just feeds it every candidate."""
    crm = crm or _default_crm()
    drafter = drafter or _default_drafter(crm)
    drafted, considered = 0, 0
    try:
        with crm._get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT lead_id FROM drafts WHERE status = 'sent'").fetchall()
    except Exception as e:
        audit("followups.sweep_failed", error=str(e)[:200])
        return {"status": "error", "error": str(e)[:160]}
    for r in rows:
        lead = crm.get_lead(r["lead_id"])
        if not lead:
            continue
        considered += 1
        try:
            d = drafter.generate_followup_draft(lead["slug"])
        except Exception:
            continue
        if d:
            drafted += 1
            audit("followup.drafted", subject=lead["slug"],
                  ping=None)   # rides the 09:00 digest, not an interruption
    audit("followups.swept", considered=considered, drafted=drafted)
    return {"status": "ok", "considered": considered, "drafted": drafted}


def _default_drafter(crm):
    from ghost.workers.outreach_drafter import OutreachDrafter
    return OutreachDrafter(crm=crm)
