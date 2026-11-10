"""System State Management for GHOST Universal Intelligence System.

Conforms to INTERFACE_CONTRACTS.md §5:
- HardwareVitals and StateSnapshot immutable models.
- Thread-safe SystemStateManager tracking live hardware, desktop, agent, and task state.
- Real-time halt status inspection (fail-closed integration with ghost.core.halt).
- EventBus integration: auto-updates vitals when system.telemetry.sample is published.
- Prompt-ready context serialization for OmniRoute agent invocations.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

from ghost.core.events import Event, EventBus, get_event_bus
from ghost.core.halt import is_halted

logger = logging.getLogger("ghost.core.system_state")


@dataclass(frozen=True)
class HardwareVitals:
    """Deterministic snapshot of host hardware performance metrics."""
    cpu_percent: float = 0.0
    ram_used_mb: float = 0.0
    ram_total_mb: float = 0.0
    ram_percent: float = 0.0
    disk_free_gb: float = 0.0
    gpu_utilization_percent: Optional[float] = None
    gpu_temp_celsius: Optional[float] = None
    gpu_vram_used_mb: Optional[float] = None
    gpu_vram_total_mb: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HardwareVitals":
        return cls(
            cpu_percent=float(data.get("cpu_percent", 0.0)),
            ram_used_mb=float(data.get("ram_used_mb", 0.0)),
            ram_total_mb=float(data.get("ram_total_mb", 0.0)),
            ram_percent=float(data.get("ram_percent", 0.0)),
            disk_free_gb=float(data.get("disk_free_gb", 0.0)),
            gpu_utilization_percent=data.get("gpu_utilization_percent"),
            gpu_temp_celsius=data.get("gpu_temp_celsius"),
            gpu_vram_used_mb=data.get("gpu_vram_used_mb"),
            gpu_vram_total_mb=data.get("gpu_vram_total_mb"),
        )


@dataclass(frozen=True)
class StateSnapshot:
    """Full unified system state snapshot across perception, execution, and health."""
    timestamp: float
    vitals: HardwareVitals
    focused_window: Optional[str] = None
    active_workspace: Optional[int] = None
    active_agents: List[str] = field(default_factory=list)
    running_tasks: int = 0
    queue_pending: int = 0
    halt_active: bool = False
    integrations_healthy: Dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["vitals"] = self.vitals.to_dict()
        return d

    def summary_text(self) -> str:
        """Condensed status summary formatted for LLM system prompts."""
        gpu_str = "N/A"
        if self.vitals.gpu_utilization_percent is not None:
            gpu_str = (
                f"{self.vitals.gpu_utilization_percent}% "
                f"({self.vitals.gpu_vram_used_mb or 0:.0f}/{self.vitals.gpu_vram_total_mb or 0:.0f} MB VRAM, "
                f"{self.vitals.gpu_temp_celsius or 0:.0f}°C)"
            )

        halt_str = "⚠️ HALTED" if self.halt_active else "🟢 ACTIVE"
        active_str = ", ".join(self.active_agents) if self.active_agents else "None"

        return (
            f"[GHOST System Status - {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))}]\n"
            f"• State: {halt_str} | Active Agents: [{active_str}]\n"
            f"• Tasks: {self.running_tasks} running, {self.queue_pending} queued\n"
            f"• CPU: {self.vitals.cpu_percent:.1f}% | RAM: {self.vitals.ram_percent:.1f}% "
            f"({self.vitals.ram_used_mb:.0f}/{self.vitals.ram_total_mb:.0f} MB) | Disk Free: {self.vitals.disk_free_gb:.1f} GB\n"
            f"• GPU (GTX 1650): {gpu_str}\n"
            f"• Desktop: Window='{self.focused_window or 'N/A'}' (Workspace {self.active_workspace or 1})"
        )


class SystemStateManager:
    """Thread-safe state manager maintaining live environment vitals and telemetry."""

    def __init__(self, event_bus: Optional[EventBus] = None):
        self._lock = threading.Lock()
        self._event_bus = event_bus or get_event_bus()

        self._vitals: HardwareVitals = HardwareVitals()
        self._focused_window: Optional[str] = None
        self._active_workspace: Optional[int] = 1
        self._active_agents: Set[str] = set()
        self._running_tasks: int = 0
        self._queue_pending: int = 0
        self._integrations_healthy: Dict[str, bool] = {
            "omniroute": True,
            "sqlite_wal": True,
            "display": True,
        }

        # Subscribe to EventBus telemetry events
        self._sub_id = self._event_bus.subscribe(
            "system.telemetry.sample",
            self._on_telemetry_event,
        )

    def _on_telemetry_event(self, event: Event) -> None:
        """Handle incoming telemetry events from SystemCollector."""
        try:
            vitals = HardwareVitals.from_dict(event.payload)
            self.set_vitals(vitals)
        except Exception as e:
            logger.warning(f"Failed to parse telemetry event payload: {e}")

    def set_vitals(self, vitals: HardwareVitals) -> None:
        with self._lock:
            self._vitals = vitals

    def update_desktop_context(
        self,
        focused_window: Optional[str] = None,
        active_workspace: Optional[int] = None,
    ) -> None:
        with self._lock:
            if focused_window is not None:
                self._focused_window = focused_window
            if active_workspace is not None:
                self._active_workspace = active_workspace

    def register_agent(self, agent_name: str) -> None:
        with self._lock:
            self._active_agents.add(agent_name)

    def unregister_agent(self, agent_name: str) -> None:
        with self._lock:
            self._active_agents.discard(agent_name)

    def update_tasks_count(self, running: int, pending: int) -> None:
        with self._lock:
            self._running_tasks = max(0, running)
            self._queue_pending = max(0, pending)

    def set_integration_health(self, integration: str, healthy: bool) -> None:
        with self._lock:
            self._integrations_healthy[integration] = healthy

    def get_snapshot(self) -> StateSnapshot:
        """Produces an immutable point-in-time snapshot of the system state."""
        with self._lock:
            vitals_copy = self._vitals
            window_copy = self._focused_window
            workspace_copy = self._active_workspace
            agents_copy = sorted(list(self._active_agents))
            running_copy = self._running_tasks
            pending_copy = self._queue_pending
            integrations_copy = dict(self._integrations_healthy)

        return StateSnapshot(
            timestamp=time.time(),
            vitals=vitals_copy,
            focused_window=window_copy,
            active_workspace=workspace_copy,
            active_agents=agents_copy,
            running_tasks=running_copy,
            queue_pending=pending_copy,
            halt_active=is_halted(),
            integrations_healthy=integrations_copy,
        )

    def get_context_for_prompt(self) -> str:
        """Returns string representation for LLM context injection."""
        return self.get_snapshot().summary_text()


# Global Singleton SystemStateManager Instance
_GLOBAL_STATE_MANAGER: Optional[SystemStateManager] = None
_STATE_LOCK = threading.Lock()


def get_system_state_manager(bus: Optional[EventBus] = None) -> SystemStateManager:
    """Returns the process-wide global SystemStateManager instance."""
    global _GLOBAL_STATE_MANAGER
    if _GLOBAL_STATE_MANAGER is None:
        with _STATE_LOCK:
            if _GLOBAL_STATE_MANAGER is None:
                _GLOBAL_STATE_MANAGER = SystemStateManager(event_bus=bus)
    return _GLOBAL_STATE_MANAGER


def reset_system_state_manager(mgr: Optional[SystemStateManager] = None) -> None:
    """Testing helper to reset or inject custom SystemStateManager instance."""
    global _GLOBAL_STATE_MANAGER
    with _STATE_LOCK:
        _GLOBAL_STATE_MANAGER = mgr
