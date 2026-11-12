"""Market Intelligence Continuous Monitoring Daemon.

Executes continuous surveillance across:
- Indian equities & indices
- US-listed Indian ADRs & currency divergence
- High-impact exchange & financial news announcements
- Automatic directional evaluation and proactive desktop alerts
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Set

from ghost.core.events import EventBus, get_event_bus
from ghost.market.analyst import MarketAnalyst
from ghost.market.data_feed import MarketDataFeed
from ghost.market.db import MarketDatabase, get_market_db
from ghost.market.news_monitor import NewsMonitor
from ghost.market.notifier import MarketNotifier
from ghost.market.universe import load_universe_config, seed_universe_to_db

logger = logging.getLogger("ghost.market.daemon")


class MarketDaemon:
    """Persistent surveillance daemon for financial markets."""

    def __init__(
        self,
        db: Optional[MarketDatabase] = None,
        event_bus: Optional[EventBus] = None,
        poll_interval_s: float = 60.0,
    ):
        self.db = db or get_market_db()
        self.event_bus = event_bus or get_event_bus()
        self.feed = MarketDataFeed(self.db)
        self.news_monitor = NewsMonitor(self.db, self.event_bus)
        self.analyst = MarketAnalyst(self.db, self.feed)
        self.notifier = MarketNotifier(self.db, self.event_bus)
        self.poll_interval_s = poll_interval_s
        self.running = False
        self._processed_news_ids: Set[str] = set()

    async def run_cycle(self) -> Dict[str, Any]:
        """Executes a single end-to-end monitoring cycle."""
        t0 = time.time()
        logger.info("Executing Market Intelligence surveillance cycle...")

        # 1. Sync universe configuration
        seed_universe_to_db(self.db)

        # 2. Ingest active instruments quotes
        instruments = self.db.get_instruments(active_only=True)
        symbols = [inst["symbol"] for inst in instruments]

        # Prioritize core indices, equities, ADRs, FX
        core_symbols = [
            "^NSEI", "^BSESN", "^NSEBANK", "USDINR=X",
            "INFY.NS", "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS",
            "INFY", "HDB", "IBN", "WIT", "BZ=F", "^TNX"
        ]
        active_syms = list(set(core_symbols + symbols[:25]))
        fetched_quotes = self.feed.fetch_quotes(active_syms)

        # 3. Calculate ADR metrics & arbitrage prem/disc
        adr_metrics = self.feed.calculate_adr_metrics()

        # 4. Ingest news feeds
        new_items = await self.news_monitor.run_sweep()

        # 5. Process CRITICAL and HIGH news for directional analysis & alerts
        triggered_alerts: List[Dict[str, Any]] = []

        for item in new_items:
            nid = item.get("id")
            if nid in self._processed_news_ids:
                continue
            self._processed_news_ids.add(nid)

            impact = item.get("impact", "LOW")
            if impact in ("CRITICAL", "HIGH"):
                # Determine primary target symbol
                item_syms = item.get("symbols", [])
                target_symbol = item_syms[0] if item_syms else "^NSEI"

                # Run in-depth directional analysis
                analysis = self.analyst.analyze_instrument(
                    symbol=target_symbol,
                    recent_news=[item],
                )

                # Send proactive desktop notification
                alert = await self.notifier.notify_market_alert(
                    symbol=target_symbol,
                    impact=impact,
                    headline=item["title"],
                    summary=item.get("summary", ""),
                    direction=analysis["direction"],
                    horizon=analysis["horizon"],
                    confidence=analysis["confidence"],
                    key_risk=analysis["risks"],
                    analysis_id=analysis["id"],
                    source=item.get("source", "Financial News Feed"),
                    timestamp=item.get("published_at"),
                    source_url=item.get("url", ""),
                )
                if alert:
                    triggered_alerts.append(alert)

        duration = round(time.time() - t0, 2)
        summary = {
            "status": "ok",
            "duration_s": duration,
            "quotes_updated": len(fetched_quotes),
            "adr_pairs_updated": len(adr_metrics),
            "new_news_items": len(new_items),
            "alerts_triggered": len(triggered_alerts),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }
        logger.info(f"Market surveillance cycle completed in {duration}s: {summary}")
        return summary

    async def run_loop(self) -> None:
        """Continuous background execution loop."""
        self.running = True
        logger.info(f"MarketDaemon background loop started with {self.poll_interval_s}s interval.")
        while self.running:
            try:
                await self.run_cycle()
            except Exception as e:
                logger.error(f"Error in MarketDaemon cycle: {e}", exc_info=True)

            slept = 0.0
            while slept < self.poll_interval_s and self.running:
                step = min(2.0, self.poll_interval_s - slept)
                await asyncio.sleep(step)
                slept += step

    def stop(self) -> None:
        self.running = False
        logger.info("MarketDaemon stopped.")


_GLOBAL_MARKET_DAEMON: Optional[MarketDaemon] = None


def get_market_daemon(poll_interval_s: float = 60.0) -> MarketDaemon:
    global _GLOBAL_MARKET_DAEMON
    if _GLOBAL_MARKET_DAEMON is None:
        _GLOBAL_MARKET_DAEMON = MarketDaemon(poll_interval_s=poll_interval_s)
    return _GLOBAL_MARKET_DAEMON
