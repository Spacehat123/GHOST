"""Autonomous AGI Watchdog & Cognitive Signal Bus for Ghost Agency Operations.
Dual-Trigger Architecture:
1. Event-Driven Signal Listener: Fast webhook / event queue for incoming client comms
   (WhatsApp, Email, Slack) and worker completion signals with lossless SQLite persistence.
2. Periodic Operations Patrol: Background task running every 30m auditing pipeline health,
   flagging stale client replies (>24h), and logging summaries to Obsidian Daily Log.
"""
import asyncio
import os
import time
import json
import sqlite3
import hashlib
import logging
from typing import Dict, Any, List, Optional, Callable
from ghost.crm.db import CRMDatabase

logger = logging.getLogger("ghost.watchdog")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
DATA_DIR = os.path.join(REPO_DIR, "data")
EVENTS_DB_PATH = os.environ.get("GHOST_EVENTS_DB", os.path.join(DATA_DIR, "events.db"))
DAILY_LOG_PATH = os.environ.get("OBSIDIAN_DAILY_LOG_PATH", os.path.expanduser("~/Obsidian Vault/Ghost/Daily Log.md"))
DEFAULT_PATROL_INTERVAL = int(os.environ.get("GHOST_WATCHDOG_INTERVAL", "1800"))  # 30 minutes default cadence

