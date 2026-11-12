"""Major News Monitoring & Impact Classification Engine for GHOST Market Intelligence.

Features:
- RSS & Financial feed ingestion (Economic Times, Livemint, Moneycontrol, RBI, SEBI)
- SHA-256 deduplication and clustering
- Deterministic Impact Classification:
    CRITICAL: Major regulatory bans, rate surprises, debt default, fraud, emergency events
    HIGH: Quarterly earnings, major contract wins, M&A, rating changes, sector policy
    MEDIUM: Brokerage target revisions, monthly sales, routine board outcomes
    LOW: General market chatter, listicles
- Automated symbol extraction and sentiment scoring
- Publishes events to EventBus for real-time alerting
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import feedparser

from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.market.db import MarketDatabase, get_market_db
from ghost.market.universe import load_universe_config

logger = logging.getLogger("ghost.market.news_monitor")

# Regex rules for Impact Classification
CRITICAL_PATTERNS = [
    r"\b(sebi\s+(?:bans|bars|freezes|raids|penalises|penalizes|investigates))\b",
    r"\b(rbi\s+(?:curbs|imposes\s+restrictions|cancels\s+licence|rate\s+cut|rate\s+hike|emergency))\b",
    r"\b(fraud|forensic\s+audit|scam|arrested|cbi|ed\s+raids|enforcement\s+directorate)\b",
    r"\b(defaults\s+on\s+debt|bankruptcy|insolvency|nclt\s+admits|liquidation)\b",
    r"\b(ceo\s+(?:resigns\s+abruptly|quits|ousted|stepping\s+down\s+immediately))\b",
    r"\b(hostile\s+takeover|trading\s+halted|circuit\s+breaker)\b",
]

HIGH_PATTERNS = [
    r"\b(q[1-4]\s+(?:profit|net\s+profit|pat|revenue|results)\s+(?:jumps|surges|soars|drops|plunges|slumps))\b",
    r"\b(bags\s+order|wins\s+deal|order\s+win|contract\s+worth|worth\s+rs\s+\d+)\b",
    r"\b(merger|acquisition|demerger|amalgamation|stake\s+sale|blocks\s+deal)\b",
    r"\b(upgraded|downgraded|rating\s+revision|credit\s+watch)\b",
    r"\b(dividend\s+(?:of\s+rs|bonus\s+issue|stock\s+split|buyback))\b",
    r"\b(patent\s+approval|fda\s+(?:approval|warning\s+letter|form\s+483))\b",
    r"\b(tariff\s+hike|windfall\s+tax|customs\s+duty|pli\s+scheme)\b",
]

MEDIUM_PATTERNS = [
    r"\b(brokerage|target\s+price|initiates\s+coverage|outperform|buy\s+call|sell\s+call)\b",
    r"\b(monthly\s+sales|auto\s+sales|dispatch\s+numbers|freight\s+volume)\b",
    r"\b(board\s+meeting|to\s+consider\s+dividend|agm|fundraising\s+plans)\b",
]

BULLISH_KEYWORDS = [
    "jump", "surge", "soar", "rally", "gain", "profit", "win", "bags", "deal", "upgrade",
    "beat", "record high", "dividend", "buyback", "growth", "bonus", "expanded", "outperform"
]

BEARISH_KEYWORDS = [
    "drop", "plunge", "slump", "fall", "loss", "penalty", "ban", "curb", "probe", "downgrade",
    "miss", "record low", "fraud", "warning", "default", "contraction", "underperform", "cut"
]

# Mapping company names / keywords to ticker symbols
COMPANY_SYMBOL_MAP = {
    "reliance": "RELIANCE.NS",
    "ril": "RELIANCE.NS",
    "tcs": "TCS.NS",
    "tata consultancy": "TCS.NS",
    "hdfc bank": "HDFCBANK.NS",
    "hdfc": "HDFCBANK.NS",
    "icici bank": "ICICIBANK.NS",
    "icici": "ICICIBANK.NS",
    "infosys": "INFY.NS",
    "infy": "INFY.NS",
    "bharti airtel": "BHARTIARTL.NS",
    "airtel": "BHARTIARTL.NS",
    "state bank of india": "SBIN.NS",
    "sbi": "SBIN.NS",
    "larsen & toubro": "LT.NS",
    "l&t": "LT.NS",
    "tata motors": "TATAMOTORS.NS",
    "itc": "ITC.NS",
    "wipro": "WIPRO.NS",
    "maruti suzuki": "MARUTI.NS",
    "maruti": "MARUTI.NS",
    "bajaj finance": "BAJFINANCE.NS",
    "sun pharma": "SUNPHARMA.NS",
    "kotak bank": "KOTAKBANK.NS",
    "kotak mahindra": "KOTAKBANK.NS",
    "axis bank": "AXISBANK.NS",
    "titan": "TITAN.NS",
    "hindustan unilever": "HINDUNILVR.NS",
    "hul": "HINDUNILVR.NS",
    "nifty": "^NSEI",
    "sensex": "^BSESN",
    "bank nifty": "^NSEBANK",
}


class NewsMonitor:
    """Monitors RSS feeds and financial announcements, extracts symbols, and assesses market impact."""

    def __init__(self, db: Optional[MarketDatabase] = None, event_bus: Optional[EventBus] = None):
        self.db = db or get_market_db()
        self.event_bus = event_bus or get_event_bus()

    def _compute_hash(self, title: str, summary: str) -> str:
        clean = (title + " " + summary).strip().lower()
        clean = re.sub(r"\s+", " ", clean)
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()

    def classify_impact(self, title: str, summary: str) -> str:
        full_text = f"{title} {summary}".lower()

        for pattern in CRITICAL_PATTERNS:
            if re.search(pattern, full_text, re.IGNORECASE):
                return "CRITICAL"

        for pattern in HIGH_PATTERNS:
            if re.search(pattern, full_text, re.IGNORECASE):
                return "HIGH"

        for pattern in MEDIUM_PATTERNS:
            if re.search(pattern, full_text, re.IGNORECASE):
                return "MEDIUM"

        return "LOW"

    def assess_sentiment(self, text: str) -> str:
        low = text.lower()
        b_count = sum(1 for kw in BULLISH_KEYWORDS if kw in low)
        bear_count = sum(1 for kw in BEARISH_KEYWORDS if kw in low)

        if b_count > bear_count and b_count >= 1:
            return "BULLISH"
        elif bear_count > b_count and bear_count >= 1:
            return "BEARISH"
        return "NEUTRAL"

    def extract_symbols(self, text: str) -> List[str]:
        low = text.lower()
        matched: Set[str] = set()

        for term, sym in COMPANY_SYMBOL_MAP.items():
            if re.search(r"\b" + re.escape(term) + r"\b", low):
                matched.add(sym)

        # Also check for explicit ticker symbols like INFY, TCS, RELIANCE
        for token in re.findall(r"\b[A-Z]{3,12}\b", text):
            token_ns = f"{token}.NS"
            for term, sym in COMPANY_SYMBOL_MAP.items():
                if sym == token_ns or sym == token:
                    matched.add(sym)

        return sorted(list(matched))

    def ingest_feed(self, feed_name: str, feed_url: str) -> List[Dict[str, Any]]:
        """Parses a single RSS feed, detects new items, and stores in SQLite."""
        new_items: List[Dict[str, Any]] = []

        try:
            d = feedparser.parse(feed_url)
            for entry in d.entries[:30]:
                title = getattr(entry, "title", "").strip()
                if not title:
                    continue

                summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
                # Strip HTML tags
                summary = re.sub(r"<[^>]+>", "", summary).strip()
                link = getattr(entry, "link", "")
                published = getattr(entry, "published", "") or getattr(entry, "updated", "")
                if not published:
                    published = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

                raw_hash = self._compute_hash(title, summary)
                impact = self.classify_impact(title, summary)
                sentiment = self.assess_sentiment(f"{title} {summary}")
                symbols = self.extract_symbols(f"{title} {summary}")

                item_id = f"news_{raw_hash[:12]}"
                item = {
                    "id": item_id,
                    "raw_hash": raw_hash,
                    "source": feed_name,
                    "title": title,
                    "summary": summary,
                    "url": link,
                    "published_at": published,
                    "impact": impact,
                    "sentiment": sentiment,
                    "symbols": symbols,
                }

                inserted = self.db.insert_news(item)
                if inserted:
                    new_items.append(item)

        except Exception as e:
            logger.error(f"Error ingesting feed {feed_name} ({feed_url}): {e}")

        return new_items

    async def run_sweep(self) -> List[Dict[str, Any]]:
        """Runs a complete sweep of all configured feeds, saving new items and emitting events."""
        conf = load_universe_config()
        feeds = conf.get("rss_feeds", [])
        all_new: List[Dict[str, Any]] = []

        for f in feeds:
            items = self.ingest_feed(f["name"], f["url"])
            all_new.extend(items)

        # Emit events for new items
        for item in all_new:
            try:
                sev = EventSeverity.INFO
                if item["impact"] == "CRITICAL":
                    sev = EventSeverity.CRITICAL
                elif item["impact"] == "HIGH":
                    sev = EventSeverity.WARNING

                await self.event_bus.publish(Event(
                    event_type=f"market.news.{item['impact'].lower()}",
                    source="ghost.market.news_monitor",
                    severity=sev,
                    payload=item,
                ))
            except Exception as e:
                logger.debug(f"Failed emitting news event: {e}")

        logger.info(f"News sweep complete. Ingested {len(all_new)} new items.")
        return all_new
