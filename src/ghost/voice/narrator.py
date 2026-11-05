"""Narrator — Ghost's voice tracks work, and only work (Roadmap §14).

The honesty constitution applied to speech: the narrator SUBSCRIBES to the
audit ledger. It can only utter what an event just proved. There is no code
path where Ghost says "found 211 photographs" unless a recon event with that
number exists in the same timestamp. Templates are deterministic; fluency is
the only thing left to the LLM (later — v1 is fully deterministic).

Interaction classes (§14.1) govern the milestone budget:
  instant(<2s): no milestones — the action itself is the answer
  short(2-30s): <= 2 milestone lines
  long(>30s):   <= 1 line per 8s of task
  background:   silent until the outcome event

Throttle: >= MIN_MILESTONE_GAP_S between lines per task (§14.2.7).
Speech sink is injected: Kokoro WAV stream, notify-send, or a test recorder.
"""
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from ghost.core.audit import AuditLedger, audit

logger = logging.getLogger("ghost.voice.narrator")

MIN_MILESTONE_GAP_S = 4.0
LONG_CLASS_GAP_S = 8.0

# ------------------------------------------------------------------- templates
# event_type -> fn(payload) -> utterance or None.
# RULE: a template may use ONLY fields present in the event payload.

TEMPLATES: Dict[str, Callable[[Dict[str, Any]], Optional[str]]] = {
    "task.acknowledged": lambda p: f"On it{' — ' + p['summary'] if p.get('summary') else '.'}"
                                   if p.get("task_class") != "long"
                                   else f"I'm doing it now{' — ' + p['summary'] if p.get('summary') else ''}.",
    "lead_hunt.candidates": lambda p: f"Found {p['count']} businesses without websites.",
    "demo.build_started": lambda p: f"Building the demo for {p.get('business', 'the top lead')}.",
    "qa.passed": lambda p: (f"QA passed — {p['screenshots']} screenshots clean, "
                            f"{p['fps']} frames per second."),
    "qa.failed": lambda p: "QA failed — I'm not shipping this. Fixing first.",
    "demo.deployed": lambda p: f"Demo's live at {p['url']}.",
    "payment.verified": lambda p: (f"Payment confirmed — {p['amount']} {p['currency']}. "
                                   "Contract sent."),
    "welcome.sent": lambda p: "Welcome pack sent. Onboarding link is in their inbox.",
    "incident.resolved": lambda p: f"Fixed: {p['resolution']}.",
    "incident.escalated": lambda p: "This one needs you — details on screen.",
    "task.done": lambda p: f"Done{' — ' + p['summary'] if p.get('summary') else '.'}",
    "task.still_running": lambda p: (f"Still on it — {p['stage']}, "
                                     f"{p['elapsed_s']:.0f} seconds in."),
    "task.interrupted": lambda p: (f"Stopped. {p['completed']} step(s) finished, "
                                   f"{p['suspended']} suspended — awaiting orders."),
}

# Events we never speak aloud (they'd be noise; they live in the digest).
SILENT_EVENTS = {"job.enqueued", "job.done", "job.retry", "policy.decision",
                 "scheduler.cycle", "outbound.blocked", "snapshot.created"}