class AutonomousWatchdog:
    _instance: Optional["AutonomousWatchdog"] = None

    def __init__(self, patrol_interval_seconds: Optional[int] = None, db_path: Optional[str] = None):
        self.patrol_interval = patrol_interval_seconds or DEFAULT_PATROL_INTERVAL
        self.db_path = db_path or EVENTS_DB_PATH
        self.is_running = False
        self._patrol_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._broadcast_callbacks: List[Callable[[Dict[str, Any]], Any]] = []
        self._init_db()

    @classmethod
    def get_instance(cls) -> "AutonomousWatchdog":
        if cls._instance is None:
            cls._instance = AutonomousWatchdog()
        return cls._instance

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS system_events (
                    id TEXT PRIMARY KEY,
                    signature TEXT UNIQUE,
                    event_type TEXT,
                    source TEXT,
                    summary TEXT,
                    payload_json TEXT,
                    urgency TEXT,
                    occurrence_count INTEGER DEFAULT 1,
                    first_seen_at REAL,
                    last_seen_at REAL,
                    occurrences_json TEXT,
                    status TEXT DEFAULT 'unprocessed'
                )
                """)
        finally:
            conn.close()

    def register_broadcast_callback(self, cb: Callable[[Dict[str, Any]], Any]):
        """Register listener (e.g. WebSocket broadcast) for real-time notifications."""
        if cb not in self._broadcast_callbacks:
            self._broadcast_callbacks.append(cb)

    async def emit_signal(
        self,
        event_type: str,
        source: str,
        summary: str,
        payload: Optional[Dict[str, Any]] = None,
        urgency: str = "normal"
    ) -> Dict[str, Any]:
        """
        Ingest an operational event (client message, worker completion, deal alert).
        Performs lossless persistence in SQLite with signature deduplication.
        Preserves trace history of multiple occurrences without silent dropping.
        """
        now = time.time()
        payload_dict = payload or {}
        payload_str = json.dumps(payload_dict, sort_keys=True)
        payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()[:16]
        signature = hashlib.sha256(f"{source}:{event_type}:{payload_hash}".encode()).hexdigest()

        event_id = f"evt_{int(now * 1000)}"
        occurrence_count = 1

        # Check existing signature for lossless deduplication
        conn = self._get_connection()
        try:
            with conn:
                existing = conn.execute(
                    "SELECT * FROM system_events WHERE signature = ?", (signature,)
                ).fetchone()

                if existing:
                    # Event is a duplicate: update count, last_seen_at, and append to occurrences history
                    event_id = existing["id"]
                    occurrence_count = existing["occurrence_count"] + 1
                    try:
                        occ_list = json.loads(existing["occurrences_json"] or "[]")
                    except Exception:
                        occ_list = []
                    occ_list.append({"timestamp": now, "summary": summary})

                    conn.execute("""
                    UPDATE system_events
                    SET occurrence_count = ?, last_seen_at = ?, occurrences_json = ?
                    WHERE signature = ?
                    """, (occurrence_count, now, json.dumps(occ_list), signature))
                    logger.info(
                        f"Watchdog Duplicate Signal Recorded [{source} -> {event_type}]: "
                        f"Occurrence #{occurrence_count} for signature {signature[:8]}."
                    )
                else:
                    # New event
                    occ_list = [{"timestamp": now, "summary": summary}]
                    conn.execute("""
                    INSERT INTO system_events (
                        id, signature, event_type, source, summary, payload_json, urgency,
                        occurrence_count, first_seen_at, last_seen_at, occurrences_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event_id, signature, event_type, source, summary, payload_str, urgency,
                        1, now, now, json.dumps(occ_list), "unprocessed"
                    ))
                    logger.info(f"Watchdog Signal Persisted [{source} -> {event_type}]: {summary}")
        finally:
            conn.close()

        event = {
            "type": "watchdog_event",
            "id": event_id,
            "signature": signature,
            "event_type": event_type,
            "source": source,
            "summary": summary,
            "payload": payload_dict,
            "urgency": urgency,
            "occurrence_count": occurrence_count,
            "timestamp": now,
            "time_str": time.strftime("%H:%M:%S")
        }

        # Send native desktop notification via notify-send
        try:
            from ghost.mcp.bridge import SystemAppBridge
            await SystemAppBridge.desktop_notify(
                title=f"Ghost Recon [{source.upper()}]",
                message=summary,
                urgency=urgency
            )
        except Exception as e:
            logger.warning(f"Could not send desktop notification for signal: {e}")

        # Broadcast event to connected GUIs
        for cb in list(self._broadcast_callbacks):
            try:
                res = cb(event)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.warning(f"Error in watchdog broadcast callback: {e}")

        # Log into CRM interactions if linked to a client
        client_slug = payload_dict.get("client_slug")
        if client_slug:
            try:
                crm = CRMDatabase.get_instance()
                crm.log_interaction(
                    client_slug=client_slug,
                    channel=source,
                    direction="inbound",
                    content=summary
                )
            except Exception as e:
                logger.warning(f"Could not log interaction for signal: {e}")

        return event

    def get_unprocessed_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieve pending events requiring cognitive assessment."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM system_events WHERE status = 'unprocessed' ORDER BY last_seen_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_event_processed(self, event_id: str, status: str = "processed") -> None:
        """Mark an event as handled."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    "UPDATE system_events SET status = ? WHERE id = ?",
                    (status, event_id)
                )
        finally:
            conn.close()

    def start_patrol(self):
        """Start the background operations patrol loop."""
        if self.is_running:
            return
        self.is_running = True
        self._patrol_task = asyncio.create_task(self._patrol_loop())
        logger.info(f"Autonomous AGI Watchdog patrol loop started (interval: {self.patrol_interval}s).")

    def stop_patrol(self):
        self.is_running = False
        if self._patrol_task and not self._patrol_task.done():
            self._patrol_task.cancel()
        logger.info("Autonomous AGI Watchdog patrol stopped.")

    async def _patrol_loop(self):
        """Periodic background operations patrol checking client staleness and pipeline health."""
        while self.is_running:
            try:
                await self.run_patrol_sweep()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error during watchdog patrol sweep: {e}", exc_info=True)

            try:
                await asyncio.sleep(self.patrol_interval)
            except asyncio.CancelledError:
                break

    async def _check_incoming_client_emails(self):
        """Scans unread inbox emails and matches them to CRM client accounts."""
        try:
            from ghost.comms.email_service import EmailService
            es = EmailService.get_instance()
            unread_emails = await asyncio.to_thread(es.check_inbox, unread_only=True, limit=5)
            if not unread_emails:
                return

            crm = CRMDatabase.get_instance()
            all_clients = crm.list_clients()

            # Map client email -> client record
            client_by_email = {}
            for c in all_clients:
                if c.get("email"):
                    client_by_email[c["email"].strip().lower()] = c

            for msg in unread_emails:
                sender_email = msg.get("sender_email", "")
                subject = msg.get("subject", "")
                body_snippet = msg.get("body", "")

                matched_client = client_by_email.get(sender_email)
                if matched_client:
                    slug = matched_client["slug"]
                    crm.log_interaction(
                        client_slug=slug,
                        channel="email",
                        direction="inbound",
                        content=f"Subject: {subject} | {body_snippet[:150]}"
                    )
                    await self.emit_signal(
                        event_type="client_email_received",
                        source="email",
                        summary=f"Inbound email from client {matched_client['business_name']}: '{subject}'",
                        payload={"client_slug": slug, "email": sender_email, "subject": subject, "body": body_snippet},
                        urgency="normal"
                    )
                else:
                    lower_subj = subject.lower()
                    if any(k in lower_subj for k in ["booking", "quote", "website", "proposal", "contract"]):
                        await self.emit_signal(
                            event_type="prospect_email_received",
                            source="email",
                            summary=f"New potential client inquiry from {sender_email}: '{subject}'",
                            payload={"sender": sender_email, "subject": subject, "body": body_snippet},
                            urgency="normal"
                        )
        except Exception as e:
            logger.warning(f"Watchdog email poll error: {e}")

    async def run_patrol_sweep(self) -> Dict[str, Any]:
        """
        Execute one operational audit sweep:
        1. Scans incoming unread emails and matches them to CRM client accounts.
        2. Checks for stale client pitches/negotiations (>24h without follow-up).
        3. Calculates pipeline metrics (pipeline count, total pipeline value).
        4. Appends audit entry into Obsidian Daily Log.
        """
        logger.info("Executing Autonomous Operations Patrol sweep...")
        await self._check_incoming_client_emails()

        crm = CRMDatabase.get_instance()
        clients = crm.list_clients()
        now = time.time()
        one_day = 86400

        stale_clients = []
        total_pipeline_val = 0.0
        active_counts = {}

        for c in clients:
            status = c.get("status", "unscouted")
            active_counts[status] = active_counts.get(status, 0) + 1
            deal_val = float(c.get("deal_value") or 0.0)
            total_pipeline_val += deal_val

            if status in ["pitch_sent", "negotiating"]:
                last_updated = c.get("updated_at") or now
                if now - last_updated > one_day:
                    stale_clients.append({
                        "slug": c.get("slug"),
                        "name": c.get("business_name"),
                        "status": status,
                        "hours_idle": int((now - last_updated) / 3600),
                        "deal_value": deal_val
                    })

        if stale_clients:
            stale_summary = ", ".join([f"{s['name']} ({s['hours_idle']}h)" for s in stale_clients[:3]])
            await self.emit_signal(
                event_type="client_followup_required",
                source="crm_patrol",
                summary=f"Stale client follow-up needed: {stale_summary}",
                payload={"stale_clients": stale_clients},
                urgency="critical"
            )

        # Proactive Vercel Token Expiry Check
        try:
            from ghost.workers.deploy import VercelDeployer
            v_status = await VercelDeployer.check_token_status()
            if v_status.get("status") == "expired":
                await self.emit_signal(
                    event_type="vercel_token_expired",
                    source="vercel_patrol",
                    summary=f"CRITICAL: Vercel Personal Access Token has EXPIRED (HTTP {v_status.get('code', 401)}). Immediate 30-day PAT renewal required.",
                    payload=v_status,
                    urgency="critical"
                )
        except Exception as e:
            logger.warning(f"Watchdog vercel token probe note: {e}")

        sweep_report = {
            "timestamp": now,
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_clients": len(clients),
            "pipeline_value": total_pipeline_val,
            "status_breakdown": active_counts,
            "stale_clients": stale_clients
        }

        self._append_sweep_to_obsidian(sweep_report)
        return sweep_report

    def _append_sweep_to_obsidian(self, report: Dict[str, Any]):
        """Append patrol audit metrics cleanly to Obsidian Daily Log."""
        if not os.path.exists(DAILY_LOG_PATH):
            return

        time_hdr = time.strftime("%H:%M:%S")
        stale_line = f"- [ ] **Follow-up Alert:** {len(report['stale_clients'])} deals pending response >24h." if report['stale_clients'] else "- [x] Pipeline responsive (0 stale leads)."

        entry = (
            f"\n### Operations Patrol Sweep — {time_hdr}\n"
            f"- **Active Pipeline**: {report['total_clients']} accounts | **Total Quoted Value**: ${report['pipeline_value']:,.2f}\n"
            f"- **Stages**: " + ", ".join([f"{k}: {v}" for k, v in report['status_breakdown'].items()]) + "\n"
            f"{stale_line}\n"
        )

        try:
            with open(DAILY_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.info("Watchdog patrol entry appended to Obsidian Daily Log.")
        except Exception as e:
            logger.error(f"Failed to append patrol log to Obsidian: {e}")
