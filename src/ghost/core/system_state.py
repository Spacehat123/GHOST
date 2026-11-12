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
    recent_alerts: List[Dict[str, Any]] = field(default_factory=list)
    operating_mode: str = "AMBIENT"

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
        alerts_str = f" | Alerts: {len(self.recent_alerts)}" if self.recent_alerts else ""

        return (
            f"[GHOST System Status - {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))}]\n"
            f"• Mode: {self.operating_mode} | State: {halt_str} | Active Agents: [{active_str}]{alerts_str}\n"
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
        self._operating_mode: str = "AMBIENT"
        self._recent_alerts: Dict[str, Dict[str, Any]] = {}
        self._integrations_healthy: Dict[str, bool] = {
            "omniroute": True,
            "sqlite_wal": True,
            "display": True,
        }

        # Subscribe to EventBus telemetry, resource alert, and agent lifecycle events
        self._sub_id = self._event_bus.subscribe(
            "system.telemetry.sample",
            self._on_telemetry_event,
        )
        self._sub_alert_id = self._event_bus.subscribe(
            "system.resource.*",
            self._on_resource_event,
        )
        self._sub_agent_id = self._event_bus.subscribe(
            "agent.*",
            self._on_agent_lifecycle_event,
        )

    def _on_telemetry_event(self, event: Event) -> None:
        """Handle incoming telemetry events from SystemCollector."""
        try:
            vitals = HardwareVitals.from_dict(event.payload)
            self.set_vitals(vitals)
        except Exception as e:
            logger.warning(f"Failed to parse telemetry event payload: {e}")

    def _on_resource_event(self, event: Event) -> None:
        """Handle resource alerts, sustained anomalies, and recovery events."""
        try:
            if event.event_type in ("system.resource.alert", "system.resource.sustained_anomaly"):
                alerts_dict = event.payload.get("alerts", {})
                if not alerts_dict and "metric" in event.payload:
                    alerts_dict = {event.payload["metric"]: event.payload}
                for metric, detail in alerts_dict.items():
                    detail_is_sustained = (
                        detail.get("is_sustained", False)
                        if isinstance(detail, dict)
                        else False
                    )
                    is_sustained = (
                        event.event_type == "system.resource.sustained_anomaly"
                        or event.payload.get("is_sustained", False)
                        or detail_is_sustained
                    )
                    self.record_alert(
                        metric_name=metric,
                        severity=event.severity.value,
                        details=detail,
                        is_sustained=is_sustained,
                    )
            elif event.event_type == "system.resource.recovered":
                metric = event.payload.get("metric")
                if metric:
                    self.clear_alert(metric)
        except Exception as e:
            logger.warning(f"Failed to process resource event: {e}")

    def _on_agent_lifecycle_event(self, event: Event) -> None:
        """Handle agent activation and deactivation events."""
        try:
            agent_id = event.payload.get("agent_id") or event.payload.get("name")
            if not agent_id:
                return
            if event.event_type == "agent.activated":
                self.register_agent(agent_id)
            elif event.event_type == "agent.deactivated":
                self.unregister_agent(agent_id)
        except Exception as e:
            logger.warning(f"Failed to handle agent lifecycle event: {e}")

    def set_vitals(self, vitals: HardwareVitals) -> None:
        with self._lock:
            self._vitals = vitals

    def set_operating_mode(self, mode: str) -> None:
        mode_upper = mode.upper()
        if mode_upper in ("AMBIENT", "OBJECTIVE", "REACTIVE"):
            with self._lock:
                self._operating_mode = mode_upper
                logger.info(f"System operating mode changed to: {mode_upper}")

    def record_alert(
        self,
        metric_name: str,
        severity: str,
        details: Dict[str, Any],
        is_sustained: bool = False,
    ) -> None:
        with self._lock:
            existing = self._recent_alerts.get(metric_name)
            sustained_flag = is_sustained or (existing.get("is_sustained", False) if existing else False)
            self._recent_alerts[metric_name] = {
                "metric": metric_name,
                "severity": severity,
                "details": details,
                "is_sustained": sustained_flag,
                "timestamp": time.time(),
            }

    def clear_alert(self, metric_name: str) -> None:
        with self._lock:
            self._recent_alerts.pop(metric_name, None)

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._recent_alerts.values())

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
            alerts_copy = list(self._recent_alerts.values())
            mode_copy = self._operating_mode

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
            recent_alerts=alerts_copy,
            operating_mode=mode_copy,
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
