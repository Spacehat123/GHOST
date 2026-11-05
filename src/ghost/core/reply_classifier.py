"""Deterministic Reply Classifier — P2 (Roadmap §2 Stage 4).

The constitution applied to conversation: classification is pure Python and
testable. An LLM may *later* refine borderline cases, but never override:
- ANGRY/CONFUSED -> always freeze + ping (§0.4), the LLM cannot talk its way past it
- call-needed detection is rule-based (§2 Stage 4): explicit ask, price pushback
  beyond tolerance, or exchange-count overflow

T5 honesty fix: this classifier NO LONGER emits typed-in confidence numbers.
`ReplyVerdict.confidence` is None at runtime; it is populated ONLY by measured
evaluations over a labeled corpus (see tests/test_t5_verifier.py), and the
solo-reply gate (`ghost_may_reply_alone`) requires a MEASURED confidence —
which rule classification never provides. Auto-reply therefore stays closed
unless a real measurement (e.g. the FAQ KB's computed token-overlap) backs it.

Returns a ReplyVerdict with: category, confidence (None until measured),
can_ghost_answer, needs_call, freeze_sequence, ping_operator, reasons
(evidence — every verdict cites which rule fired, same honesty contract as
three_qa).
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

# ----------------------------------------------------------------- categories
INTERESTED = "INTERESTED"
QUESTION = "QUESTION"
OBJECTION = "OBJECTION"
NOT_NOW = "NOT_NOW"
ANGRY = "ANGRY"
AUTO_REPLY = "AUTO_REPLY"
BOUNCE = "BOUNCE"

ALL_CATEGORIES = (INTERESTED, QUESTION, OBJECTION, NOT_NOW, ANGRY, AUTO_REPLY, BOUNCE)

ANSWER_CONFIDENCE_FLOOR = 0.95   # applied to MEASURED confidence only
DEFAULT_PRICE_PUSHBACK_TOLERANCE_PCT = 15.0   # §2 Stage 4
EXCHANGES_BEFORE_CALL = 3                     # §2 Stage 4


@dataclass
class ReplyVerdict:
    category: str
    confidence: Optional[float] = None   # MEASURED only — never typed in (T5)
    reasons: List[str] = field(default_factory=list)
    can_ghost_answer: bool = False
    needs_call: bool = False
    freeze_sequence: bool = False
    ping_operator: bool = False
    ping_category: Optional[str] = None        # §10: (a) money (b) distress (c) unverifiable


# ------------------------------------------------------------------ rule data
# Ordered by severity; first matching family wins the category, all matches
# are recorded as reasons.

_ANGRY = [
    r"\bangry\b", r"\bfurious\b", r"\bdisgusting\b", r"\bappalled\b",
    r"how dare", r"\binsult", r"\bridiculous\b", r"\bpathetic\b",
    r"\bspam\b", r"\bharass", r"never contact", r"do not (email|contact|message) me",
    r"remove me", r"unsubscribe",
]
_NOT_NOW = [
    r"not (right|this) (time|moment|week|month|quarter)", r"\btoo (busy|expensive|soon)\b",
    r"(next|another|following) (year|quarter|month|season)", r"we'?re (fine|good|okay|ok) for now",
    r"(closing|closed) (for|the) (season|year)", r"revisit (in|next)",
    r"\bmaybe (later|next)\b", r"not (interested|looking) (right now|at the moment|currently)",
]
_OBJECTION = [
    r"\bexpensive\b", r"\bcheaper\b", r"\btoo (much|costly|pricey)\b",
    r"\bdiscount\b", r"\bprice\b.*\b(high|steep|firm)\b", r"already (have|worked with)",
    r"\bcompetitor\b", r"\boutsource", r"what.?s? (the )?catch",
]
_AUTO_REPLY = [
    r"^auto[- ]?(reply|response|generated)", r"out of office", r"\bvacation\b",
    r"away from (the )?office", r"automatic reply", r"\bundersigned\b",
    r"delivery (status )?notification", r"undeliver", r"mailbox (is )?full",
    r"mail delivery subsystem", r"delivery has failed", r"address (does not exist|not found)",
]
_BOUNCE = [
    r"550 5\.", r"user unknown", r"no such (user|address|mailbox)", r"recipient address rejected",
    r"domain (not found|does not exist)", r"\bbounce\b", r"permanent error",
]
_CALL_ASK = [
    r"call me", r"give me a (call|ring)", r"can (we )?(talk|speak|chat) (on (a )?call|by phone|via phone)?",
    r"(schedule|book|set up) a call", r"available for a (quick )?call", r"hop on a call",
    r"\bphone\b.*\b(call|chat)\b", r"(zoom|meet|teams) (link|call|meeting)",
]
_QUESTION = [
    r"\?", r"^(what|how|when|where|who|which|can|could|would|do|does|is|are|will)\b.*\?",
    r"what.?s? (included|the price|your process)", r"how (much|long|does it work)",
    r"(portfolio|examples|samples) of", r"can you (show|send|share)",
]
_INTERESTED = [
    r"\binterested\b", r"sounds (good|great|interesting)", r"i(')?(m| am) in",
    r"(love|like) (it|the|this)", r"\bimpressive\b", r"let'?s (do|proceed|move forward|talk)",
    r"send (me )?(the |more )?(details|info|contract|agreement|quote|invoice)",
    r"what are the next steps", r"how do we (start|begin|proceed)", r"\byes\b,? (let'?s|please|send)",
]


def _matches(text_lower: str, patterns: List[str]) -> List[str]:
    hits = []
    for p in patterns:
        if re.search(p, text_lower):
            hits.append(p)
    return hits


def classify_reply(
    text: str,
    exchange_count: int = 1,
    discount_requested_pct: Optional[float] = None,
    price_pushback_tolerance_pct: float = DEFAULT_PRICE_PUSHBACK_TOLERANCE_PCT,
) -> ReplyVerdict:
    """Classify one inbound reply. Deterministic: same inputs, same verdict."""
    reasons: List[str] = []
    text = (text or "").strip()
    lower = text.lower()

    def verdict(category: str, **flags) -> ReplyVerdict:
        v = ReplyVerdict(category=category, reasons=list(reasons), **flags)
        return v

    if not text:
        reasons.append("empty reply")
        return verdict(BOUNCE, freeze_sequence=False, ping_operator=True, ping_category="unverifiable")

    # ---- hard families first: they always win and always escalate ----------
    angry = _matches(lower, _ANGRY)
    if angry:
        reasons.append(f"distress markers: {angry[:3]}")
        return verdict(ANGRY, can_ghost_answer=False, freeze_sequence=True,
                       ping_operator=True, ping_category="distress")

    bounce = _matches(lower, _BOUNCE)
    if bounce:
        reasons.append(f"bounce markers: {bounce[:2]}")
        return verdict(BOUNCE, freeze_sequence=True, ping_operator=False)

    auto = _matches(lower, _AUTO_REPLY)
    # Unsubscribe alone is a suppression command even inside auto-replies.
    if auto and "unsubscribe" not in lower and "remove me" not in lower and "do not" not in lower:
        reasons.append(f"automated message: {auto[:2]}")
        return verdict(AUTO_REPLY, freeze_sequence=True, ping_operator=False)

    # ---- call-needed triggers (§2 Stage 4), evaluated before soft families --
    call_ask = _matches(lower, _CALL_ASK)
    big_pushback = discount_requested_pct is not None and discount_requested_pct > price_pushback_tolerance_pct
    overflow = exchange_count >= EXCHANGES_BEFORE_CALL
    needs_call = bool(call_ask or big_pushback or overflow)
    if call_ask:
        reasons.append("explicit call request")
    if big_pushback:
        reasons.append(f"price pushback {discount_requested_pct:.0f}% exceeds {price_pushback_tolerance_pct:.0f}% tolerance")
    if overflow and not call_ask:
        reasons.append(f"{exchange_count} exchanges without close — call recommended")

    # ---- soft families: question vs objection vs interested ----------------
    objection = _matches(lower, _OBJECTION)
    interested = _matches(lower, _INTERESTED)
    question = _matches(lower, _QUESTION)
    if "unsubscribe" in lower or "remove me" in lower or "never contact" in lower:
        # Already caught by ANGRY; unreachable, kept for explicitness.
        return verdict(ANGRY, freeze_sequence=True, ping_operator=True, ping_category="distress")

    if interested and not objection:
        reasons.append(f"interest markers: {interested[:3]}")
        v = verdict(INTERESTED,
                    needs_call=needs_call,
                    ping_operator=needs_call, ping_category="unverifiable" if needs_call else None)
        if question:
            reasons.append("interest contains a question — answer it in the same reply")
            v.reasons = list(reasons)
        return v

    if objection:
        reasons.append(f"price/competition objection: {objection[:3]}")
        return verdict(OBJECTION, needs_call=needs_call,
                       ping_operator=big_pushback, ping_category="money" if big_pushback else None)

    not_now = _matches(lower, _NOT_NOW)
    if not_now:
        reasons.append(f"timing markers: {not_now[:2]}")
        return verdict(NOT_NOW, needs_call=needs_call, ping_operator=False)

    if question:
        reasons.append(f"question markers: {question[:2]}")
        return verdict(QUESTION, needs_call=needs_call,
                       can_ghost_answer=True,
                       ping_operator=needs_call,
                       ping_category="unverifiable" if needs_call else None)

    # ---- nothing matched: fail closed, it's unverifiable --------------------
    reasons.append("no rule matched — cannot verify intent")
    return verdict(QUESTION, can_ghost_answer=False, needs_call=needs_call,
                   ping_operator=True, ping_category="unverifiable")


def ghost_may_reply_alone(v: ReplyVerdict) -> bool:
    """The single gate every auto-responder must call (fail closed).
    Requires MEASURED confidence — rule classification provides none, so the
    gate stays closed until a real evaluation (e.g. the FAQ KB's computed
    overlap) stamps a number."""
    return (
        not v.freeze_sequence
        and not v.ping_operator
        and v.can_ghost_answer
        and v.confidence is not None
        and v.confidence >= ANSWER_CONFIDENCE_FLOOR
    )
