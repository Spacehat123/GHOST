"""Market Intelligence Specialized Agent for GHOST Universal Intelligence System.

Conforms to:
- INTERFACE_CONTRACTS.md §4 (AgentContract)
- DETERMINISTIC AGENT ROUTING & CONTEXT ISOLATION
- Zero trade placement, zero broker orders, zero funds transfer.
- Verifiable institutional financial intelligence.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from ghost.agents.base import AgentContract, VerificationEvidence
from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.core.task_orchestrator import Task, TaskStatus
from ghost.market.analyst import MarketAnalyst
from ghost.market.daemon import get_market_daemon
from ghost.market.data_feed import MarketDataFeed
from ghost.market.db import MarketDatabase, get_market_db

logger = logging.getLogger("ghost.agents.market")


class MarketIntelligenceAgent(AgentContract):
    """Specialized agent responsible for financial markets surveillance and directional analysis."""

    def __init__(self, event_bus: Optional[EventBus] = None):
        super().__init__(
            agent_id="market_agent",
            name="Market Intelligence Agent",
            responsibility=(
                "Continuously monitors Indian equities, benchmark indices, US-listed ADR divergence, "
                "macro factors, and regulatory announcements. Produces institutional directional analyses "
                "with explicit invalidation levels, horizons, and risk profiles."
            ),
            supported_tasks=[
                "market_surveillance",
                "instrument_analysis",
                "adr_arbitrage_scan",
                "news_impact_assessment",
                "morning_briefing",
            ],
            required_permissions=["market.read", "notification.send"],
            event_bus=event_bus,
        )
        self.db = get_market_db()
        self.feed = MarketDataFeed(self.db)
        self.analyst = MarketAnalyst(self.db, self.feed)
        self.daemon = get_market_daemon()

    async def plan_task(self, objective: Dict[str, Any]) -> List[Task]:
        """Decomposes a market query or objective into verifiable sub-tasks."""
        obj_id = objective.get("objective_id", f"obj_mkt_{int(time.time())}")
        task_type = objective.get("task_type", "market_surveillance")
        symbol = objective.get("symbol", "^NSEI")

        task = Task(
            task_id=f"task_mkt_{int(time.time() * 1000)}",
            objective_id=obj_id,
            assigned_agent=self.agent_id,
            title=f"Market Intelligence: {task_type} ({symbol})",
            description=objective.get("description", f"Execute {task_type} for {symbol}"),
            status=TaskStatus.PLANNED,
            input_payload=objective,
            required_permissions=self.required_permissions,
            timeout_s=90.0,
        )
        return [task]

    async def execute_task(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        """Executes market surveillance, technical analysis, or ADR tracking."""
        payload = task.input_payload or {}
        task_type = payload.get("task_type", "market_surveillance")
        symbol = payload.get("symbol")

        if task_type == "instrument_analysis" or symbol:
            sym = symbol or "^NSEI"
            analysis = self.analyst.analyze_instrument(sym)
            quote = self.db.get_quote(sym)
            return {
                "status": "success",
                "task_type": "instrument_analysis",
                "symbol": sym,
                "analysis": analysis,
                "quote": quote,
                "timestamp": time.time(),
            }

        elif task_type == "adr_arbitrage_scan":
            adr_pairs = self.feed.calculate_adr_metrics()
            return {
                "status": "success",
                "task_type": "adr_arbitrage_scan",
                "adr_pairs": adr_pairs,
                "timestamp": time.time(),
            }

        elif task_type == "news_impact_assessment":
            impact = payload.get("impact", "HIGH")
            news = self.db.get_news(limit=20, impact=impact)
            return {
                "status": "success",
                "task_type": "news_impact_assessment",
                "count": len(news),
                "news": news,
                "timestamp": time.time(),
            }

        elif task_type == "morning_briefing":
            cycle_result = await self.daemon.run_cycle()
            quotes = self.db.get_quotes(["^NSEI", "^BSESN", "^NSEBANK", "USDINR=X"])
            adrs = self.db.get_adr_pairs()
            critical_news = self.db.get_news(limit=5, impact="CRITICAL")
            high_news = self.db.get_news(limit=5, impact="HIGH")
            return {
                "status": "success",
                "task_type": "morning_briefing",
                "benchmark_quotes": quotes,
                "adr_divergence": adrs,
                "critical_announcements": critical_news,
                "high_announcements": high_news,
                "cycle_summary": cycle_result,
                "timestamp": time.time(),
            }

        else:
            # Default market surveillance sweep
            cycle_result = await self.daemon.run_cycle()
            quotes = self.db.get_quotes(["^NSEI", "^BSESN", "^NSEBANK"])
            return {
                "status": "success",
                "task_type": "market_surveillance",
                "cycle_result": cycle_result,
                "key_indices": quotes,
                "timestamp": time.time(),
            }

    async def verify_outcome(self, task: Task, output: Dict[str, Any]) -> VerificationEvidence:
        """Verifies that market intelligence outputs meet institutional standards."""
        if output.get("status") != "success":
            return VerificationEvidence(
                verified=False,
                evidence_type="readback",
                artifacts=[],
                details={"error": "Market agent execution status was not success"},
            )

        task_type = output.get("task_type")
        if task_type == "instrument_analysis":
            analysis = output.get("analysis", {})
            verified = (
                bool(analysis.get("direction")) and
                bool(analysis.get("key_levels")) and
                bool(analysis.get("invalidation"))
            )
            return VerificationEvidence(
                verified=verified,
                evidence_type="readback",
                artifacts=[],
                details={
                    "symbol": output.get("symbol"),
                    "direction": analysis.get("direction"),
                    "confidence": analysis.get("confidence"),
                },
            )

        elif task_type == "adr_arbitrage_scan":
            adr_pairs = output.get("adr_pairs", [])
            verified = len(adr_pairs) > 0 and all("implied_inr" in p for p in adr_pairs)
            return VerificationEvidence(
                verified=verified,
                evidence_type="readback",
                artifacts=[],
                details={"pairs_verified": len(adr_pairs)},
            )

        return VerificationEvidence(
            verified=True,
            evidence_type="readback",
            artifacts=[],
            details={"task_type": task_type, "timestamp": output.get("timestamp")},
        )
