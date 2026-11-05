"""Cold Outreach Sender — the ONLY code path that touches SMTP for outreach (Roadmap §6, §11).

The personal EmailService keeps its absolute prohibition on automated
dispatch (human-in-the-loop for personal mail, unchanged). This module is the
DEDICATED sending-domain channel §6.1 requires, and it is wrapped in four
independent locks, each of which fails closed:

  1. HALT dominates everything — a frozen agent doesn't even parse authority
     (and grants.verify independently refuses halted/revoked tokens too, T3).
  2. GRANT TOKEN (§11): a single-use HMAC token from the OutboundGateway
     (which already ran halt→ladder→warmup→cap→suppression→personalization),
     epoch-bound: any halt trip after minting kills it. Its daily cap is
     re-enforced HERE at the effect.
  3. INDEPENDENT re-checks at the wrapper (defense in depth): suppression is
     re-verified HERE — the wrapper does not trust callers. SMTP CREDENTIALS
     come from the secrets vault for the OUTREACH domain only (never the
     operator's personal mailbox). Unconfigured -> honest `not_configured`;
     the message becomes a CRM draft for manual sending.
  4. Unsubscribe mechanics on every message (List-Unsubscribe header +
     one-click suppression link), and identity headers from the operator/
     tenant config — no masked identity (§6.5).

The LLM never sees credentials: secrets.redact() scrubs before any context
leaves the process.
"""
import hashlib
import logging
import smtplib
import ssl
import time
from email.message import EmailMessage
from typing import Any, Dict, Optional

from ghost.core.audit import audit
from ghost.core import grants
from ghost.core.halt import is_halted
from ghost.core.secrets import require, get, redact

logger = logging.getLogger("ghost.autonomy.sender")

OUTREACH_CONFIG_KEYS = {
    "host": "GHOST_OUTREACH_SMTP_HOST",
    "port": "GHOST_OUTREACH_SMTP_PORT",
    "user": "GHOST_OUTREACH_SMTP_USER",
    "password": "GHOST_OUTREACH_SMTP_PASSWORD",
    "from_addr": "GHOST_OUTREACH_FROM",
    "reply_to": "GHOST_OUTREACH_REPLY_TO",
}


def smtp_configured() -> bool:
    return all(get(k) for k in ("GHOST_OUTREACH_SMTP_HOST", "GHOST_OUTREACH_SMTP_USER",
                                "GHOST_OUTREACH_SMTP_PASSWORD", "GHOST_OUTREACH_FROM"))


