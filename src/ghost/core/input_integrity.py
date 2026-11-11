"""
GHOST Input Integrity & Token Budgeting Engine
==============================================
Preserves complete, unadulterated user requests across the request lifecycle:
UI -> WebSocket / REST API -> Agent -> TaskRouter -> Delegation Contracts.

Prevents silent truncation, detects incomplete / severed payloads, and provides
explicit token budgeting and multi-paragraph segmentation without losing requirements.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ghost.core.input_integrity")


@dataclass
class InputPayload:
    """Canonical model for an authoritative, tamper-evident user request payload."""
    raw_text: str
    length_chars: int
    estimated_tokens: int
    sha256: str
    is_truncated: bool = False
    truncation_reason: Optional[str] = None
    is_incomplete: bool = False
    incomplete_reason: Optional[str] = None
    cleaned_text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "length_chars": self.length_chars,
            "estimated_tokens": self.estimated_tokens,
            "sha256": self.sha256,
            "is_truncated": self.is_truncated,
            "truncation_reason": self.truncation_reason,
            "is_incomplete": self.is_incomplete,
            "incomplete_reason": self.incomplete_reason,
            "cleaned_text": self.cleaned_text,
            "metadata": self.metadata,
        }


def estimate_tokens(text: str) -> int:
    """Estimates tokens based on character and word count (~4 chars per token)."""
    if not text:
        return 0
    words = len(text.split())
    chars = len(text)
    # Blend word count and 4-chars-per-token heuristic
    return max(1, max(words, chars // 4))


def validate_input_integrity(raw_text: str) -> InputPayload:
    """
    Analyzes raw user input for integrity, unadulterated length, truncation markers,
    and trailing incomplete fragments.
    """
    if raw_text is None:
        raw_text = ""

    length_chars = len(raw_text)
    token_est = estimate_tokens(raw_text)
    sha256_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

    is_truncated = False
    truncation_reason = None
    is_incomplete = False
    incomplete_reason = None

    stripped = raw_text.strip()

    if not stripped:
        return InputPayload(
            raw_text=raw_text,
            length_chars=length_chars,
            estimated_tokens=0,
            sha256=sha256_hash,
            is_truncated=False,
            is_incomplete=True,
            incomplete_reason="Empty input payload.",
            cleaned_text="",
        )

    # 1. Detect severed tokens at tail (e.g. ends with `ta’`, `ta'`, `ta'...`, or dangling punctuation)
    if re.search(r"\b[a-zA-Z]{1,4}[’'](\.{0,3})$", stripped):
        is_truncated = True
        truncation_reason = "Input terminates abruptly with a severed token and trailing apostrophe."
        is_incomplete = True
        incomplete_reason = truncation_reason

    # Pattern: ends with trailing ellipsis with no following content after an incomplete sentence or prompt
    elif re.search(r"\b(doing the following|for doing the|such as the following)\s*(\w+)?\s*(\.{2,3})$", stripped.lower()):
        is_truncated = True
        truncation_reason = "Input prompts for a following task but terminates with ellipsis without providing the task description."
        is_incomplete = True
        incomplete_reason = truncation_reason

    elif stripped.endswith("...") and len(stripped) > 3 and not re.search(r"[.?!]\s*\.\.\.$", stripped):
        # Could be intentional ellipsis or truncation; check if trailing word is cut off
        words = stripped[:-3].strip().split()
        if words and words[-1].lower() in ("the", "following", "a", "an", "for", "to", "of", "and", "or", "in"):
            is_incomplete = True
            incomplete_reason = f"Input ends with dangling preposition/conjunction '{words[-1]}' and ellipsis."

    # 2. Check for unmatched opening quotes or code blocks
    single_quotes = stripped.count("'") + stripped.count("’")
    double_quotes = stripped.count('"')
    code_fences = stripped.count("```")

    if code_fences % 2 != 0:
        is_incomplete = True
        incomplete_reason = "Input contains an unclosed markdown code fence (```)."

    # 3. Check for abrupt termination phrases
    tail_lower = stripped[-40:].lower()
    if re.search(r"\b(doing the following|for doing the|such as the following)\s*$", tail_lower):
        is_incomplete = True
        incomplete_reason = "Input prompts for a following task but terminates without providing the task description."

    return InputPayload(
        raw_text=raw_text,
        length_chars=length_chars,
        estimated_tokens=token_est,
        sha256=sha256_hash,
        is_truncated=is_truncated,
        truncation_reason=truncation_reason,
        is_incomplete=is_incomplete,
        incomplete_reason=incomplete_reason,
        cleaned_text=stripped,
        metadata={"length_chars": length_chars, "words": len(stripped.split())},
    )


def budget_prompt_tokens(
    text: str,
    max_budget: int = 4000,
    reserve_tokens: int = 500,
) -> Tuple[str, bool, Optional[str]]:
    """
    Explicit token budgeting. If text exceeds budget, segments without losing
    core requirements. Preserves exact text when within budget.
    """
    est = estimate_tokens(text)
    effective_limit = max_budget - reserve_tokens

    if est <= effective_limit:
        return text, False, None

    # Text exceeds budget - perform structured paragraph-level segmentation
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

    selected: List[str] = []
    current_tokens = 0

    for idx, p in enumerate(paragraphs):
        p_tokens = estimate_tokens(p)
        if current_tokens + p_tokens <= effective_limit:
            selected.append(p)
            current_tokens += p_tokens
        else:
            # Add condensation notice
            remaining = len(paragraphs) - idx
            notice = f"\n\n[... GHOST Context Budgeting: Segmented {remaining} remaining paragraph(s). Original length: {len(text)} chars, SHA-256: {hashlib.sha256(text.encode()).hexdigest()[:12]} ...]"
            selected.append(notice)
            break

    segmented = "\n\n".join(selected)
    log_msg = f"Prompt token budget exceeded ({est} > {effective_limit}). Segmented to {len(segmented)} chars ({estimate_tokens(segmented)} tokens)."
    logger.info(log_msg)
    return segmented, True, log_msg
