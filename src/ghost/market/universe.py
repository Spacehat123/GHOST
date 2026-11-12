"""Instrument Universe Manager for GHOST Market Intelligence.

Loads configurable instruments, ADR pairs, macro factors, and default watchlists
from configs/market_universe.json and seeds SQLite persistence.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from ghost.market.db import MarketDatabase, get_market_db
from ghost.paths import repo_root

logger = logging.getLogger("ghost.market.universe")


def get_universe_config_path() -> str:
    return os.path.join(repo_root(), "configs", "market_universe.json")


def load_universe_config() -> Dict[str, Any]:
    path = get_universe_config_path()
    if not os.path.exists(path):
        logger.warning(f"Market universe config not found at {path}, returning empty defaults")
        return {"indices": [], "adrs": [], "equities": [], "macro": [], "watchlists": [], "rss_feeds": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to parse {path}: {e}")
        return {"indices": [], "adrs": [], "equities": [], "macro": [], "watchlists": [], "rss_feeds": []}


def seed_universe_to_db(db: Optional[MarketDatabase] = None) -> int:
    """Synchronizes market_universe.json into SQLite instruments and watchlists tables."""
    db = db or get_market_db()
    conf = load_universe_config()
    count = 0

    # 1. Indices
    for idx in conf.get("indices", []):
        db.upsert_instrument(
            symbol=idx["symbol"],
            exchange=idx.get("exchange", "INDEX"),
            name=idx.get("name", idx["symbol"]),
            sector=idx.get("sector", ""),
            is_active=idx.get("is_active", True),
        )
        count += 1

    # 2. Equities
    for eq in conf.get("equities", []):
        db.upsert_instrument(
            symbol=eq["symbol"],
            exchange=eq.get("exchange", "NSE"),
            name=eq.get("name", eq["symbol"]),
            sector=eq.get("sector", ""),
            adr_symbol=eq.get("adr_symbol"),
            adr_ratio=eq.get("adr_ratio", 1.0),
            is_active=eq.get("is_active", True),
        )
        count += 1

    # 3. ADRs
    for adr in conf.get("adrs", []):
        db.upsert_instrument(
            symbol=adr["adr_symbol"],
            exchange=adr.get("exchange", "NYSE"),
            name=adr.get("name", adr["adr_symbol"]),
            sector=adr.get("sector", ""),
            adr_symbol=adr["adr_symbol"],
            adr_ratio=adr.get("adr_ratio", 1.0),
            is_active=adr.get("is_active", True),
            metadata={"domestic_symbol": adr["domestic_symbol"]},
        )
        # Also seed initial adr_tracking entry
        db.upsert_adr({
            "adr_symbol": adr["adr_symbol"],
            "domestic_symbol": adr["domestic_symbol"],
            "adr_ratio": adr.get("adr_ratio", 1.0),
        })
        count += 1

    # 4. Macro & FX
    for m in conf.get("macro", []):
        db.upsert_instrument(
            symbol=m["symbol"],
            exchange=m.get("exchange", "MACRO"),
            name=m.get("name", m["symbol"]),
            sector=m.get("sector", ""),
            is_active=m.get("is_active", True),
        )
        count += 1

    # 5. Watchlists
    for wl in conf.get("watchlists", []):
        db.upsert_watchlist(
            wid=wl["id"],
            name=wl["name"],
            description=wl.get("description", ""),
            symbols=wl.get("symbols", []),
        )

    logger.info(f"Seeded {count} instruments and watchlists into MarketDatabase.")
    return count
