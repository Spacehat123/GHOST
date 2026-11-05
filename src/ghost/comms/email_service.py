"""Email Service — read-only inbox (IMAP) + draft queue for the operator.

This service CANNOT send email, by design: the only sending path in Ghost is
the effects-plane ColdOutreachSender (autonomy/sender.py), gated by grant
tokens, the halt switch, suppression and SMTP credentials. Everything here
either reads the inbox or prepares drafts in the CRM review queue.
"""
import os
import json
import imaplib
import email
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("ghost.comms.email")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
CONFIG_PATH = os.path.join(REPO_DIR, "configs", "email.json")

class EmailService:
    _instance: Optional["EmailService"] = None

    def __init__(self, config_path: str = CONFIG_PATH):
        self.config_path = config_path
        self._config = self._load_config()

    @classmethod
    def get_instance(cls) -> "EmailService":
        if cls._instance is None:
            cls._instance = EmailService()
        return cls._instance

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed loading email config: {e}")
        return {}

    @staticmethod
    def _clean_header(header_val: Optional[str]) -> str:
        if not header_val:
            return ""
        decoded_parts = decode_header(header_val)
        res = []
        for content, encoding in decoded_parts:
            if isinstance(content, bytes):
                res.append(content.decode(encoding or "utf-8", errors="replace"))
            else:
                res.append(str(content))
        return "".join(res)

    def check_inbox(self, unread_only: bool = True, limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch recent emails from inbox via IMAP SSL."""
        cfg = self._config or self._load_config()
        user = cfg.get("email")
        pwd = cfg.get("app_password", "").replace(" ", "")
        server = cfg.get("imap_server", "imap.gmail.com")
        port = int(cfg.get("imap_port", 993))

        if not user or not pwd:
            logger.warning("Email credentials missing in email.json")
            return []

        emails = []
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(server, port, timeout=10)
            mail.login(user, pwd)
            mail.select("INBOX")

            search_crit = "UNSEEN" if unread_only else "ALL"
            status, messages = mail.search(None, search_crit)
            if status != "OK" or not messages[0]:
                return []

            msg_ids = messages[0].split()
            # Fetch most recent messages up to limit
            target_ids = msg_ids[-limit:]

            for mid in reversed(target_ids):
                res, data = mail.fetch(mid, "(RFC822)")
                if res != "OK":
                    continue
                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject = self._clean_header(msg.get("Subject", "No Subject"))
                from_hdr = self._clean_header(msg.get("From", ""))
                date_hdr = msg.get("Date", "")

                # Extract sender email address
                sender_email = ""
                if "<" in from_hdr and ">" in from_hdr:
                    sender_email = from_hdr.split("<")[1].split(">")[0].strip()
                else:
                    sender_email = from_hdr.strip()

                # Extract body preview
                body_text = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        content_disposition = str(part.get("Content-Disposition"))
                        if content_type == "text/plain" and "attachment" not in content_disposition:
                            payload = part.get_payload(decode=True)
                            if payload:
                                body_text = payload.decode(errors="replace")
                                break
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode(errors="replace")

                emails.append({
                    "id": mid.decode(),
                    "sender": from_hdr,
                    "sender_email": sender_email.lower(),
                    "subject": subject,
                    "date": date_hdr,
                    "body": body_text.strip()[:600]
                })

            return emails
        except Exception as e:
            logger.error(f"Error checking email inbox: {e}")
            return []
        finally:
            if mail:
                try:
                    mail.close()
                    mail.logout()
                except Exception:
                    pass

    def draft_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        crm: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """The honest capability: prepare a draft in the CRM review queue.
        Returns {"drafted": True, "sent": False, ...}; NEVER sends. If the CRM
        is unreachable, the draft is appended to data/drafts.jsonl so it cannot
        be lost — and that degradation is audited, not silent."""
        record: Dict[str, Any] = {
            "to": to_email, "subject": subject, "body": body,
            "html_body": html_body, "at": time.time(),
        }
        try:
            from ghost.crm.db import CRMDatabase
            crm = crm or CRMDatabase.get_instance()
            result = crm.add_or_update_draft(lead_id=to_email, channel="email",
                                             body=body, subject=subject)
            record["draft_id"] = result.get("id")
            record["queue"] = "crm"
        except Exception as e:
            path = os.path.join(REPO_DIR, "data", "drafts.jsonl")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            record.update({"queue": "file", "path": path, "crm_error": str(e)[:200]})
        from ghost.core.audit import audit
        audit("email.drafted", subject=to_email, queue=record.get("queue"),
              draft_id=record.get("draft_id"))
        record["drafted"] = True
        record["sent"] = False
        return record
