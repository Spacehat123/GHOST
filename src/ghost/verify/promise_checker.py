"""VERIFY PLANE — promise checker (T5): never promise off-terms anything.

The brief's rule: "Never promise anything (price, scope, deadlines) that
isn't in your approved terms." This is the enforcement half — outbound text
is scanned BEFORE the send path, and any detected promise that the approved
terms don't back BLOCKS the send (fail closed).

Detected promise types:
  - currency amounts  (₹25,000 / Rs 25000 / INR 25,000 / $500) — must be in
    the approved list, exact after normalization (commas stripped)
  - deadline commitments ("by Friday", "within 24 hours", "tomorrow 5pm",
    "in 3 days", "end of the week") — none are allowed unless terms say so
  - forbidden phrases (guaranteed / risk-free / lifetime warranty ...)

Terms live in configs/terms.json (operator knob): {"approved_amounts": [...],
"forbidden_phrases": [...], "allow_deadlines": false}. Missing file = empty
approval list: plain conversation passes, ANY detected promise blocks.
The checker never suggests replacements — it blocks and cites what fired.
"""
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_AMOUNT_RE = re.compile(
    r"(?:₹|rs\.?|inr|\$|usd)\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
    r"|(?:([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s?(?:₹|rs\.?|inr|usd))",
    re.IGNORECASE)
_DEADLINE_RES = [
    re.compile(r"\bby (monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|tonight|eod|end of (the )?(day|week|month))\b", re.I),
    re.compile(r"\bwithin \d+\s?(minutes?|mins?|hours?|hrs?|days?|weeks?)\b", re.I),
    re.compile(r"\bin \d+\s?(minutes?|mins?|hours?|hrs?|days?|weeks?)\b", re.I),
    re.compile(r"\b(tomorrow|tonight|today) (at )?\d{1,2}(:\d{2})?\s?(am|pm)?\b", re.I),
    re.compile(r"\bthis (week|weekend|month)\b", re.I),
    re.compile(r"\b(24|48|72)\s?hours\b", re.I),
]
_DEFAULT_FORBIDDEN = ["guaranteed", "risk-free", "risk free", "money-back",
                      "lifetime warranty", "100% success", "no risk"]


def terms_path() -> str:
    return os.environ.get("GHOST_TERMS_PATH") or os.path.join(REPO_DIR, "configs", "terms.json")


def load_terms() -> Dict[str, Any]:
    """Operator knob; unreadable/missing = empty approvals (fail closed for
    promises, plain conversation still passes)."""
    terms: Dict[str, Any] = {"approved_amounts": [], "forbidden_phrases": list(_DEFAULT_FORBIDDEN),
                             "allow_deadlines": False}
    if os.path.exists(terms_path()):
        try:
            with open(terms_path(), "r", encoding="utf-8") as f:
                override = json.load(f)
            terms["approved_amounts"] = [str(a) for a in override.get("approved_amounts", [])]
            terms["forbidden_phrases"] = list(override.get("forbidden_phrases", terms["forbidden_phrases"]))
            terms["allow_deadlines"] = bool(override.get("allow_deadlines", False))
        except Exception:
            pass
    return terms


def _normalize_amount(raw: str) -> str:
    try:
        from decimal import Decimal
        # 'f' format avoids Decimal.normalize()'s scientific notation (25000 -> 2.5E+4)
        return format(Decimal(raw.replace(",", "")).normalize(), "f")
    except Exception:
        return raw.replace(",", "")


def scan(text: str, terms: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Pure scan — no I/O beyond terms loading (injectable for tests)."""
    terms = terms or load_terms()
    text = text or ""
    detected_amounts = []
    for m in _AMOUNT_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        if raw:
            detected_amounts.append(_normalize_amount(raw))
    unapproved_amounts = sorted({a for a in detected_amounts
                                 if a not in {str(x) for x in terms["approved_amounts"]}})
    deadline_hits: List[str] = []
    if not terms.get("allow_deadlines"):
        for rx in _DEADLINE_RES:
            m = rx.search(text)
            if m:
                deadline_hits.append(m.group(0).strip())
    forbidden_hits = [p for p in terms.get("forbidden_phrases", [])
                      if p and p.lower() in text.lower()]
    return {
        "detected_amounts": sorted(set(detected_amounts)),
        "unapproved_amounts": unapproved_amounts,
        "deadline_promises": sorted(set(deadline_hits)),
        "forbidden_phrases": sorted(set(forbidden_hits)),
    }


def check_send_allowed(subject: str, body: str,
                       terms: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
    """The send-path gate: (allowed, scan_result). Blocks on ANY unapproved
    amount, forbidden phrase, or deadline promise."""
    text = f"{subject or ''}\n{body or ''}"
    result = scan(text, terms)
    violations = []
    if result["unapproved_amounts"]:
        violations.append(f"unapproved amounts: {result['unapproved_amounts']}")
    if result["deadline_promises"]:
        violations.append(f"deadline promises: {result['deadline_promises']}")
    if result["forbidden_phrases"]:
        violations.append(f"forbidden phrases: {result['forbidden_phrases']}")
    result["violations"] = violations
    return (not violations), result
