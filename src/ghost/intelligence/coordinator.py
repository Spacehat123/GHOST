"""Cross-Domain Intelligence Coordinator for GHOST.

Conforms to ARCHITECTURE_TARGET.md & PHASE 8 CROSS-DOMAIN INTELLIGENCE:
- Unifies all 7 operational domains into a coordinated intelligence system:
  1. System Intelligence (hardware, thermal, memory, recovery)
  2. Development Intelligence (git repos, test suites, sidecars)
  3. Research Intelligence (web recon, source ledgers, dossiers)
  4. Personal Intelligence (context, objectives, schedules, briefings)
  5. Physical Intelligence / Smart Home (Home Assistant, MQTT, actuation)
  6. Business Intelligence (lead hunting, demos, CRM)
  7. Creative Intelligence (CAD OpenSCAD, Blender 3D, web components)
- Handles cross-agent task delegation with parent-child dependency tracking.
- Orchestrates multi-domain cascades (e.g., test failure -> dev fix -> verification;
  business demo -> creative 3D assets; personal focus mode -> physical smart home scene).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from ghost.agents import get_available_agents
from ghost.agents.base import AgentContract
from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.core.system_state import SystemStateManager, get_system_state_manager
from ghost.core.task_orchestrator import Task, TaskOrchestrator, TaskStatus, get_task_orchestrator
from ghost.intelligence.objective_manager import ObjectiveManager, get_objective_manager

logger = logging.getLogger("ghost.intelligence.coordinator")


class CrossDomainCoordinator:
    """Coordinates cross-agent delegation and multi-domain intelligence cascades."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        task_orchestrator: Optional[TaskOrchestrator] = None,
        state_manager: Optional[SystemStateManager] = None,
        objective_manager: Optional[ObjectiveManager] = None,
    ):
        self.event_bus = event_bus or get_event_bus()
        self.task_orchestrator = task_orchestrator or get_task_orchestrator(event_bus=self.event_bus)
        self.state_manager = state_manager or get_system_state_manager(bus=self.event_bus)
        self.objective_manager = objective_manager or get_objective_manager()

        self._sub_id: Optional[str] = None
        self._delegation_history: List[Dict[str, Any]] = []

    def start(self) -> None:
        """Starts cross-domain event coordination."""
        if self._sub_id is None:
            self._sub_id = self.event_bus.subscribe("*", self._on_event)
            logger.info("CrossDomainCoordinator active and listening to event stream.")

    def stop(self) -> None:
        if self._sub_id:
            self.event_bus.unsubscribe(self._sub_id)
            self._sub_id = None
            logger.info("CrossDomainCoordinator stopped.")

    async def delegate_task(
        self,
        from_agent: str,
        to_agent: str,
        goal: str,
        payload: Optional[Dict[str, Any]] = None,
        parent_task_id: Optional[str] = None,
    ) -> Task:
        """Enables one specialized agent to delegate a sub-objective to another specialized agent."""
        obj_id = f"obj-delegated-{uuid.uuid4().hex[:6]}"
        task = self.task_orchestrator.submit_task(
            objective_id=obj_id,
            assigned_agent=to_agent,
            title=f"Delegated from {from_agent}: {goal}",
            description=f"Cross-domain subtask assigned by {from_agent}.",
            input_payload={
                **(payload or {}),
                "parent_task_id": parent_task_id,
                "delegator": from_agent,
            },
            required_permissions=[f"{to_agent.split('_')[0]}.execute"],
        )

        record = {
            "delegation_id": uuid.uuid4().hex[:8],
            "from_agent": from_agent,
            "to_agent": to_agent,
            "goal": goal,
            "task_id": task.task_id,
            "parent_task_id": parent_task_id,
        }
        self._delegation_history.append(record)

        # Broadcast cross-domain delegation event
        await self.event_bus.publish(
            Event(
                event_type="cross_domain.delegation",
                source="coordinator.cross_domain",
                severity=EventSeverity.INFO,
                payload=record,
                correlation_id=task.task_id,
            )
        )
        logger.info(f"Cross-domain delegation: [{from_agent}] -> [{to_agent}] for '{goal}' (Task {task.task_id})")
        return task

    async def _on_event(self, event: Event) -> None:
        """Evaluates cross-domain ripple effects and coordinates inter-agent responses."""
        if event.source.startswith("coordinator."):
            return

        etype = event.event_type

        # Cascade 1: Dev test failure -> Dev Agent diagnosis
        if etype == "dev.test.failure":
            await self._handle_dev_failure_cascade(event)

        # Cascade 2: Sustained system resource anomaly -> System Agent root cause & backoff
        elif etype == "system.resource.sustained_anomaly":
            await self._handle_resource_pressure_cascade(event)

        # Cascade 3: Research discovery -> Knowledge injection & Business Lead enrichment
        elif etype == "research.source.discovered":
            await self._handle_research_discovery_cascade(event)

        # Cascade 4: Personal focus mode -> Smart home ambient lighting adjustment
        elif etype == "personal.mode.focus_engaged":
            await self._handle_focus_smart_home_cascade(event)

    async def _handle_dev_failure_cascade(self, event: Event) -> None:
        """Routes test failure to DevelopmentIntelligenceAgent."""
        test_path = event.payload.get("test", "unknown")
        task = self.task_orchestrator.submit_task(
            objective_id=f"obj-dev-fix-{uuid.uuid4().hex[:6]}",
            assigned_agent="dev_agent",
            title=f"Diagnose and Stage Fix for: {test_path}",
            description=f"Automated cross-domain cascade from test failure {event.event_id}.",
            input_payload={"test_path": test_path, "error": event.payload.get("error", "")},
            required_permissions=["dev.test_runner", "dev.sidecar"],
        )
        logger.info(f"Cross-domain cascade spawned dev task {task.task_id} for {test_path}")

    async def _handle_resource_pressure_cascade(self, event: Event) -> None:
        """Handles high resource load by tasking System agent and notifying runtime."""
        alerts = event.payload.get("alerts", {})
        task = self.task_orchestrator.submit_task(
            objective_id=f"obj-sys-pressure-{uuid.uuid4().hex[:6]}",
            assigned_agent="system_agent",
            title="Diagnose Hardware Resource Anomaly",
            description=f"Investigate sustained resource pressure on: {list(alerts.keys())}",
            input_payload={"alerts": alerts, "vitals": event.payload.get("vitals", {})},
            required_permissions=["system.process_inspect"],
        )
        logger.info(f"Cross-domain cascade spawned system recovery task {task.task_id}")

    async def _handle_research_discovery_cascade(self, event: Event) -> None:
        """Extracts discovered research and notifies business agent."""
        url = event.payload.get("url", "")
        summary = event.payload.get("summary", "")
        if "lead" in summary.lower() or "agency" in summary.lower():
            await self.delegate_task(
                from_agent="research_agent",
                to_agent="business_agent",
                goal=f"Evaluate prospect from research discovery: {url}",
                payload={"url": url, "summary": summary},
            )

    async def _handle_focus_smart_home_cascade(self, event: Event) -> None:
        """Personal focus mode triggers physical environment switch."""
        await self.delegate_task(
            from_agent="personal_agent",
            to_agent="physical_agent",
            goal="Set desk ambient scene for Focus Session",
            payload={"action": "toggle_switch", "entity_id": "switch.desk_lamp", "target_state": "on"},
        )

    def get_delegation_history(self) -> List[Dict[str, Any]]:
        return list(self._delegation_history)


# Global Singleton CrossDomainCoordinator
_GLOBAL_COORDINATOR: Optional[CrossDomainCoordinator] = None

def get_cross_domain_coordinator(
    event_bus: Optional[EventBus] = None,
    task_orchestrator: Optional[TaskOrchestrator] = None,
    state_manager: Optional[SystemStateManager] = None,
    objective_manager: Optional[ObjectiveManager] = None,
) -> CrossDomainCoordinator:
    global _GLOBAL_COORDINATOR
    if _GLOBAL_COORDINATOR is None:
        _GLOBAL_COORDINATOR = CrossDomainCoordinator(
            event_bus=event_bus,
            task_orchestrator=task_orchestrator,
            state_manager=state_manager,
            objective_manager=objective_manager,
        )
    return _GLOBAL_COORDINATOR
