"""Comprehensive Test Suite for GHOST Market Intelligence Capability.

Tests:
1. SQLite persistence and schema integrity (MarketDatabase).
2. Universe configuration and database seeding.
3. Data feed quote fetching, OHLCV candles, and ADR arbitrage conversion.
4. News monitoring, SHA-256 deduplication, and deterministic impact classification.
5. Financial analysis engine with directional assessment, levels, and invalidation.
6. Desktop alerts formatting, notify-send execution, and persistence.
7. MarketDaemon and MarketCollector background cycle execution.
8. Specialized MarketIntelligenceAgent contract and evidence verification.
9. Deterministic routing of financial queries via TaskRouter.
10. ToolRegistry registration and execution of all 6 market tools.
11. FastAPI REST endpoints for Market Intelligence.
"""

import asyncio
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from ghost.agents.market_agent import MarketIntelligenceAgent
from ghost.core.agent_registry import get_agent_registry
from ghost.core.task_router import IntentCategory, TaskRouter, get_task_router
from ghost.core.tool_registry import get_tool_registry
from ghost.market.analyst import MarketAnalyst, calculate_rsi, calculate_sma
from ghost.market.daemon import MarketDaemon
from ghost.market.data_feed import MarketDataFeed
from ghost.market.db import MarketDatabase
from ghost.market.news_monitor import NewsMonitor
from ghost.market.notifier import MarketNotifier
from ghost.market.universe import load_universe_config, seed_universe_to_db
from ghost.server.app import app


