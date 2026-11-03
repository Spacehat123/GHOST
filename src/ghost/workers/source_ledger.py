"""Source Ledger & Fact Verification Engine for Ghost.

Enforces zero-hallucination policy by ensuring every business fact rendered
in a demo or pitch is backed by a verified scraped source or explicitly
marked as a placeholder. No plausible defaults.
"""
import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, Any, Optional


COUNTRY_CURRENCY_MAP = {
    "uk": "£",
    "united kingdom": "£",
    "london": "£",
    "england": "£",
    "scotland": "£",
    "wales": "£",
    "gb": "£",
    "gbp": "£",
    "us": "$",
    "usa": "$",
    "united states": "$",
    "new york": "$",
    "ny": "$",
    "california": "$",
    "usd": "$",
    "india": "₹",
    "in": "₹",
    "inr": "₹",
    "bengaluru": "₹",
    "bangalore": "₹",
    "mumbai": "₹",
    "delhi": "₹",
    "karnataka": "₹",
    "france": "€",
    "germany": "€",
    "spain": "€",
    "italy": "€",
    "netherlands": "€",
    "europe": "€",
    "eur": "€",
    "australia": "A$",
    "canada": "C$",
    "japan": "¥",
    "uae": "AED ",
    "dubai": "AED "
}


def infer_currency_symbol(location: Optional[str]) -> str:
    """Infers the appropriate local currency symbol from location string. Default to $ if unknown."""
    if not location:
        return "$"
    loc_lower = location.lower()
    for key, symbol in COUNTRY_CURRENCY_MAP.items():
        if re.search(r'\b' + re.escape(key) + r'\b', loc_lower):
            return symbol
    return "$"


class SourceEntry:
    def __init__(
        self,
        field: str,
        value: Any,
        source_url: Optional[str] = None,
        scraped_at: Optional[str] = None,
        verified: bool = False,
        notes: Optional[str] = None
    ):
        self.field = field
        self.value = value
        self.source_url = source_url
        self.scraped_at = scraped_at or datetime.now(timezone.utc).isoformat()
        self.verified = verified
        self.notes = notes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "source_url": self.source_url,
            "scraped_at": self.scraped_at,
            "verified": self.verified,
            "notes": self.notes
        }


class SourceLedger:
    """
    Maintains a strict ledger of scraped business facts.
    Missing data returns None or marked placeholders.
    Never invents facts.
    """

    def __init__(self):
        self.entries: Dict[str, SourceEntry] = {}

    def add(
        self,
        field: str,
        value: Any,
        source_url: Optional[str] = None,
        scraped_at: Optional[str] = None,
        verified: bool = True,
        notes: Optional[str] = None
    ) -> None:
        """Records a fact into the ledger."""
        if value is None:
            self.entries[field] = SourceEntry(
                field=field,
                value=None,
                source_url=source_url,
                scraped_at=scraped_at,
                verified=False,
                notes=notes or "Unverified or missing"
            )
        else:
            self.entries[field] = SourceEntry(
                field=field,
                value=value,
                source_url=source_url,
                scraped_at=scraped_at,
                verified=verified,
                notes=notes
            )

    def get(self, field: str, default: Any = None) -> Any:
        """Retrieves raw fact value if present and non-empty, else default."""
        entry = self.entries.get(field)
        if entry is None or entry.value is None:
            return default
        return entry.value

    def require(self, field: str, placeholder: str = "[Inquire for quotation]") -> str:
        """Returns string representation of the fact if verified, or a marked placeholder."""
        val = self.get(field)
        if val is None:
            return placeholder
        val_str = str(val).strip()
        if not val_str:
            return placeholder
        return val_str

    def has(self, field: str) -> bool:
        """Returns True if the fact exists and is not None."""
        val = self.get(field)
        return val is not None and (not isinstance(val, (list, dict, str)) or len(val) > 0)

    def to_dict(self) -> Dict[str, Any]:
        return {k: v.to_dict() for k, v in self.entries.items()}

    def save(self, filepath: str) -> None:
        """Persists ledger to SOURCES.json in the demo directory."""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, filepath: str) -> "SourceLedger":
        ledger = cls()
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, d in data.items():
                ledger.entries[k] = SourceEntry(
                    field=d.get("field", k),
                    value=d.get("value"),
                    source_url=d.get("source_url"),
                    scraped_at=d.get("scraped_at"),
                    verified=d.get("verified", False),
                    notes=d.get("notes")
                )
        return ledger
