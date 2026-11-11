"""Proactive Intelligence Orchestrator for GHOST.

Conforms to ARCHITECTURE_TARGET.md §Layer 4 & PHASE 5 PROACTIVE INTELLIGENCE:
- Connects Universal EventBus to PriorityEngine and TaskOrchestrator.
- Evaluates real-time event streams according to the current Operating Mode:
  1. AMBIENT: Observe, summarize, only escalate on CRITICAL alerts.
  2. OBJECTIVE: Continuously drive progress toward active long-term goals.
  3. REACTIVE: Autonomous root-cause investigation of hardware and software anomalies.
- Submits bounded, observable tasks to TaskOrchestrator with approval gating for high-risk actions.
- Emits intelligence.proactive.decision events to the EventBus with full evidence chains.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.core.system_state import SystemStateManager, get_system_state_manager
from ghost.core.task_orchestrator import TaskOrchestrator, TaskStatus, get_task_orchestrator
from ghost.intelligence.priority_engine import EventPriorityEngine, PrioritizationDecision

logger = logging.getLogger("ghost.intelligence.proactive")


class ProactiveOrchestrator:
    """Listens to environmental events and autonomously triggers investigations or objective tasks."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        priority_engine: Optional[EventPriorityEngine] = None,
        task_orchestrator: Optional[TaskOrchestrator] = None,
        state_manager: Optional[SystemStateManager] = None,
    ):
        self.event_bus = event_bus or get_event_bus()
        self.priority_engine = priority_engine or EventPriorityEngine()
        self.task_orchestrator = task_orchestrator or get_task_orchestrator(bus=self.event_bus)
        self.state_manager = state_manager or get_system_state_manager(bus=self.event_bus)

        self._sub_id: Optional[str] = None
        self._recent_decisions: List[PrioritizationDecision] = []

    def start(self) -> None:
        """Subscribes to all EventBus topics (*)."""
        if self._sub_id is None:
            self._sub_id = self.event_bus.subscribe("*", self._on_event)
            logger.info("ProactiveOrchestrator started and subscribed to EventBus.")

    def stop(self) -> None:
        """Unsubscribes from EventBus."""
        if self._sub_id:
            self.event_bus.unsubscribe(self._sub_id)
            self._sub_id = None
            logger.info("ProactiveOrchestrator stopped.")

    async def _on_event(self, event: Event) -> None:
        """Evaluates incoming event and acts proactively if warranted."""
        # Avoid recursive intelligence loops
        if event.source.startswith("intelligence.") or event.event_type.startswith("intelligence."):
            return

        mode = self.state_manager.get_snapshot().operating_mode
        decision = self.priority_engine.evaluate(event, operating_mode=mode)
        self._recent_decisions.append(decision)
        if len(self._recent_decisions) > 50:
            self._recent_decisions.pop(0)

        # Publish the prioritization decision for auditable intelligence feed
        decision_event = Event(
            event_type="intelligence.proactive.decision",
            source="intelligence.proactive",
            severity=EventSeverity.INFO if decision.priority_score < 7.0 else EventSeverity.WARNING,
            payload=decision.to_dict(),
            correlation_id=event.event_id,
        )
        await self.event_bus.publish(decision_event)

        # Trigger autonomous workflow if recommended
        if decision.action_recommended in ("INVESTIGATE", "CREATE_OBJECTIVE_TASK"):
            await self._create_proactive_task(event, decision)

    async def _create_proactive_task(self, trigger_event: Event, decision: PrioritizationDecision) -> None:
        """Creates a managed Task in the TaskOrchestrator with permission and approval boundaries."""
        # Determine title and description
        if decision.action_recommended == "INVESTIGATE":
            title = f"Proactive Investigation: {trigger_event.event_type} on {decision.assigned_domain}"
            desc = f"{decision.reason} Triggered by event {trigger_event.event_id}."
        else:
            title = f"Objective Task: Advance {decision.assigned_domain} objective"
            desc = decision.reason

        # High-risk actions require operator approval
        requires_approval = (
            decision.assigned_domain in ("physical", "business")
            or decision.priority_score >= 9.0
            or "critical" in trigger_event.severity.value.lower()
        )

        obj_id = decision.evidence.get("matched_objective_id") or f"obj-proactive-{uuid.uuid4().hex[:6]}"

        task = self.task_orchestrator.submit_task(
            objective_id=obj_id,
            assigned_agent=decision.assigned_agent,
            title=title,
            description=desc,
            input_payload={
                "trigger_event": trigger_event.to_dict(),
                "decision": decision.to_dict(),
                "requires_approval": requires_approval,
            },
            required_permissions=[f"{decision.assigned_domain}.investigate"],
        )

        if requires_approval:
            self.task_orchestrator.request_approval(task.task_id)
            logger.info(f"Proactive task {task.task_id} requires operator approval (Domain: {decision.assigned_domain}).")
        else:
            logger.info(f"Proactive task {task.task_id} queued for agent '{decision.assigned_agent}'.")

    def get_recent_decisions(self) -> List[PrioritizationDecision]:
        return list(self._recent_decisions)


# Global Singleton ProactiveOrchestrator
_GLOBAL_PROACTIVE_ORCH: Optional[ProactiveOrchestrator] = None

def get_proactive_orchestrator(
    event_bus: Optional[EventBus] = None,
    priority_engine: Optional[EventPriorityEngine] = None,
    task_orchestrator: Optional[TaskOrchestrator] = None,
    state_manager: Optional[SystemStateManager] = None,
) -> ProactiveOrchestrator:
    global _GLOBAL_PROACTIVE_ORCH
    if _GLOBAL_PROACTIVE_ORCH is None:
        _GLOBAL_PROACTIVE_ORCH = ProactiveOrchestrator(
            event_bus=event_bus,
            priority_engine=priority_engine,
            task_orchestrator=task_orchestrator,
            state_manager=state_manager,
        )
    return _GLOBAL_PROACTIVE_ORCH