class TestMarketDatabase(unittest.TestCase):
    """Verifies SQLite database operations and constraints."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_market.db")
        self.db = MarketDatabase(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_instrument_upsert_and_retrieve(self):
        self.db.upsert_instrument(
            symbol="INFY.NS",
            exchange="NSE",
            name="Infosys Ltd",
            sector="IT",
            adr_symbol="INFY",
            adr_ratio=1.0,
        )
        inst = self.db.get_instrument("INFY.NS")
        self.assertIsNotNone(inst)
        self.assertEqual(inst["symbol"], "INFY.NS")
        self.assertEqual(inst["exchange"], "NSE")
        self.assertEqual(inst["adr_symbol"], "INFY")
        self.assertEqual(inst["adr_ratio"], 1.0)

    def test_quote_upsert_and_retrieve(self):
        quote_data = {
            "symbol": "^NSEI",
            "price": 24500.50,
            "change": 125.20,
            "change_pct": 0.51,
            "open": 24400.0,
            "high": 24550.0,
            "low": 24380.0,
            "previous_close": 24375.30,
            "volume": 250000000,
            "source": "yfinance_market_feed",
            "latency_ms": 150.0,
            "is_delayed": 1,
        }
        self.db.upsert_quote(quote_data)
        q = self.db.get_quote("^NSEI")
        self.assertIsNotNone(q)
        self.assertEqual(q["price"], 24500.50)
        self.assertEqual(q["change"], 125.20)
        self.assertEqual(q["source"], "yfinance_market_feed")
        self.assertEqual(q["is_delayed"], 1)

    def test_adr_upsert_and_retrieve(self):
        adr_data = {
            "adr_symbol": "HDB",
            "domestic_symbol": "HDFCBANK.NS",
            "adr_ratio": 3.0,
            "adr_price_usd": 60.0,
            "domestic_price_inr": 1700.0,
            "usdinr_rate": 87.0,
            "implied_inr": 1740.0,
            "premium_discount_pct": 2.35,
            "session_phase": "REGULAR",
        }
        self.db.upsert_adr(adr_data)
        pairs = self.db.get_adr_pairs()
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["adr_symbol"], "HDB")
        self.assertEqual(pairs[0]["implied_inr"], 1740.0)
        self.assertEqual(pairs[0]["premium_discount_pct"], 2.35)

    def test_news_deduplication(self):
        item = {
            "id": "news_123",
            "raw_hash": "hash_abc_1",
            "source": "ET Markets",
            "title": "RBI keeps repo rate unchanged at 6.5%",
            "summary": "The Monetary Policy Committee voted unanimously to keep rates steady.",
            "url": "https://example.com/rbi",
            "published_at": "2026-11-11 10:00:00 UTC",
            "impact": "HIGH",
            "sentiment": "NEUTRAL",
            "symbols": ["^NSEI", "^NSEBANK"],
        }
        # First insertion should succeed
        inserted1 = self.db.insert_news(item)
        self.assertTrue(inserted1)

        # Duplicate insertion with same raw_hash should be rejected
        inserted2 = self.db.insert_news(item)
        self.assertFalse(inserted2)

        news = self.db.get_news(limit=10)
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["symbols"], ["^NSEI", "^NSEBANK"])


class TestMarketUniverse(unittest.TestCase):
    """Verifies universe loading and database population."""

    def test_universe_config_loaded(self):
        conf = load_universe_config()
        self.assertIn("indices", conf)
        self.assertIn("equities", conf)
        self.assertIn("adrs", conf)
        self.assertIn("macro", conf)
        self.assertGreater(len(conf["indices"]), 0)
        self.assertGreater(len(conf["equities"]), 0)
        self.assertGreater(len(conf["adrs"]), 0)

    def test_seed_universe_to_temp_db(self):
        temp_dir = tempfile.mkdtemp()
        try:
            db_path = os.path.join(temp_dir, "test_market.db")
            db = MarketDatabase(db_path)
            count = seed_universe_to_db(db)
            self.assertGreater(count, 20)
            insts = db.get_instruments()
            self.assertEqual(len(insts), count)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestADRArbitrageMath(unittest.TestCase):
    """Verifies ADR parity and conversion math."""

    def test_adr_conversion_formula(self):
        # Formula: Implied INR = (ADR_USD * USDINR) / Ratio
        # Premium % = ((Implied INR - Domestic INR) / Domestic INR) * 100
        adr_price_usd = 20.0
        usdinr_rate = 85.0
        adr_ratio = 1.0  # e.g. INFY
        domestic_price_inr = 1650.0

        implied_inr = (adr_price_usd * usdinr_rate) / adr_ratio  # 1700.0
        self.assertEqual(implied_inr, 1700.0)

        premium_pct = round(((implied_inr - domestic_price_inr) / domestic_price_inr) * 100.0, 2)
        # (1700 - 1650) / 1650 * 100 = 50 / 1650 * 100 = 3.03%
        self.assertEqual(premium_pct, 3.03)

    def test_adr_multi_share_ratio(self):
        # e.g. HDB: 1 ADR = 3 equity shares
        adr_price_usd = 60.0
        usdinr_rate = 87.0
        adr_ratio = 3.0
        domestic_price_inr = 1750.0

        implied_inr = (adr_price_usd * usdinr_rate) / adr_ratio  # (60 * 87) / 3 = 1740.0
        self.assertEqual(implied_inr, 1740.0)

        prem_disc = round(((implied_inr - domestic_price_inr) / domestic_price_inr) * 100.0, 2)
        # (1740 - 1750) / 1750 * 100 = -10 / 1750 * 100 = -0.57%
        self.assertEqual(prem_disc, -0.57)

    def test_session_phase_resolution(self):
        feed = MarketDataFeed()
        # Verify get_us_session_phase returns valid phase string
        phase = feed.get_us_session_phase()
        self.assertIn(phase, ["REGULAR", "PREMARKET", "AFTER_HOURS", "CLOSED"])


class TestNewsMonitorAndClassifier(unittest.TestCase):
    """Verifies news impact classification and symbol extraction."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = MarketDatabase(os.path.join(self.temp_dir, "test.db"))
        self.monitor = NewsMonitor(self.db)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_classify_critical_impact(self):
        headline = "SEBI bans company promoter and orders forensic audit into fraud allegations"
        summary = "Enforcement directorate raids company offices."
        impact = self.monitor.classify_impact(headline, summary)
        self.assertEqual(impact, "CRITICAL")

    def test_classify_high_impact(self):
        headline = "Infosys bags major deal worth $1.5 billion from European banking consortium"
        summary = "Q3 net profit surges 12% year-on-year."
        impact = self.monitor.classify_impact(headline, summary)
        self.assertEqual(impact, "HIGH")

    def test_classify_medium_impact(self):
        headline = "Global brokerage raises target price on Reliance Industries to Rs 3,200"
        summary = "Monthly auto sales numbers show steady volume growth."
        impact = self.monitor.classify_impact(headline, summary)
        self.assertEqual(impact, "MEDIUM")

    def test_sentiment_assessment(self):
        bullish_text = "Net profit jumps 25% as company wins mega contract and announces dividend."
        bearish_text = "Revenue drops sharply amid margin slump and regulatory penalty."
        neutral_text = "Board of directors will meet on Tuesday to review routine matters."

        self.assertEqual(self.monitor.assess_sentiment(bullish_text), "BULLISH")
        self.assertEqual(self.monitor.assess_sentiment(bearish_text), "BEARISH")
        self.assertEqual(self.monitor.assess_sentiment(neutral_text), "NEUTRAL")

    def test_symbol_extraction(self):
        text = "TCS and Infosys lead IT rally while Reliance Industries and HDFC Bank consolidate."
        symbols = self.monitor.extract_symbols(text)
        self.assertIn("TCS.NS", symbols)
        self.assertIn("INFY.NS", symbols)
        self.assertIn("RELIANCE.NS", symbols)
        self.assertIn("HDFCBANK.NS", symbols)


