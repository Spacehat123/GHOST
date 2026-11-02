"""Autonomous AGI Watchdog & Signal Bus for Ghost Agency Operations.
Dual-Trigger Architecture:
1. Event-Driven Signal Listener: Fast webhook / event queue for incoming client comms
   (WhatsApp, Email, Slack) and worker completion signals (OpenCode / DeepSeek).
2. Periodic Operations Patrol: Background task running every 30m auditing pipeline health,
   flagging stale client replies (>24h), and logging summaries to Obsidian Daily Log.
"""
import asyncio
import os
import time
import logging
from typing import Dict, Any, List, Optional, Callable
from ghost.crm.db import CRMDatabase

logger = logging.getLogger("ghost.watchdog")

DAILY_LOG_PATH = "/home/pranc/Obsidian Vault/Ghost/Daily Log.md"

class AutonomousWatchdog:
    _instance: Optional["AutonomousWatchdog"] = None

    def __init__(self, patrol_interval_seconds: int = 86400):  # 24-hour periodic sweep cadence
        self.patrol_interval = patrol_interval_seconds
        self.is_running = False
        self._patrol_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._broadcast_callbacks: List[Callable[[Dict[str, Any]], Any]] = []

    @classmethod
    def get_instance(cls) -> "AutonomousWatchdog":
        if cls._instance is None:
            cls._instance = AutonomousWatchdog()
        return cls._instance

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
        Immediately notifies the operator via Desktop HUD and WebSocket broadcast.
        """
        event = {
            "type": "watchdog_event",
            "event_type": event_type,
            "source": source,
            "summary": summary,
            "payload": payload or {},
            "urgency": urgency,
            "timestamp": time.time(),
            "time_str": time.strftime("%H:%M:%S")
        }

        logger.info(f"Watchdog Signal [{source} -> {event_type}]: {summary}")

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
        client_slug = (payload or {}).get("client_slug")
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
                    # Log inbound email to CRM interaction history and Obsidian note
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
                    # Generic high-priority alert if contains hospitality/client keywords
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

            # Flag clients waiting on reply in pitch_sent or negotiating stages
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

        # If stale deals detected, trigger high-priority alert
        if stale_clients:
            stale_summary = ", ".join([f"{s['name']} ({s['hours_idle']}h)" for s in stale_clients[:3]])
            await self.emit_signal(
                event_type="client_followup_required",
                source="crm_patrol",
                summary=f"Stale client follow-up needed: {stale_summary}",
                payload={"stale_clients": stale_clients},
                urgency="critical"
            )

        # 3. Proactive Vercel Token Expiry Check
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

        # Append sweep summary to Obsidian Daily Log
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

        date_hdr = time.strftime("%Y-%m-%d")
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
