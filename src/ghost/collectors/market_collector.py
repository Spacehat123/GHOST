"""Market Intelligence Collector for GHOST Environment Perception.

Passively samples quotes, monitors financial developments, computes ADR metrics,
and publishes structured market events into the universal EventBus.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from ghost.core.events import EventBus, get_event_bus
from ghost.market.daemon import MarketDaemon, get_market_daemon

logger = logging.getLogger("ghost.collectors.market")


class MarketCollector:
    """Passively samples market indicators and publishes market events."""

    def __init__(self, event_bus: Optional[EventBus] = None):
        self.event_bus = event_bus or get_event_bus()
        self.daemon = get_market_daemon()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def run_loop(self, interval_s: float = 60.0) -> None:
        """Runs the periodic market surveillance collector loop."""
        self._running = True
        logger.info(f"MarketCollector started (interval={interval_s}s)")
        while self._running:
            try:
                await self.daemon.run_cycle()
            except Exception as e:
                logger.error(f"MarketCollector error: {e}", exc_info=True)

            slept = 0.0
            while slept < interval_s and self._running:
                step = min(2.0, interval_s - slept)
                await asyncio.sleep(step)
                slept += step

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("MarketCollector stopped.")