class Narrator:
    """Event-bus subscriber. Feed it audit events; it speaks the truth."""

    def __init__(self, speak: Optional[Callable[[str], Any]] = None,
                 ledger: Optional[AuditLedger] = None,
                 now: Optional[Callable[[], float]] = None):
        self.speak = speak or self._log_speak
        self.ledger = ledger
        self._last_spoke_at: Dict[str, float] = {}   # task_id -> ts
        self._milestones: Dict[str, int] = {}        # task_id -> count
        self._task_class: Dict[str, str] = {}
        self._now = now or time.time
        self.spoken: List[Dict[str, Any]] = []       # tape: utterances + evidence ids

    @staticmethod
    def _log_speak(text: str) -> None:
        logger.info("[voice] %s", text)

    # ---------------------------------------------------------------- feeding

    def on_event(self, event_type: str, payload: Dict[str, Any],
                 subject: Optional[str] = None, ts: Optional[float] = None) -> Optional[str]:
        """Process one audit event; returns the utterance if one was spoken."""
        if event_type in SILENT_EVENTS:
            return None
        template = TEMPLATES.get(event_type)
        if template is None:
            return None
        try:
            utterance = template(payload or {})
        except (KeyError, TypeError, ValueError):
            # A template that can't be filled from the payload stays SILENT —
            # never guess numbers (no narration without an event, strictly).
            audit("narrator.template_skipped", subject=subject, event=event_type,
                  reason="payload_missing_fields")
            return None
        if not utterance:
            return None

        task_id = subject or "global"
        task_class = (payload or {}).get("task_class", "long")
        if not self._allow(task_id, event_type, task_class):
            return None

        self.speak(utterance)
        _broadcast_speech("narrator.speak",
                          {"utterance": utterance, "event": event_type,
                           "task": task_id, "task_class": task_class})
        entry = {"ts": ts or self._now(), "task": task_id,
                 "event": event_type, "utterance": utterance}
        self.spoken.append(entry)
        # The spoken line itself becomes evidence-linked in the ledger.
        audit("narrator.spoke", subject=task_id, event=event_type,
              utterance=utterance)
        return utterance

    def _allow(self, task_id: str, event_type: str, task_class: str) -> bool:
        now = self._now()
        last = self._last_spoke_at.get(task_id, 0.0)
        gap = LONG_CLASS_GAP_S if task_class == "long" else MIN_MILESTONE_GAP_S
        # Terminal events always speak (done/stopped are the payoff, §14.1).
        terminal = event_type in ("task.done", "task.interrupted", "payment.verified",
                                  "welcome.sent", "qa.failed", "incident.escalated")
        if not terminal and now - last < gap:
            return False
        budget = {"instant": 0, "short": 2}.get(task_class, 10**6)
        used = self._milestones.get(task_id, 0)
        if not terminal and used >= budget:
            return False
        self._last_spoke_at[task_id] = now
        self._milestones[task_id] = used + 1
        self._task_class[task_id] = task_class
        return True

    # --------------------------------------------------------- ledger replay

    def attach_to_ledger(self, ledger: Optional[AuditLedger] = None) -> None:
        """Replay today's events through the narrator (used at startup and in
        tests to prove every spoken line traces to a logged event)."""
        ledger = ledger or self.ledger or AuditLedger.get_instance()
        for row in ledger.query(limit=500):
            self.on_event(row["event_type"], row.get("payload") or {},
                          subject=row.get("subject"), ts=row["ts"])

    def tape(self) -> List[Dict[str, Any]]:
        """The recording: every utterance with the event that caused it (§14.3)."""
        return list(self.spoken)


def proof_of_honesty(narrator: Narrator, ledger: AuditLedger) -> bool:
    """§14.3 verification: every spoken line must map to a ledger event of the
    same type for the same subject, at or before the utterance timestamp."""
    events = {}
    for row in ledger.query(limit=1000):
        events.setdefault((row["event_type"], row.get("subject")), []).append(row["ts"])
    for line in narrator.tape():
        candidates = events.get((line["event"], line["task"]), [])
        if not any(ts <= line["ts"] + 0.001 for ts in candidates):
            return False
    return True


# ------------------------------------------------------------- GUI broadcast
# The orb narrates too (§14.2.5): the GUI gets every spoken line through this
# hook so the orb can show a "working" pulse while Ghost is busy. Set by the
# server startup; defaults to no-op for headless use.

_speech_broadcast: Optional[Callable[[str, Dict[str, Any]], Any]] = None


def set_speech_broadcast(fn: Optional[Callable[[str, Dict[str, Any]], Any]]) -> None:
    global _speech_broadcast
    _speech_broadcast = fn


def _broadcast_speech(event_type: str, payload: Dict[str, Any]) -> None:
    if _speech_broadcast:
        try:
            _speech_broadcast(event_type, payload)
        except Exception:
            pass
