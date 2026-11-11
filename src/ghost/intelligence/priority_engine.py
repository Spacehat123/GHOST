"""Event Priority and Proactive Decision Engine for GHOST.

Conforms to ARCHITECTURE_TARGET.md §Layer 4 & PHASE 5 PROACTIVE INTELLIGENCE:
- Continuous event evaluation across all seven intelligence domains.
- Deterministic calculation of Importance, Urgency, and Objective Relevance scores.
- Duplicate event suppression within rolling time windows.
- Context-aware prioritization reflecting current system operating mode:
  1. AMBIENT: Non-intrusive observation, passive summarization.
  2. OBJECTIVE: Autonomous pursuit of defined operator objectives.
  3. REACTIVE: Fast-path response and automated investigation of anomalies.
- Emits structured PrioritizationDecision with auditable evidence.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ghost.core.events import Event, EventSeverity
from ghost.intelligence.objective_manager import Objective, ObjectiveManager, get_objective_manager

logger = logging.getLogger("ghost.intelligence.priority")


@dataclass(frozen=True)
class PrioritizationDecision:
    """Auditable evidence record for why an event was prioritized and what action was taken."""
    event_id: str
    event_type: str
    operating_mode: str
    priority_score: float  # 1.0 to 10.0
    importance: float      # 1.0 to 10.0
    urgency: float         # 1.0 to 10.0
    relevance: float       # 0.0 to 1.0
    action_recommended: str  # INVESTIGATE, CREATE_OBJECTIVE_TASK, LOG_AMBIENT, SUPPRESS_DUPLICATE
    assigned_domain: str
    assigned_agent: str
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EventPriorityEngine:
    """Evaluates incoming events to determine importance, urgency, and proactive workflows."""

    def __init__(
        self,
        objective_manager: Optional[ObjectiveManager] = None,
        suppression_window_s: float = 60.0,
    ):
        self.objective_manager = objective_manager or get_objective_manager()
        self.suppression_window_s = suppression_window_s
        self._seen_fingerprints: Dict[str, float] = {}  # hash -> timestamp

    def _generate_fingerprint(self, event: Event) -> str:
        """Computes deduplication hash for an event."""
        if event.dedup_id:
            return f"dedup:{event.dedup_id}"
        # Fingerprint based on type, source, and sorted key elements of payload
        payload_key = json.dumps(event.payload, sort_keys=True, default=str)[:300]
        raw = f"{event.event_type}:{event.source}:{payload_key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def is_duplicate(self, event: Event) -> bool:
        """Determines if this event is a duplicate within the rolling suppression window."""
        now = time.time()
        # Clean expired fingerprints
        cutoff = now - self.suppression_window_s
        self._seen_fingerprints = {k: ts for k, ts in self._seen_fingerprints.items() if ts > cutoff}

        fp = self._generate_fingerprint(event)
        if fp in self._seen_fingerprints:
            return True
        self._seen_fingerprints[fp] = now
        return False

    def calculate_importance(self, event: Event) -> float:
        """Calculates importance (1.0 to 10.0) based on severity and operational impact."""
        severity_map = {
            EventSeverity.DEBUG: 2.0,
            EventSeverity.INFO: 4.0,
            EventSeverity.WARNING: 7.0,
            EventSeverity.ERROR: 8.5,
            EventSeverity.CRITICAL: 10.0,
        }
        score = severity_map.get(event.severity, 4.0)

        # Domain-specific adjustments
        if "sustained_anomaly" in event.event_type:
            score = max(score, 8.5)
        elif "test.failure" in event.event_type:
            score = max(score, 8.0)
        elif "lead.qualified" in event.event_type:
            score = max(score, 7.5)
        elif "telemetry.sample" in event.event_type:
            score = 2.0  # Periodic heartbeats/telemetry are routine

        return round(min(10.0, max(1.0, score)), 1)

    def calculate_urgency(self, event: Event) -> float:
        """Calculates urgency (1.0 to 10.0) based on time sensitivity."""
        if event.severity == EventSeverity.CRITICAL:
            return 10.0
        if "sustained_anomaly" in event.event_type or "resource.alert" in event.event_type:
            return 8.5
        if "test.failure" in event.event_type:
            return 8.0
        if "client" in event.event_type or "inquiry" in event.event_type:
            return 7.5
        if event.severity == EventSeverity.WARNING:
            return 6.5
        return 3.0

    def match_objective_relevance(self, event: Event, objectives: List[Objective]) -> Tuple[float, Optional[Objective]]:
        """Calculates relevance score (0.0 to 1.0) against active objectives."""
        if not objectives:
            return 0.0, None

        best_score = 0.0
        best_obj: Optional[Objective] = None

        event_str = f"{event.event_type} {event.source} {json.dumps(event.payload)}".lower()

        for obj in objectives:
            if obj.status != "ACTIVE":
                continue

            score = 0.0
            # Direct domain match
            if obj.domain in event.event_type:
                score += 0.4

            # Title or criteria match
            if obj.title.lower() in event_str:
                score += 0.4
            for crit in obj.criteria:
                if crit.lower() in event_str:
                    score += 0.2

            score = min(1.0, score)
            if score > best_score:
                best_score = score
                best_obj = obj

        return round(best_score, 2), best_obj

    def determine_agent_and_domain(self, event: Event, matched_objective: Optional[Objective]) -> Tuple[str, str]:
        """Resolves target agent and domain taxonomy from event structure."""
        if matched_objective:
            return matched_objective.domain, matched_objective.target_agent

        etype = event.event_type
        if etype.startswith("system."):
            return "system", "system_agent"
        if etype.startswith("dev."):
            return "dev", "dev_agent"
        if etype.startswith("research."):
            return "research", "research_agent"
        if etype.startswith("business.") or etype.startswith("pipeline."):
            return "business", "business_agent"
        if etype.startswith("personal."):
            return "personal", "personal_agent"
        if etype.startswith("physical.") or etype.startswith("smart_home."):
            return "physical", "physical_agent"
        if etype.startswith("creative."):
            return "creative", "creative_agent"
        return "system", "system_agent"

    def evaluate(self, event: Event, operating_mode: str = "AMBIENT") -> PrioritizationDecision:
        """Evaluates an event and determines whether proactive action is recommended."""
        mode = operating_mode.upper()
        if mode not in ("AMBIENT", "OBJECTIVE", "REACTIVE"):
            mode = "AMBIENT"

        # Check duplicate suppression
        if self.is_duplicate(event):
            return PrioritizationDecision(
                event_id=event.event_id,
                event_type=event.event_type,
                operating_mode=mode,
                priority_score=1.0,
                importance=1.0,
                urgency=1.0,
                relevance=0.0,
                action_recommended="SUPPRESS_DUPLICATE",
                assigned_domain="system",
                assigned_agent="system_agent",
                reason=f"Event suppressed: duplicate signal received within {self.suppression_window_s}s window.",
                evidence={"dedup": True},
            )

        importance = self.calculate_importance(event)
        urgency = self.calculate_urgency(event)

        active_objs = self.objective_manager.list_objectives(status="ACTIVE")
        relevance, matched_obj = self.match_objective_relevance(event, active_objs)

        domain, agent = self.determine_agent_and_domain(event, matched_obj)

        # Composite priority formula: (Importance * 0.4) + (Urgency * 0.4) + (Relevance * 2.0)
        raw_priority = (importance * 0.4) + (urgency * 0.4) + (relevance * 2.0)
        priority_score = round(min(10.0, max(1.0, raw_priority)), 1)

        # Determine recommended action based on operating mode
        action = "LOG_AMBIENT"
        reason = f"Event recorded under {mode} mode. Routine observation."
        evidence = {
            "importance": importance,
            "urgency": urgency,
            "relevance": relevance,
            "matched_objective_id": matched_obj.objective_id if matched_obj else None,
        }

        if mode == "REACTIVE":
            # Reactive responds to any anomaly or warning/critical
            if (
                event.severity in (EventSeverity.WARNING, EventSeverity.CRITICAL)
                or "anomaly" in event.event_type
                or "failure" in event.event_type
                or priority_score >= 6.5
            ):
                action = "INVESTIGATE"
                reason = (
                    f"Reactive Mode triggered investigation: Priority {priority_score}/10 "
                    f"(Importance={importance}, Urgency={urgency}). Event '{event.event_type}' requires resolution."
                )
        elif mode == "OBJECTIVE":
            # Objective mode acts on relevant tasks
            if matched_obj and relevance >= 0.4:
                action = "CREATE_OBJECTIVE_TASK"
                reason = (
                    f"Objective Mode triggered task creation: Event matches objective '{matched_obj.title}' "
                    f"with relevance {relevance:.2f}."
                )
            elif event.severity == EventSeverity.CRITICAL:
                action = "INVESTIGATE"
                reason = f"Critical failure '{event.event_type}' requires immediate mitigation regardless of objective."
        elif mode == "AMBIENT":
            # Ambient only elevates on critical emergencies
            if event.severity == EventSeverity.CRITICAL:
                action = "INVESTIGATE"
                reason = f"Ambient Mode exception: Critical severity event '{event.event_type}' requires intervention."

        return PrioritizationDecision(
            event_id=event.event_id,
            event_type=event.event_type,
            operating_mode=mode,
            priority_score=priority_score,
            importance=importance,
            urgency=urgency,
            relevance=relevance,
            action_recommended=action,
            assigned_domain=domain,
            assigned_agent=agent,
            reason=reason,
            evidence=evidence,
        )
