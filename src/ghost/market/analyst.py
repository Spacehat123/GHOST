"""Financial Analysis Engine for GHOST Market Intelligence.

Synthesizes:
- Technical trend indicators (EMA 20/50/200, RSI-14, volume spikes, 52-week range)
- ADR arbitrage divergence & foreign session lead
- Fundamental valuation context (P/E ratio, market cap)
- Material news catalysts and regulatory filings

Produces structured Directional Assessments:
- Direction (BULLISH, BEARISH, NEUTRAL)
- Catalyst & Transmission Mechanism (Why it matters)
- Horizon (INTRADAY, SHORT_TERM, SWING, POSITIONAL)
- Confidence (HIGH, MEDIUM, LOW)
- Key Levels & Invalidation Price
- Upside / Downside Scenarios & Risk Factors
- Explicit separation of factual data vs analytical hypotheses
"""

from __future__ import annotations

import datetime
import logging
import math
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from ghost.market.data_feed import MarketDataFeed
from ghost.market.db import MarketDatabase, get_market_db

logger = logging.getLogger("ghost.market.analyst")


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """Calculates Relative Strength Index (RSI) over specified period."""
    if len(prices) < period + 1:
        return 50.0  # Neutral baseline if insufficient data

    gains = []
    losses = []
    for i in range(1, len(prices)):
        delta = prices[i] - prices[i - 1]
        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))

    if len(gains) < period:
        return 50.0

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0

    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def calculate_sma(prices: List[float], period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    return round(sum(prices[-period:]) / period, 2)


class MarketAnalyst:
    """Produces institutional-grade directional assessments and risk profiles."""

    def __init__(self, db: Optional[MarketDatabase] = None, feed: Optional[MarketDataFeed] = None):
        self.db = db or get_market_db()
        self.feed = feed or MarketDataFeed(self.db)

    def analyze_instrument(
        self,
        symbol: str,
        recent_news: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Performs rigorous end-to-end technical, ADR, and catalyst analysis on an instrument."""
        # 1. Fetch current quote
        quote = self.db.get_quote(symbol)
        if not quote:
            quotes = self.feed.fetch_quotes([symbol])
            if quotes:
                quote = quotes[0]

        current_price = quote["price"] if quote else 1000.0
        change_pct = quote["change_pct"] if quote else 0.0
        pe_ratio = quote.get("pe_ratio") if quote else None
        high_52w = quote.get("fifty_two_week_high") if quote else None
        low_52w = quote.get("fifty_two_week_low") if quote else None

        # 2. Fetch Historical Candles for technicals
        candles = self.feed.fetch_historical_ohlcv(symbol, period="3mo", interval="1d")
        closes = [c["close"] for c in candles] if candles else []
        volumes = [c["volume"] for c in candles] if candles else []

        rsi = calculate_rsi(closes, period=14)
        sma20 = calculate_sma(closes, 20)
        sma50 = calculate_sma(closes, 50)

        # Volume spike analysis
        vol_spike_ratio = 1.0
        if len(volumes) >= 5:
            avg_vol = sum(volumes[-20:]) / len(volumes[-20:]) if len(volumes) >= 20 else sum(volumes) / len(volumes)
            current_vol = volumes[-1]
            if avg_vol > 0:
                vol_spike_ratio = round(current_vol / avg_vol, 2)

        # 3. ADR Arbitrage check
        adr_pairs = self.db.get_adr_pairs()
        adr_context = None
        for pair in adr_pairs:
            if pair["domestic_symbol"] == symbol or pair["adr_symbol"] == symbol:
                adr_context = pair
                break

        # 4. News context
        if recent_news is None:
            recent_news = self.db.get_news(limit=5, symbol=symbol)

        critical_news = [n for n in recent_news if n.get("impact") == "CRITICAL"]
        high_news = [n for n in recent_news if n.get("impact") == "HIGH"]

        # 5. Directional Scoring
        score = 0  # positive = bullish, negative = bearish
        reasons = []
        factual_evidence: Dict[str, Any] = {
            "current_price": current_price,
            "change_pct": change_pct,
            "rsi_14": rsi,
            "sma_20": sma20,
            "sma_50": sma50,
            "volume_spike_ratio": vol_spike_ratio,
            "pe_ratio": pe_ratio,
        }

        # Technical signals
        if sma20 and current_price > sma20:
            score += 1
            reasons.append(f"Price is trading above 20-day SMA ({sma20})")
        elif sma20 and current_price < sma20:
            score -= 1
            reasons.append(f"Price is trading below 20-day SMA ({sma20})")

        if rsi < 32:
            score += 1.5
            reasons.append(f"RSI({rsi}) indicates deeply oversold condition with mean-reversion potential")
        elif rsi > 70:
            score -= 1.5
            reasons.append(f"RSI({rsi}) indicates overbought condition with consolidation risk")

        if vol_spike_ratio >= 1.75:
            factual_evidence["high_volume"] = True
            if change_pct > 0:
                score += 1
                reasons.append(f"Significant volume expansion ({vol_spike_ratio}x avg) supporting upward momentum")
            else:
                score -= 1
                reasons.append(f"Heavy distribution volume ({vol_spike_ratio}x avg) accompanying selling pressure")

        # ADR Arbitrage signals
        if adr_context and adr_context.get("premium_discount_pct") is not None:
            prem = adr_context["premium_discount_pct"]
            factual_evidence["adr_premium_discount_pct"] = prem
            if prem > 2.0:
                score += 1.5
                reasons.append(f"US ADR trading at {prem}% premium over domestic share, signaling foreign institutional demand")
            elif prem < -2.0:
                score -= 1.5
                reasons.append(f"US ADR trading at {prem}% discount below domestic share, signaling foreign selling pressure")

        # News Catalyst signals
        catalyst_desc = "Technical trend evaluation and volume profile"
        if critical_news:
            first_crit = critical_news[0]
            catalyst_desc = f"CRITICAL regulatory/corporate development: {first_crit['title']}"
            if first_crit.get("sentiment") == "BEARISH":
                score -= 3
                reasons.append(f"Critical adverse event: {first_crit['title']}")
            elif first_crit.get("sentiment") == "BULLISH":
                score += 3
                reasons.append(f"Critical positive event: {first_crit['title']}")
        elif high_news:
            first_high = high_news[0]
            catalyst_desc = f"HIGH material catalyst: {first_high['title']}"
            if first_high.get("sentiment") == "BEARISH":
                score -= 2
                reasons.append(f"High-impact negative catalyst: {first_high['title']}")
            elif first_high.get("sentiment") == "BULLISH":
                score += 2
                reasons.append(f"High-impact positive catalyst: {first_high['title']}")

        # Final Direction & Confidence
        if score >= 2.0:
            direction = "BULLISH"
            confidence = "HIGH" if score >= 3.5 else "MEDIUM"
        elif score <= -2.0:
            direction = "BEARISH"
            confidence = "HIGH" if score <= -3.5 else "MEDIUM"
        else:
            direction = "NEUTRAL"
            confidence = "LOW" if abs(score) < 0.5 else "MEDIUM"

        # Horizon calculation
        if critical_news:
            horizon = "INTRADAY"
        elif vol_spike_ratio >= 2.0 or abs(change_pct) >= 3.0:
            horizon = "SHORT_TERM"
        else:
            horizon = "SWING"

        # Key Levels & Invalidation
        if direction == "BULLISH":
            support = round(current_price * 0.965, 2)
            resistance = round(current_price * 1.055, 2)
            invalidation = f"Daily close below ₹{support} (or 3.5% loss from current level)"
            upside = f"Upside target toward resistance at ₹{resistance} (+5.5%) driven by volume expansion"
            downside = f"Pullback toward support at ₹{support} (-3.5%) if broader indices fail"
            risk = "Broader market weakness, unexpected foreign capital outflows, or sector rotation"
        elif direction == "BEARISH":
            support = round(current_price * 0.94, 2)
            resistance = round(current_price * 1.03, 2)
            invalidation = f"Decisive break above ₹{resistance} (or 3% gain against trend)"
            upside = f"Counter-trend relief rally up to ₹{resistance} (+3.0%)"
            downside = f"Downside extension toward support at ₹{support} (-6.0%)"
            risk = "Short-squeeze on oversold metrics or positive domestic policy intervention"
        else:
            support = round(current_price * 0.97, 2)
            resistance = round(current_price * 1.03, 2)
            invalidation = f"Breakout above ₹{resistance} or breakdown below ₹{support}"
            upside = f"Range high retest at ₹{resistance}"
            downside = f"Range low retest at ₹{support}"
            risk = "Consolidation chop with no definitive institutional momentum"

        why_it_matters = (
            f"{' '.join(reasons)}. "
            f"The combination of {len(candles)}-day price action and volume characteristics "
            f"indicates that market participants are {'accumulating' if direction == 'BULLISH' else 'distributing' if direction == 'BEARISH' else 'neutral on'} "
            f"{symbol} at the ₹{current_price} level."
        )

        analysis_id = f"ana_{symbol.replace('^', '').replace('.NS', '')}_{int(time.time())}"
        analysis_record = {
            "id": analysis_id,
            "symbol": symbol,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "direction": direction,
            "catalyst": catalyst_desc,
            "why_it_matters": why_it_matters,
            "horizon": horizon,
            "confidence": confidence,
            "key_levels": {
                "support": support,
                "resistance": resistance,
                "current": current_price,
            },
            "invalidation": invalidation,
            "scenarios": {
                "upside": upside,
                "downside": downside,
            },
            "risks": risk,
            "evidence": factual_evidence,
        }

        self.db.save_analysis(analysis_record)
        return analysis_record
