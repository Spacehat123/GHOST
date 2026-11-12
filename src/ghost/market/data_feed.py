"""Market Data Ingestion Engine for GHOST Market Intelligence.

Integrates:
- Real-time/delayed quote fetching via yfinance
- OHLCV historical candle series
- ADR-to-Domestic arbitrage math and currency-adjusted conversion:
    Implied INR = (ADR_USD * USDINR) / ADR_Ratio
    Premium/Discount % = ((Implied_INR - Domestic_INR) / Domestic_INR) * 100
- Explicit provenance: data source, latency measurement, and delay flags
"""

from __future__ import annotations

import datetime
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import yfinance as yf

from ghost.market.db import MarketDatabase, get_market_db
from ghost.market.universe import load_universe_config

logger = logging.getLogger("ghost.market.data_feed")


class MarketDataFeed:
    """Manages ingestion of market quotes, historical series, and ADR calculations."""

    def __init__(self, db: Optional[MarketDatabase] = None):
        self.db = db or get_market_db()

    def get_us_session_phase(self, dt: Optional[datetime.datetime] = None) -> str:
        """Determines US market session phase (US Eastern Time)."""
        dt = dt or datetime.datetime.now(datetime.timezone.utc)
        # Convert to Eastern Time (UTC-5 or UTC-4 depending on DST)
        # Approximate ET as UTC-4 (EDT) or UTC-5 (EST)
        # Mon-Fri:
        if dt.weekday() >= 5:
            return "CLOSED"
        # UTC hour for ET:
        # Pre-market: 04:00 - 09:30 ET -> ~08:00 - 13:30 UTC
        # Regular: 09:30 - 16:00 ET -> ~13:30 - 20:00 UTC
        # After-hours: 16:00 - 20:00 ET -> ~20:00 - 00:00 UTC
        hour_frac = dt.hour + (dt.minute / 60.0)
        if 8.0 <= hour_frac < 13.5:
            return "PREMARKET"
        elif 13.5 <= hour_frac < 20.0:
            return "REGULAR"
        elif 20.0 <= hour_frac < 24.0:
            return "AFTER_HOURS"
        else:
            return "CLOSED"

    def fetch_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """Fetches quote snapshots with latency and source provenance."""
        if not symbols:
            return []

        results: List[Dict[str, Any]] = []
        t0 = time.time()

        try:
            # Batch fetch via yfinance Tickers
            sym_str = " ".join(symbols)
            tickers = yf.Tickers(sym_str)
            latency_ms = (time.time() - t0) * 1000.0 / max(len(symbols), 1)

            for sym in symbols:
                try:
                    t = tickers.tickers.get(sym)
                    if not t:
                        continue
                    info = {}
                    try:
                        info = t.info or {}
                    except Exception:
                        pass

                    # Attempt fast info or regularMarketPrice
                    price = info.get("regularMarketPrice") or info.get("currentPrice")
                    prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")

                    # If info is empty, try fast_info
                    if price is None and hasattr(t, "fast_info"):
                        try:
                            price = getattr(t.fast_info, "last_price", None)
                            prev_close = getattr(t.fast_info, "previous_close", None)
                        except Exception:
                            pass

                    # If still None, try history(period='2d')
                    if price is None:
                        try:
                            df = t.history(period="2d")
                            if not df.empty:
                                price = float(df["Close"].iloc[-1])
                                if len(df) > 1:
                                    prev_close = float(df["Close"].iloc[-2])
                                else:
                                    prev_close = float(df["Open"].iloc[0])
                        except Exception:
                            pass

                    if price is None:
                        continue

                    price = float(price)
                    prev_close = float(prev_close) if prev_close else price
                    change = round(price - prev_close, 2)
                    change_pct = round((change / prev_close) * 100.0, 2) if prev_close else 0.0

                    quote_data = {
                        "symbol": sym,
                        "price": price,
                        "change": change,
                        "change_pct": change_pct,
                        "open": float(info.get("regularMarketOpen") or info.get("open") or price),
                        "high": float(info.get("regularMarketDayHigh") or info.get("dayHigh") or price),
                        "low": float(info.get("regularMarketDayLow") or info.get("dayLow") or price),
                        "previous_close": prev_close,
                        "volume": int(info.get("regularMarketVolume") or info.get("volume") or 0),
                        "pe_ratio": float(info.get("trailingPE") or 0.0) if info.get("trailingPE") else None,
                        "market_cap": float(info.get("marketCap") or 0.0) if info.get("marketCap") else None,
                        "fifty_two_week_high": float(info.get("fiftyTwoWeekHigh") or 0.0) if info.get("fiftyTwoWeekHigh") else None,
                        "fifty_two_week_low": float(info.get("fiftyTwoWeekLow") or 0.0) if info.get("fiftyTwoWeekLow") else None,
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "source": "yfinance_market_feed",
                        "latency_ms": round(latency_ms, 2),
                        "is_delayed": 1,
                    }

                    self.db.upsert_quote(quote_data)
                    results.append(quote_data)

                except Exception as ex:
                    logger.debug(f"Failed parsing quote for {sym}: {ex}")

        except Exception as e:
            logger.error(f"Batch quote fetch error: {e}")

        return results

    def fetch_historical_ohlcv(
        self,
        symbol: str,
        period: str = "1mo",
        interval: str = "1d",
    ) -> List[Dict[str, Any]]:
        """Retrieves historical OHLCV candles for technical indicator calculation."""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)
            if df.empty:
                return []
            candles = []
            for dt, row in df.iterrows():
                candles.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                })
            return candles
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol}: {e}")
            return []

    def calculate_adr_metrics(self, usdinr_rate: Optional[float] = None) -> List[Dict[str, Any]]:
        """Calculates ADR implied price and premium/discount % for all configured ADR pairs."""
        conf = load_universe_config()
        adr_list = conf.get("adrs", [])
        if not adr_list:
            return []

        # 1. Resolve USD/INR rate
        if usdinr_rate is None or usdinr_rate <= 0:
            usdinr_quote = self.db.get_quote("USDINR=X")
            if not usdinr_quote:
                # Fetch fresh USDINR quote
                fresh = self.fetch_quotes(["USDINR=X"])
                if fresh:
                    usdinr_rate = fresh[0]["price"]
            else:
                usdinr_rate = usdinr_quote["price"]

        if not usdinr_rate:
            # Fallback default reasonable rate if market is closed/unreachable
            usdinr_rate = 87.50

        # Collect symbols to ensure fresh quotes
        needed_syms = ["USDINR=X"]
        for item in adr_list:
            needed_syms.append(item["adr_symbol"])
            needed_syms.append(item["domestic_symbol"])

        quotes_map = {q["symbol"]: q for q in self.db.get_quotes(needed_syms)}

        missing = [s for s in needed_syms if s not in quotes_map]
        if missing:
            fresh_quotes = self.fetch_quotes(missing)
            for fq in fresh_quotes:
                quotes_map[fq["symbol"]] = fq

        session_phase = self.get_us_session_phase()
        updated_pairs: List[Dict[str, Any]] = []

        for item in adr_list:
            adr_sym = item["adr_symbol"]
            dom_sym = item["domestic_symbol"]
            ratio = float(item.get("adr_ratio", 1.0))

            adr_q = quotes_map.get(adr_sym)
            dom_q = quotes_map.get(dom_sym)

            adr_price_usd = adr_q["price"] if adr_q else None
            dom_price_inr = dom_q["price"] if dom_q else None

            implied_inr = None
            prem_disc_pct = None

            if adr_price_usd and ratio > 0 and usdinr_rate:
                # Implied INR = (ADR_USD * USDINR) / ratio
                implied_inr = round((adr_price_usd * usdinr_rate) / ratio, 2)

                if dom_price_inr and dom_price_inr > 0:
                    # Premium / Discount % = ((Implied_INR - Domestic_INR) / Domestic_INR) * 100
                    prem_disc_pct = round(((implied_inr - dom_price_inr) / dom_price_inr) * 100.0, 2)

            data = {
                "adr_symbol": adr_sym,
                "domestic_symbol": dom_sym,
                "adr_ratio": ratio,
                "adr_price_usd": adr_price_usd,
                "domestic_price_inr": dom_price_inr,
                "usdinr_rate": usdinr_rate,
                "implied_inr": implied_inr,
                "premium_discount_pct": prem_disc_pct,
                "session_phase": session_phase,
            }
            self.db.upsert_adr(data)
            updated_pairs.append(data)

        return updated_pairs
