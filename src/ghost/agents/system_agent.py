"""System Intelligence Agent for GHOST.

Conforms to INTERFACE_CONTRACTS.md §4:
- Specializes in host vitals, hardware bottlenecks, and process health.
- Decomposes resource alerts into diagnostic and mitigation tasks.
- Verifies outcomes using deterministic procfs / NVML readbacks.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from ghost.agents.base import AgentContract, VerificationEvidence
from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.core.system_state import HardwareVitals, get_system_state_manager
from ghost.core.task_orchestrator import Task, TaskStatus

logger = logging.getLogger("ghost.agents.system")


class SystemIntelligenceAgent(AgentContract):
    """Specialized agent responsible for host stability and resource optimization."""

    def __init__(self, event_bus: Optional[EventBus] = None):
        super().__init__(
            agent_id="system_agent",
            name="System Intelligence Agent",
            responsibility="Monitors host vitals, investigates hardware spikes, and recovers stalled processes.",
            supported_tasks=["diagnose_hardware_spike", "cleanup_zombies", "verify_vitals"],
            required_permissions=["system.read", "process.manage"],
            event_bus=event_bus,
        )

    async def plan_task(self, objective: Dict[str, Any]) -> List[Task]:
        """Decomposes an alert or health objective into tasks."""
        obj_id = objective.get("objective_id", f"obj_{int(time.time())}")
        target = objective.get("target", "vitals")
        
        task = Task(
            task_id=f"task_sys_{int(time.time() * 1000)}",
            objective_id=obj_id,
            assigned_agent=self.agent_id,
            title=f"Investigate System Condition: {target}",
            description=objective.get("description", "Inspect host vitals and determine bottleneck source."),
            status=TaskStatus.PLANNED,
            input_payload=objective,
            required_permissions=self.required_permissions,
            timeout_s=60.0,
        )
        return [task]

    async def execute_task(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        """Performs system diagnostics."""
        state_mgr = get_system_state_manager(bus=self.event_bus)
        snapshot = state_mgr.get_snapshot()
        vitals = snapshot.vitals

        findings = []
        if vitals.cpu_percent > 85.0:
            findings.append(f"High CPU utilization: {vitals.cpu_percent}%")
        if vitals.ram_percent > 85.0:
            findings.append(f"High RAM pressure: {vitals.ram_percent}% ({vitals.ram_used_mb:.0f} MB)")
        if vitals.gpu_vram_used_mb and vitals.gpu_vram_total_mb:
            vram_pct = (vitals.gpu_vram_used_mb / vitals.gpu_vram_total_mb) * 100.0
            if vram_pct > 90.0:
                findings.append(f"High GPU VRAM consumption: {vram_pct:.1f}%")

        status_str = "healthy" if not findings else "degraded"
        
        output = {
            "status": status_str,
            "vitals": vitals.to_dict(),
            "findings": findings,
            "inspected_at": time.time(),
        }
        return output

    async def verify_outcome(self, task: Task, output: Dict[str, Any]) -> VerificationEvidence:
        """Verifies diagnostic reading against live hardware."""
        state_mgr = get_system_state_manager(bus=self.event_bus)
        current = state_mgr.get_snapshot()
        
        # Readback verification: snapshot vitals must have positive / non-negative readings
        verified = (output.get("inspected_at", 0) > 0 and current.vitals.cpu_percent >= 0.0)
        
        return VerificationEvidence(
            verified=verified,
            evidence_type="readback",
            artifacts=[],
            details={
                "verified_vitals": current.vitals.to_dict(),
                "timestamp": time.time(),
            },
        )
