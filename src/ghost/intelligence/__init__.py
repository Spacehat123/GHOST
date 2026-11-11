"""GHOST Proactive Intelligence Subsystem.

Conforms to ARCHITECTURE_TARGET.md §Layer 4:
- EventPriorityEngine: Continuous event evaluation, importance/urgency/relevance scoring.
- ObjectiveManager: Persistent goal and criteria tracking.
- ProactiveOrchestrator: Autonomous task creation and investigation workflows.
"""

from ghost.intelligence.objective_manager import (
    Objective,
    ObjectiveManager,
    get_objective_manager,
)
from ghost.intelligence.priority_engine import (
    EventPriorityEngine,
    PrioritizationDecision,
)
from ghost.intelligence.proactive_orchestrator import (
    ProactiveOrchestrator,
    get_proactive_orchestrator,
)

__all__ = [
    "Objective",
    "ObjectiveManager",
    "get_objective_manager",
    "EventPriorityEngine",
    "PrioritizationDecision",
    "ProactiveOrchestrator",
    "get_proactive_orchestrator",
]
