"""SQLite Persistence Engine for GHOST Market Intelligence.

Stores:
- Configurable instruments and watchlists
- Live and historical quote snapshots with explicit source & latency metadata
- ADR conversion pairs, implied prices, and premium/discount metrics
- Deduplicated news items with impact ratings (CRITICAL, HIGH, MEDIUM, LOW)
- Directional market analyses with horizons, catalysts, levels, and risks
- High/Critical desktop alerts history
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from ghost.paths import data_dir

logger = logging.getLogger("ghost.market.db")


class MarketDatabase:
    """Thread-safe SQLite manager for Market Intelligence state."""

    _instance: Optional["MarketDatabase"] = None

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.path.join(data_dir(), "market.db")
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_schema()

    @classmethod
    def get_instance(cls, db_path: Optional[str] = None) -> "MarketDatabase":
        if cls._instance is None or (db_path and cls._instance.db_path != db_path):
            cls._instance = MarketDatabase(db_path)
        return cls._instance

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _init_schema(self) -> None:
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS instruments (
                    symbol TEXT PRIMARY KEY,
                    exchange TEXT NOT NULL,
                    name TEXT NOT NULL,
                    sector TEXT,
                    adr_symbol TEXT,
                    adr_ratio REAL DEFAULT 1.0,
                    is_active INTEGER DEFAULT 1,
                    metadata_json TEXT DEFAULT '{}',
                    updated_at REAL
                );

                CREATE TABLE IF NOT EXISTS quotes (
                    symbol TEXT PRIMARY KEY,
                    price REAL,
                    change REAL,
                    change_pct REAL,
                    open REAL,
                    high REAL,
                    low REAL,
                    previous_close REAL,
                    volume INTEGER,
                    pe_ratio REAL,
                    market_cap REAL,
                    fifty_two_week_high REAL,
                    fifty_two_week_low REAL,
                    timestamp TEXT,
                    source TEXT,
                    latency_ms REAL,
                    is_delayed INTEGER DEFAULT 1,
                    updated_at REAL
                );

                CREATE TABLE IF NOT EXISTS adr_tracking (
                    adr_symbol TEXT PRIMARY KEY,
                    domestic_symbol TEXT NOT NULL,
                    adr_ratio REAL NOT NULL,
                    adr_price_usd REAL,
                    domestic_price_inr REAL,
                    usdinr_rate REAL,
                    implied_inr REAL,
                    premium_discount_pct REAL,
                    session_phase TEXT DEFAULT 'REGULAR',
                    updated_at REAL
                );

                CREATE TABLE IF NOT EXISTS news_items (
                    id TEXT PRIMARY KEY,
                    raw_hash TEXT UNIQUE NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT,
                    url TEXT,
                    published_at TEXT,
                    ingested_at REAL,
                    impact TEXT NOT NULL,
                    sentiment TEXT DEFAULT 'NEUTRAL',
                    symbols_json TEXT DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS market_analyses (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    catalyst TEXT,
                    why_it_matters TEXT,
                    horizon TEXT,
                    confidence TEXT,
                    key_levels_json TEXT DEFAULT '{}',
                    invalidation TEXT,
                    scenarios_json TEXT DEFAULT '{}',
                    risks TEXT,
                    evidence_json TEXT DEFAULT '{}',
                    created_at REAL
                );

                CREATE TABLE IF NOT EXISTS market_alerts (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    impact TEXT NOT NULL,
                    headline TEXT NOT NULL,
                    summary TEXT,
                    direction TEXT,
                    horizon TEXT,
                    confidence TEXT,
                    key_risk TEXT,
                    analysis_id TEXT,
                    notified_at REAL,
                    source_url TEXT
                );

                CREATE TABLE IF NOT EXISTS watchlists (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    symbols_json TEXT DEFAULT '[]',
                    updated_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at DESC);
                CREATE INDEX IF NOT EXISTS idx_news_impact ON news_items(impact);
                CREATE INDEX IF NOT EXISTS idx_analyses_symbol ON market_analyses(symbol, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_alerts_time ON market_alerts(notified_at DESC);
            """)

    # ------------------------------------------------------------------ Instruments
    def upsert_instrument(
        self,
        symbol: str,
        exchange: str,
        name: str,
        sector: str = "",
        adr_symbol: Optional[str] = None,
        adr_ratio: float = 1.0,
        is_active: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        meta_str = json.dumps(metadata or {})
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO instruments (symbol, exchange, name, sector, adr_symbol, adr_ratio, is_active, metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    exchange=excluded.exchange,
                    name=excluded.name,
                    sector=excluded.sector,
                    adr_symbol=excluded.adr_symbol,
                    adr_ratio=excluded.adr_ratio,
                    is_active=excluded.is_active,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (symbol, exchange, name, sector, adr_symbol, adr_ratio, int(is_active), meta_str, now),
            )

    def get_instruments(self, active_only: bool = True) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            if active_only:
                rows = conn.execute("SELECT * FROM instruments WHERE is_active = 1 ORDER BY symbol ASC").fetchall()
            else:
                rows = conn.execute("SELECT * FROM instruments ORDER BY symbol ASC").fetchall()
            return [dict(r) for r in rows]

    def get_instrument(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM instruments WHERE symbol = ?", (symbol,)).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------ Quotes
    def upsert_quote(self, quote: Dict[str, Any]) -> None:
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO quotes (
                    symbol, price, change, change_pct, open, high, low, previous_close,
                    volume, pe_ratio, market_cap, fifty_two_week_high, fifty_two_week_low,
                    timestamp, source, latency_ms, is_delayed, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    price=excluded.price,
                    change=excluded.change,
                    change_pct=excluded.change_pct,
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    previous_close=excluded.previous_close,
                    volume=excluded.volume,
                    pe_ratio=excluded.pe_ratio,
                    market_cap=excluded.market_cap,
                    fifty_two_week_high=excluded.fifty_two_week_high,
                    fifty_two_week_low=excluded.fifty_two_week_low,
                    timestamp=excluded.timestamp,
                    source=excluded.source,
                    latency_ms=excluded.latency_ms,
                    is_delayed=excluded.is_delayed,
                    updated_at=excluded.updated_at
                """,
                (
                    quote["symbol"],
                    quote.get("price"),
                    quote.get("change"),
                    quote.get("change_pct"),
                    quote.get("open"),
                    quote.get("high"),
                    quote.get("low"),
                    quote.get("previous_close"),
                    quote.get("volume"),
                    quote.get("pe_ratio"),
                    quote.get("market_cap"),
                    quote.get("fifty_two_week_high"),
                    quote.get("fifty_two_week_low"),
                    quote.get("timestamp"),
                    quote.get("source", "unknown"),
                    quote.get("latency_ms", 0.0),
                    int(quote.get("is_delayed", 1)),
                    now,
                ),
            )

    def get_quotes(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            if symbols:
                placeholders = ",".join(["?"] * len(symbols))
                rows = conn.execute(f"SELECT * FROM quotes WHERE symbol IN ({placeholders})", symbols).fetchall()
            else:
                rows = conn.execute("SELECT * FROM quotes ORDER BY updated_at DESC").fetchall()
            return [dict(r) for r in rows]

    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM quotes WHERE symbol = ?", (symbol,)).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------ ADR Tracking
    def upsert_adr(self, adr_data: Dict[str, Any]) -> None:
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO adr_tracking (
                    adr_symbol, domestic_symbol, adr_ratio, adr_price_usd,
                    domestic_price_inr, usdinr_rate, implied_inr, premium_discount_pct,
                    session_phase, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(adr_symbol) DO UPDATE SET
                    domestic_symbol=excluded.domestic_symbol,
                    adr_ratio=excluded.adr_ratio,
                    adr_price_usd=excluded.adr_price_usd,
                    domestic_price_inr=excluded.domestic_price_inr,
                    usdinr_rate=excluded.usdinr_rate,
                    implied_inr=excluded.implied_inr,
                    premium_discount_pct=excluded.premium_discount_pct,
                    session_phase=excluded.session_phase,
                    updated_at=excluded.updated_at
                """,
                (
                    adr_data["adr_symbol"],
                    adr_data["domestic_symbol"],
                    adr_data["adr_ratio"],
                    adr_data.get("adr_price_usd"),
                    adr_data.get("domestic_price_inr"),
                    adr_data.get("usdinr_rate"),
                    adr_data.get("implied_inr"),
                    adr_data.get("premium_discount_pct"),
                    adr_data.get("session_phase", "REGULAR"),
                    now,
                ),
            )

    def get_adr_pairs(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM adr_tracking ORDER BY adr_symbol ASC").fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------ News
    def insert_news(self, news_item: Dict[str, Any]) -> bool:
        """Returns True if news item was newly inserted, False if duplicate."""
        now = time.time()
        syms = json.dumps(news_item.get("symbols", []))
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO news_items (
                        id, raw_hash, source, title, summary, url, published_at,
                        ingested_at, impact, sentiment, symbols_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        news_item["id"],
                        news_item["raw_hash"],
                        news_item["source"],
                        news_item["title"],
                        news_item.get("summary", ""),
                        news_item.get("url", ""),
                        news_item.get("published_at", ""),
                        now,
                        news_item.get("impact", "LOW"),
                        news_item.get("sentiment", "NEUTRAL"),
                        syms,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_news(
        self,
        limit: int = 50,
        impact: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            query = "SELECT * FROM news_items"
            params: List[Any] = []
            clauses = []
            if impact:
                clauses.append("impact = ?")
                params.append(impact.upper())
            if symbol:
                clauses.append("symbols_json LIKE ?")
                params.append(f'%"{symbol}"%')
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY published_at DESC, ingested_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            res = []
            for r in rows:
                d = dict(r)
                d["symbols"] = json.loads(d.get("symbols_json") or "[]")
                res.append(d)
            return res

    # ------------------------------------------------------------------ Analyses
    def save_analysis(self, analysis: Dict[str, Any]) -> None:
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO market_analyses (
                    id, symbol, timestamp, direction, catalyst, why_it_matters,
                    horizon, confidence, key_levels_json, invalidation, scenarios_json,
                    risks, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    symbol=excluded.symbol,
                    timestamp=excluded.timestamp,
                    direction=excluded.direction,
                    catalyst=excluded.catalyst,
                    why_it_matters=excluded.why_it_matters,
                    horizon=excluded.horizon,
                    confidence=excluded.confidence,
                    key_levels_json=excluded.key_levels_json,
                    invalidation=excluded.invalidation,
                    scenarios_json=excluded.scenarios_json,
                    risks=excluded.risks,
                    evidence_json=excluded.evidence_json,
                    created_at=excluded.created_at
                """,
                (
                    analysis["id"],
                    analysis["symbol"],
                    analysis["timestamp"],
                    analysis["direction"],
                    analysis.get("catalyst", ""),
                    analysis.get("why_it_matters", ""),
                    analysis.get("horizon", "SHORT_TERM"),
                    analysis.get("confidence", "MEDIUM"),
                    json.dumps(analysis.get("key_levels", {})),
                    analysis.get("invalidation", ""),
                    json.dumps(analysis.get("scenarios", {})),
                    analysis.get("risks", ""),
                    json.dumps(analysis.get("evidence", {})),
                    now,
                ),
            )

    def get_analyses(
        self,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            query = "SELECT * FROM market_analyses"
            clauses = []
            params: List[Any] = []
            if symbol:
                clauses.append("symbol = ?")
                params.append(symbol)
            if direction:
                clauses.append("direction = ?")
                params.append(direction.upper())
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["key_levels"] = json.loads(d.get("key_levels_json") or "{}")
                d["scenarios"] = json.loads(d.get("scenarios_json") or "{}")
                d["evidence"] = json.loads(d.get("evidence_json") or "{}")
                out.append(d)
            return out

    # ------------------------------------------------------------------ Alerts
    def save_alert(self, alert: Dict[str, Any]) -> None:
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO market_alerts (
                    id, symbol, impact, headline, summary, direction,
                    horizon, confidence, key_risk, analysis_id, notified_at, source_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert["id"],
                    alert["symbol"],
                    alert["impact"],
                    alert["headline"],
                    alert.get("summary", ""),
                    alert.get("direction", "NEUTRAL"),
                    alert.get("horizon", "SHORT_TERM"),
                    alert.get("confidence", "MEDIUM"),
                    alert.get("key_risk", ""),
                    alert.get("analysis_id"),
                    now,
                    alert.get("source_url", ""),
                ),
            )

    def get_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM market_alerts ORDER BY notified_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------ Watchlists
    def upsert_watchlist(self, wid: str, name: str, description: str, symbols: List[str]) -> None:
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO watchlists (id, name, description, symbols_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    symbols_json=excluded.symbols_json,
                    updated_at=excluded.updated_at
                """,
                (wid, name, description, json.dumps(symbols), now),
            )

    def get_watchlists(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM watchlists ORDER BY name ASC").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["symbols"] = json.loads(d.get("symbols_json") or "[]")
                out.append(d)
            return out


def get_market_db(db_path: Optional[str] = None) -> MarketDatabase:
    return MarketDatabase.get_instance(db_path)
