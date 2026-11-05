"""Vertical FAQ Knowledge Base — how Ghost answers questions alone (§2 Stage 4).

'INTERESTED -> Ghost answers from a vertical FAQ knowledge base' — this is
that base. Deterministic retrieval: token-overlap scoring against a curated
per-vertical KB. `answer()` returns (answer_text, confidence) or (None, 0);
the reply loop only auto-answers above the ANSWER_CONFIDENCE_FLOOR —
otherwise it pings you (c). No LLM in the loop for facts: the KB is data the
operator curates; Ghost never invents policy answers.
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from ghost.core.audit import audit

logger = logging.getLogger("ghost.faq")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
KB_PATH = os.environ.get("GHOST_FAQ_KB_PATH") or os.path.join(REPO_DIR, "configs", "faq_knowledge.json")

# Seed KB: honest defaults. The operator curates/extends via the JSON file.
SEED_KB: Dict[str, List[Dict[str, str]]] = {
    "restaurant": [
        {"q": "what is included in the demo website",
         "a": "The concept demo includes a homepage with your menu highlights, a photo gallery of your space, your location and hours, and a reservation button that texts your phone. It is a working preview — the full site adds online reservations, your full menu pages, and Google maps integration."},
        {"q": "how much does the full website cost",
         "a": "Full sites for restaurants typically run sixty to one hundred fifty thousand rupees depending on online reservations and menu management. The exact quote comes after a 15-minute call about your needs — no obligation."},
        {"q": "how long does the full site take",
         "a": "Most restaurant sites go live within two to three weeks of content handover. We handle everything technical; you only review and approve."},
        {"q": "do we need to provide photos",
         "a": "Yes please — your own photos of the space and dishes make the site yours. If photography is a gap, we can arrange a shoot as an add-on."},
        {"q": "will it work on phones",
         "a": "Yes — most of your diners will find you on a phone. Every page is built mobile-first and tested on small screens before launch."},
    ],
    "clinic": [
        {"q": "is patient data safe on the website",
         "a": "The public site stores no patient data. Appointment requests are delivered directly to your staff inbox or phone. Any patient portal work follows applicable health-data rules and is scoped separately."},
        {"q": "can patients book appointments online",
         "a": "The demo includes a request-a-slot button that notifies your front desk. Full online scheduling with slot management is part of the full build."},
    ],
    "default": [
        {"q": "what is this website you sent",
         "a": "It is a free working concept for your business built from your public listings — a preview of what your own website could look like. No payment or commitment is involved in looking at it."},
        {"q": "who are you",
         "a": "We are a small web studio that builds premium websites for local businesses. The concept demo is how we show our work instead of telling."},
        {"q": "is this a scam or spam",
         "a": "Fair question. The demo is free to view, there is no payment page, and you can reply 'unsubscribe' to never hear from us again — that is honored immediately and permanently."},
    ],
}

_WORD = re.compile(r"[a-z0-9₹]+")
_STOP = {"the", "a", "an", "is", "are", "do", "does", "we", "you", "your", "our",
         "i", "me", "my", "can", "will", "and", "or", "of", "for", "to", "in",
         "on", "it", "this", "that", "what", "how", "much", "with"}


def _tokens(text: str) -> set:
    return {t for t in _WORD.findall((text or "").lower()) if t not in _STOP and len(t) > 1}


class FAQKnowledgeBase:
    def __init__(self, kb_path: str = KB_PATH):
        self.kb_path = kb_path
        self.kb = self._load()

    def _load(self) -> Dict[str, List[Dict[str, str]]]:
        if os.path.exists(self.kb_path):
            try:
                with open(self.kb_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                merged = {v: list(entries) for v, entries in SEED_KB.items()}
                for vertical, entries in data.items():
                    merged.setdefault(vertical, []).extend(entries)
                return merged
            except Exception as e:
                logger.warning("FAQ KB unreadable (%s) — using seed", e)
        return {v: list(entries) for v, entries in SEED_KB.items()}

    def answer(self, question: str, vertical: str = "default",
               min_confidence: float = 0.45) -> Tuple[Optional[str], float]:
        """Best KB answer for a question. Deterministic. (text, confidence)."""
        entries = self.kb.get(vertical, []) + self.kb.get("default", [])
        if not entries or not (question or "").strip():
            return None, 0.0
        q_tokens = _tokens(question)
        if not q_tokens:
            return None, 0.0
        best, best_score = None, 0.0
        for entry in entries:
            entry_tokens = _tokens(entry["q"]) | _tokens(entry["a"])
            if not entry_tokens:
                continue
            overlap = len(q_tokens & entry_tokens) / len(q_tokens)
            if overlap > best_score:
                best, best_score = entry["a"], overlap
        if best is None or best_score < min_confidence:
            audit("faq.no_match", vertical=vertical, confidence=round(best_score, 2))
            return None, round(best_score, 3)
        audit("faq.answered", vertical=vertical, confidence=round(best_score, 2))
        return best, round(best_score, 3)

    def can_answer_alone(self, question: str, vertical: str = "default",
                         floor: float = 0.95) -> bool:
        """The §2 Stage 4 rule: >=95% confidence or it pings the operator."""
        text, conf = self.answer(question, vertical)
        return text is not None and conf >= floor

    def verticals(self) -> List[str]:
        return sorted(self.kb.keys())
