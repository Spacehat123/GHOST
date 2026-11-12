"""Market Intelligence Alert & Proactive Desktop Notification Engine.

Delivers formatted, institutional alerts for CRITICAL and HIGH market events
via Linux desktop notifications (notify-send) and persistent SQLite records.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
import uuid
from typing import Any, Dict, Optional

from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.market.db import MarketDatabase, get_market_db

logger = logging.getLogger("ghost.market.notifier")


class MarketNotifier:
    """Delivers formatted market notifications to desktop HUD and audit trail."""

    def __init__(self, db: Optional[MarketDatabase] = None, event_bus: Optional[EventBus] = None):
        self.db = db or get_market_db()
        self.event_bus = event_bus or get_event_bus()

    def format_alert_text(
        self,
        impact: str,
        symbol: str,
        direction: str,
        headline: str,
        summary: str,
        horizon: str,
        confidence: str,
        key_risk: str,
        source: str,
        timestamp: str,
    ) -> Tuple[str, str]:
        """Generates the title and structured body adhering to user specification."""
        title = f"MARKET ALERT — {impact.upper()}"
        body = (
            f"{symbol}: Potentially {direction.upper()}.\n"
            f"Event: {headline}\n"
            f"Impact: {summary[:140]}...\n"
            f"Horizon: {horizon}. Confidence: {confidence}. Key risk: {key_risk[:100]}.\n"
            f"Source: {source} Timestamp: {timestamp}"
        )
        return title, body

    def send_desktop_notification(self, title: str, body: str, urgency: str = "normal") -> bool:
        """Executes notify-send to present notification on active Linux desktop."""
        clean_urgency = "critical" if urgency.lower() == "critical" else "normal"
        try:
            # Check DISPLAY / WAYLAND_DISPLAY
            env = os.environ.copy()
            if "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env:
                env["DISPLAY"] = ":0"

            res = subprocess.run(
                ["notify-send", "-a", "GHOST Market Intelligence", "-u", clean_urgency, title, body],
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return res.returncode == 0
        except Exception as e:
            logger.warning(f"notify-send failed: {e}")
            return False

    async def notify_market_alert(
        self,
        symbol: str,
        impact: str,
        headline: str,
        summary: str,
        direction: str = "NEUTRAL",
        horizon: str = "SHORT_TERM",
        confidence: str = "MEDIUM",
        key_risk: str = "Market volatility",
        analysis_id: Optional[str] = None,
        source: str = "GHOST Market Engine",
        timestamp: Optional[str] = None,
        source_url: str = "",
    ) -> Dict[str, Any]:
        """Saves alert to DB, broadcasts on EventBus, and delivers desktop notification."""
        impact_upper = impact.upper()
        if impact_upper not in ("CRITICAL", "HIGH"):
            logger.debug(f"Skipping alert for impact {impact_upper}; only CRITICAL and HIGH trigger notifications.")
            return {}

        now_str = timestamp or time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        title, body = self.format_alert_text(
            impact=impact_upper,
            symbol=symbol,
            direction=direction,
            headline=headline,
            summary=summary,
            horizon=horizon,
            confidence=confidence,
            key_risk=key_risk,
            source=source,
            timestamp=now_str,
        )

        alert_id = f"alt_{uuid.uuid4().hex[:12]}"
        alert_record = {
            "id": alert_id,
            "symbol": symbol,
            "impact": impact_upper,
            "headline": headline,
            "summary": summary,
            "direction": direction,
            "horizon": horizon,
            "confidence": confidence,
            "key_risk": key_risk,
            "analysis_id": analysis_id,
            "source_url": source_url,
            "notified_at": time.time(),
        }

        # 1. Save to SQLite
        self.db.save_alert(alert_record)

        # 2. Fire Linux desktop notification
        urgency = "critical" if impact_upper == "CRITICAL" else "normal"
        delivered = self.send_desktop_notification(title, body, urgency=urgency)
        alert_record["desktop_delivered"] = delivered

        # 3. Publish to EventBus
        try:
            sev = EventSeverity.CRITICAL if impact_upper == "CRITICAL" else EventSeverity.WARNING
            await self.event_bus.publish(Event(
                event_type="market.alert",
                source="ghost.market.notifier",
                severity=sev,
                payload=alert_record,
            ))
        except Exception as e:
            logger.debug(f"EventBus dispatch failed for alert: {e}")

        logger.info(f"Delivered {impact_upper} market alert for {symbol}: {headline}")
        return alert_record
