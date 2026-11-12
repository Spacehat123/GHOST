"""Base Agent Contract and Verification Interface for GHOST Universal Intelligence System.

Conforms to INTERFACE_CONTRACTS.md §4:
- Mandatory independent verification before an agent considers a task complete.
- Decomposition of high-level objectives into atomic, verifiable Tasks.
- Explicit declared permissions and tool capabilities.
- Live presence registration with SystemStateManager.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.core.system_state import get_system_state_manager
from ghost.core.task_orchestrator import Task

logger = logging.getLogger("ghost.agents.base")


@dataclass
class VerificationEvidence:
    """Ground-truth evidence proving that an agent's task completed successfully."""
    verified: bool
    evidence_type: str                 # "screenshot", "test_report", "dom_element", "readback", "file_checksum"
    artifacts: List[str] = field(default_factory=list)  # Local file paths or generated URLs
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verified": self.verified,
            "evidence_type": self.evidence_type,
            "artifacts": self.artifacts,
            "details": self.details,
        }


class AgentContract(ABC):
    """Abstract contract that all specialized GHOST agents must satisfy."""

    def __init__(
        self,
        agent_id: str,
        name: str,
        responsibility: str,
        supported_tasks: Optional[List[str]] = None,
        required_permissions: Optional[List[str]] = None,
        event_bus: Optional[EventBus] = None,
    ):
        self.agent_id = agent_id
        self.name = name
        self.responsibility = responsibility
        self.supported_tasks = supported_tasks or []
        self.required_permissions = required_permissions or []
        self.event_bus = event_bus or get_event_bus()
        self._state_mgr = get_system_state_manager(bus=self.event_bus)

    async def initialize(self) -> None:
        """Invoked when agent is activated."""
        self._state_mgr.register_agent(self.name)
        self._state_mgr.register_agent(self.agent_id)
        try:
            await self.event_bus.publish(
                Event(
                    event_type="agent.activated",
                    source=f"agent.{self.agent_id}",
                    severity=EventSeverity.INFO,
                    payload={
                        "agent_id": self.agent_id,
                        "name": self.name,
                        "responsibility": self.responsibility,
                    },
                )
            )
        except Exception:
            pass

    async def shutdown(self) -> None:
        """Invoked when agent stands down."""
        self._state_mgr.unregister_agent(self.name)
        self._state_mgr.unregister_agent(self.agent_id)
        try:
            await self.event_bus.publish(
                Event(
                    event_type="agent.deactivated",
                    source=f"agent.{self.agent_id}",
                    severity=EventSeverity.INFO,
                    payload={"agent_id": self.agent_id, "name": self.name},
                )
            )
        except Exception:
            pass

    @abstractmethod
    async def plan_task(self, objective: Dict[str, Any]) -> List[Task]:
        """Decomposes a high-level objective dictionary into a list of atomic executable Tasks."""
        ...

    @abstractmethod
    async def execute_task(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a leased task using registered tools within authorized bounds."""
        ...

    @abstractmethod
    async def verify_outcome(self, task: Task, output: Dict[str, Any]) -> VerificationEvidence:
        """Independently verifies that the task outcome is genuine and satisfies acceptance criteria."""
        ...