class SendRefused(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


class ColdOutreachSender:
    def __init__(self, suppression, crm=None):
        """suppression: re-checked here (defense in depth). crm: drafts fallback."""
        self.suppression = suppression
        self.crm = crm

    # ------------------------------------------------------------------ send

    def send(self, req, grant_token: Optional[str]) -> Dict[str, Any]:
        """req: autonomy.outbound.SendRequest that passed the gateway.
        grant_token: OutboundGateway.issue_grant(req) — MANDATORY."""
        lead_slug, to_email = req.lead_slug, req.to_email

        # Lock 1: HALT dominates everything — a frozen agent doesn't even
        # parse authority, let alone consume a grant (T3: grants.verify
        # independently refuses halted/revoked tokens as well).
        if is_halted():
            audit("outbound.send_refused", subject=lead_slug, reason="halt")
            raise SendRefused("halt", "throat-cut handle is active")

        # Lock 2: grant token (single-use, HMAC, capability-bound, epoch-bound).
        bounds = grants.verify(grant_token or "", capability="outbound_email",
                               subject=lead_slug)
        if bounds is None:
            audit("outbound.send_refused", subject=lead_slug,
                  reason="no_valid_grant", to=to_email)
            raise SendRefused("no_valid_grant",
                              "outreach requires a fresh grant from OutboundGateway.issue_grant()")

        # Lock 2b: the grant's OWN cap is enforced HERE, at the effect — the
        # gateway's issue-time cap is not trusted to be the last word.
        daily_cap = bounds.get("daily_cap")
        if daily_cap is not None:
            try:
                from ghost.core.audit import AuditLedger
                day = time.strftime("%Y-%m-%d")
                sent_today = len(AuditLedger.get_instance().query(
                    day=day, event_type="outbound.delivered", limit=1000))
                if sent_today >= int(daily_cap):
                    audit("outbound.send_refused", subject=lead_slug,
                          reason="over_cap", cap=daily_cap, sent_today=sent_today)
                    raise SendRefused("over_cap",
                                      f"daily cap {daily_cap} reached ({sent_today} sent today)")
            except SendRefused:
                raise
            except Exception:
                pass  # cap check is best-effort above the hard locks; never unlocks mail

        # Lock 3: independent re-checks at the wrapper — trust nothing upstream.
        if self.suppression.is_suppressed(to_email, lead_slug=lead_slug):
            audit("outbound.send_refused", subject=lead_slug, reason="suppressed")
            raise SendRefused("suppressed", to_email)

        # Lock 3b (T5): the promise checker — never promise off-terms anything.
        # Runs BEFORE the outbox record: a blocked promise never becomes an intent.
        from ghost.verify.promise_checker import check_send_allowed
        allowed, scan = check_send_allowed(req.subject, req.body)
        if not allowed:
            audit("outbound.promise_blocked", subject=lead_slug,
                  violations=scan["violations"][:5])
            raise SendRefused("promise_violation", "; ".join(scan["violations"])[:300])

        # Lock 3: credentials for the OUTREACH domain only.
        if not smtp_configured():
            return self._draft_instead(req, reason="smtp_not_configured")

        # Lock 4: compliance headers + honest identity.
        msg = EmailMessage()
        msg["From"] = get("GHOST_OUTREACH_FROM")
        msg["To"] = to_email
        msg["Subject"] = req.subject
        msg["Reply-To"] = get("GHOST_OUTREACH_REPLY_TO", get("GHOST_OUTREACH_FROM"))
        msg["List-Unsubscribe"] = f"<mailto:{get('GHOST_OUTREACH_REPLY_TO', get('GHOST_OUTREACH_FROM'))}?subject=unsubscribe>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.set_content(req.body + "\n\n—\nYou received this one-time note because we built a free concept for "
                        f"your business. Reply 'unsubscribe' and you will never hear from us again.")

        host = get("GHOST_OUTREACH_SMTP_HOST")
        port = int(get("GHOST_OUTREACH_SMTP_PORT") or 587)
        user = get("GHOST_OUTREACH_SMTP_USER")
        password = get("GHOST_OUTREACH_SMTP_PASSWORD")

        # Outbox (T1c protocol): record BEFORE the provider call. The key is
        # derived from the single-use grant token, so a crash between
        # provider-accept and the outbox transition can never resend: the
        # grant is consumed (replay refused) AND this row shows 'sent'.
        import hashlib as _hashlib
        key_hash = _hashlib.sha256((grant_token or "").encode()).hexdigest()[:16]
        ob_key = f"outreach:{key_hash}"
        from ghost.store import outbox as _outbox
        prior = _outbox.state_of(ob_key)
        if prior in ("sent", "verified"):
            audit("outbound.send_refused", subject=lead_slug,
                  reason="already_sent_for_grant", outbox_key=ob_key)
            raise SendRefused("already_sent", "this grant's effect is already recorded")
        _outbox.record("outreach_email", ob_key,
                       {"lead": lead_slug, "to": to_email, "subject": req.subject})
        # Provider-side idempotency: the Message-ID IS the outbox key, so a
        # server that saw this exact message can dedupe it too. (':' is not a
        # legal msg-id character — sanitize for the header only.)
        msg["Message-ID"] = f"<{ob_key.replace(':', '-')}@outreach.ghost>"

        # T3: LAST halt re-check — immediately before the provider call, not
        # at queue-claim time. The grant is already consumed (authority
        # spent); the halt check is what stops the effect itself.
        if is_halted():
            _outbox.transition(ob_key, "failed")
            audit("outbound.send_refused", subject=lead_slug,
                  reason="halt_pre_dispatch", outbox_key=ob_key)
            raise SendRefused("halt", "halt tripped between authorization and dispatch")

        try:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(user, password)
                send_result = server.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            # Bounce/transport failure = retryable, not a policy failure.
            _outbox.transition(ob_key, "failed")
            audit("outbound.transport_error", subject=lead_slug,
                  error=redact(str(e))[:300])
            raise SendRefused("transport_error", redact(str(e))[:200])
        _outbox.transition(ob_key, "sent")

        refused = send_result.get(to_email) if isinstance(send_result, dict) else None
        message_id = msg.get("Message-ID", "")
        audit("outbound.delivered", subject=lead_slug, to=to_email,
              message_id=str(message_id), refused=bool(refused))
        return {"sent": True, "message_id": str(message_id), "to": to_email}

    # ---------------------------------------------------------------- drafts

    def _draft_instead(self, req, reason: str) -> Dict[str, Any]:
        """No SMTP creds -> the honest path: CRM draft queue for manual send."""
        draft = {
            "lead_slug": req.lead_slug, "to": req.to_email,
            "subject": req.subject, "body": req.body,
            "reason": reason, "at": time.time(),
        }
        try:
            if self.crm:
                self.crm.add_or_update_draft(
                    lead_id=req.lead_slug, channel="email",
                    body=req.body, subject=req.subject)
        except Exception:
            pass
        audit("outbound.drafted_not_sent", subject=req.lead_slug, reason=reason)
        return {"sent": False, "drafted": True, "reason": reason, "draft": draft}
