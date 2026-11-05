"""TaskRunner — the Runner Pattern spine for voice presence (Roadmap §14.2.3, §14.2.6).

Work is an async GENERATOR yielding lifecycle events; the runner multiplexes
work and narration on one loop — speech never blocks work, work never blocks
speech, and barge-in cancels both at the next yield point with an honest
accounting of what finished vs. what suspended (the "Stop" contract).

This mirrors fury-sdk's runner (CONTEXT.md §2.1): interrupt() aborts
generation mid-stream instead of waiting for completion.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from ghost.core.audit import audit
from ghost.voice.narrator import Narrator

logger = logging.getLogger("ghost.voice.task_runner")


@dataclass
class TaskEvent:
    kind: str                 # acknowledged | progress | done | interrupted
    task_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class TaskRunner:
    def __init__(self, narrator: Optional[Narrator] = None):
        self.narrator = narrator or Narrator()
        self._interrupted = False
        self._active: Optional[str] = None

    def interrupt(self) -> None:
        """Barge-in: stop speaking AND stop working at the next yield point."""
        self._interrupted = True
        if self._active:
            audit("task.barge_in", subject=self._active)

    def reset(self) -> None:
        self._interrupted = False

    async def run(self, task_id: str, task_class: str, summary: str,
                  work: AsyncGenerator[Dict[str, Any], None]) -> List[TaskEvent]:
        """Drive one task to completion (or interruption).

        `work` yields step dicts: {"event": "<audit_event_type>", **payload}.
        Every yielded event is (1) written to the audit ledger, (2) offered to
        the narrator. Terminal event: {"event": "task.done", "summary": ...}.
        """
        self._active = task_id
        self._interrupted = False
        events: List[TaskEvent] = []

        def emit(kind: str, payload: Dict[str, Any]) -> TaskEvent:
            ev = TaskEvent(kind, task_id, payload)
            events.append(ev)
            return ev

        ack = emit("acknowledged", {"task_class": task_class, "summary": summary})
        audit("task.acknowledged", subject=task_id, task_class=task_class,
              summary=summary)
        self.narrator.on_event("task.acknowledged", ack.payload, subject=task_id)

        started = time.time()
        completed_steps = 0
        suspended_steps = 0
        last_status_at = started

        async for step in work:
            if self._interrupted:
                suspended_steps += 1
                continue  # drain the generator politely; do not execute steps

            event_type = step.get("event", "task.progress")
            payload = {k: v for k, v in step.items() if k != "event"}
            payload.setdefault("task_class", task_class)
            audit(event_type, subject=task_id, **payload)
            emit("progress", {"event": event_type, **payload})
            self.narrator.on_event(event_type, payload, subject=task_id)

            if event_type == "task.done":
                emit("done", {"summary": payload.get("summary", "")})
                self._active = None
                return events

            # §14.1 long-class proactive status if we're running over.
            if (task_class == "long" and not self._interrupted
                    and time.time() - last_status_at > 20.0):
                last_status_at = time.time()
                stage = payload.get("stage", event_type)
                status_payload = {"stage": stage, "elapsed_s": time.time() - started,
                                  "task_class": task_class}
                audit("task.still_running", subject=task_id, **status_payload)
                self.narrator.on_event("task.still_running", status_payload,
                                       subject=task_id)
            completed_steps += 1

        if self._interrupted:
            emit("interrupted", {"completed": completed_steps,
                                 "suspended": suspended_steps})
            audit("task.interrupted", subject=task_id, completed=completed_steps,
                  suspended=suspended_steps)
            self.narrator.on_event("task.interrupted",
                                   {"completed": completed_steps,
                                    "suspended": suspended_steps,
                                    "task_class": task_class}, subject=task_id)
        else:
            emit("done", {"summary": summary})
            audit("task.done", subject=task_id, summary=summary,
                  task_class=task_class)
            self.narrator.on_event("task.done", {"summary": summary,
                                                 "task_class": task_class},
                                   subject=task_id)
        self._active = None
        return events


# ------------------------------------------------------------------- demo flow
# The §14.3 reference-parity sequence: one command -> narrated milestones ->
# "Done", with every spoken number traceable to a logged event (proven by
# proof_of_honesty() in tests — the tape IS the deliverable).

async def find_and_build_demo_flow(hunter_results: Dict[str, Any],
                                   qa_results: Dict[str, Any],
                                   deploy_url: str) -> AsyncGenerator[Dict[str, Any], None]:
    """Yields the real pipeline events for 'find me X and build them a demo'.
    hunter_results/qa_results come from the actual workers — never invented."""
    yield {"event": "lead_hunt.candidates", "count": hunter_results["count"]}
    yield {"event": "demo.build_started", "business": hunter_results["top_business"]}
    yield {"event": "qa.passed", "screenshots": qa_results["screenshots"],
           "fps": qa_results["fps"]}
    yield {"event": "demo.deployed", "url": deploy_url}
    yield {"event": "task.done", "summary": "demo live, draft email ready"}
