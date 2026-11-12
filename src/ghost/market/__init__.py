"""Market Intelligence package for GHOST Universal Intelligence System.

Conforms to permanent Market Intelligence specification:
- Indian equities & indices (NSE, BSE, NIFTY 50, BANK NIFTY, SENSEX)
- US-listed Indian ADRs (INFY, HDB, IBN, WIT)
- Macro, commodities & FX (USD/INR, Brent crude, US 10Y, GIFT Nifty)
- Free & accessible feeds (yfinance, NSE/BSE corporate RSS)
- Persistent SQLite state, real-time news impact classification & desktop alerts
"""

from ghost.market.db import MarketDatabase, get_market_db

__all__ = ["MarketDatabase", "get_market_db"]