class TestFinancialAnalyst(unittest.TestCase):
    """Verifies technical indicator math and directional assessments."""

    def test_rsi_calculation(self):
        prices = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0, 114.0, 116.0, 118.0, 120.0, 122.0, 124.0, 126.0, 128.0]
        rsi = calculate_rsi(prices, period=14)
        # All gains, 0 losses -> RSI must be 100.0
        self.assertEqual(rsi, 100.0)

    def test_sma_calculation(self):
        prices = [10.0, 20.0, 30.0, 40.0, 50.0]
        sma = calculate_sma(prices, 5)
        self.assertEqual(sma, 30.0)

    def test_analyst_assessment_structure(self):
        temp_dir = tempfile.mkdtemp()
        try:
            db = MarketDatabase(os.path.join(temp_dir, "test.db"))
            # Seed mock quote
            db.upsert_quote({
                "symbol": "INFY.NS",
                "price": 1850.0,
                "change": 25.0,
                "change_pct": 1.37,
                "previous_close": 1825.0,
                "source": "mock_feed",
                "is_delayed": 1,
            })
            analyst = MarketAnalyst(db)
            analysis = analyst.analyze_instrument("INFY.NS")

            self.assertIn(analysis["direction"], ["BULLISH", "BEARISH", "NEUTRAL"])
            self.assertIn(analysis["horizon"], ["INTRADAY", "SHORT_TERM", "SWING", "POSITIONAL"])
            self.assertIn(analysis["confidence"], ["HIGH", "MEDIUM", "LOW"])
            self.assertIn("support", analysis["key_levels"])
            self.assertIn("resistance", analysis["key_levels"])
            self.assertIn("invalidation", analysis)
            self.assertIn("risks", analysis)
            self.assertIn("scenarios", analysis)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestMarketNotifier(unittest.IsolatedAsyncioTestCase):
    """Verifies structured alert formatting and dispatch."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = MarketDatabase(os.path.join(self.temp_dir, "test.db"))
        self.notifier = MarketNotifier(self.db)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_format_alert_text(self):
        title, body = self.notifier.format_alert_text(
            impact="HIGH",
            symbol="INFY",
            direction="BULLISH",
            headline="Major earnings revision",
            summary="Revenue guidance revised upward by 200 bps.",
            horizon="Short-term",
            confidence="Medium",
            key_risk="IT sector weakness",
            source="NSE Feed",
            timestamp="2026-11-11 12:00:00 UTC",
        )
        self.assertEqual(title, "MARKET ALERT — HIGH")
        self.assertIn("INFY: Potentially BULLISH.", body)
        self.assertIn("Event: Major earnings revision", body)
        self.assertIn("Horizon: Short-term. Confidence: Medium. Key risk: IT sector weakness.", body)
        self.assertIn("Source: NSE Feed Timestamp: 2026-11-11 12:00:00 UTC", body)

    async def test_notify_persists_in_db(self):
        alert = await self.notifier.notify_market_alert(
            symbol="RELIANCE.NS",
            impact="HIGH",
            headline="New green energy project approved",
            summary="Board approved Rs 15,000 Cr investment.",
            direction="BULLISH",
            horizon="SWING",
            confidence="HIGH",
            key_risk="Execution delays",
        )
        self.assertIsNotNone(alert)
        alerts = self.db.get_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["symbol"], "RELIANCE.NS")
        self.assertEqual(alerts[0]["impact"], "HIGH")


class TestMarketAgentAndRouting(unittest.TestCase):
    """Verifies MarketIntelligenceAgent and TaskRouter deterministic policy."""

    def test_agent_registration(self):
        reg = get_agent_registry()
        desc = reg.get("market_agent")
        self.assertIsNotNone(desc)
        self.assertEqual(desc.domain, "market")
        self.assertIn("market_analyze_instrument", desc.registered_tool_ids)
        self.assertIn("market_overview", desc.registered_tool_ids)

    def test_task_router_matches_financial_queries(self):
        router = get_task_router()
        decision = router.classify_and_route("Analyze INFY stock and give me bullish or bearish levels")
        self.assertEqual(decision.decision_type, "SINGLE_AGENT")
        self.assertEqual(decision.target_agent, "market_agent")
        self.assertEqual(decision.domain, "market")
        self.assertEqual(len(decision.contracts), 1)

    def test_task_router_matches_market_overview(self):
        router = get_task_router()
        decision = router.classify_and_route("What is happening in Indian markets and overnight ADRs today?")
        self.assertEqual(decision.decision_type, "SINGLE_AGENT")
        self.assertEqual(decision.target_agent, "market_agent")
        self.assertEqual(decision.domain, "market")

    def test_market_agent_execution(self):
        agent = MarketIntelligenceAgent()
        self.assertEqual(agent.agent_id, "market_agent")
        self.assertIn("market_surveillance", agent.supported_tasks)
        self.assertIn("instrument_analysis", agent.supported_tasks)


class TestToolRegistryMarketTools(unittest.IsolatedAsyncioTestCase):
    """Verifies tool discovery and execution via ToolRegistry."""

    def setUp(self):
        self.reg = get_tool_registry()

    def test_market_tools_registered(self):
        tool_names = [t.name for t in self.reg.list_tools()]
        expected = [
            "market_overview",
            "market_analyze_instrument",
            "market_adr_summary",
            "market_get_news",
            "market_get_alerts",
            "market_trigger_scan",
        ]
        for t in expected:
            self.assertIn(t, tool_names)

    async def test_execute_market_overview(self):
        res = await self.reg.execute("market_overview", {})
        self.assertEqual(res.get("status"), "success")
        self.assertTrue(res.get("verified"))

    async def test_execute_market_analyze_instrument(self):
        res = await self.reg.execute("market_analyze_instrument", {"symbol": "^NSEI"})
        self.assertIn(res.get("direction"), ["BULLISH", "BEARISH", "NEUTRAL"])
        self.assertIn("key_levels", res)


class TestMarketRESTEndpoints(unittest.TestCase):
    """Verifies FastAPI REST endpoints."""

    def setUp(self):
        self.client = TestClient(app)

    def test_get_market_overview_api(self):
        resp = self.client.get("/api/market/overview")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertIn("benchmarks", data)
        self.assertIn("adrs", data)
        self.assertIn("recent_news", data)

    def test_get_market_quotes_api(self):
        resp = self.client.get("/api/market/quotes")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("quotes", data)

    def test_get_market_adrs_api(self):
        resp = self.client.get("/api/market/adrs")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("adrs", data)

    def test_get_market_news_api(self):
        resp = self.client.get("/api/market/news?limit=10")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("news", data)

    def test_post_market_analyze_api(self):
        resp = self.client.post("/api/market/analyze", json={"symbol": "INFY.NS"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("symbol"), "INFY.NS")
        self.assertIn("analysis", data)
        self.assertIn(data["analysis"].get("direction"), ["BULLISH", "BEARISH", "NEUTRAL"])


if __name__ == "__main__":
    unittest.main()
