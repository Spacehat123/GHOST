"""Unit and integration tests for System State and SystemCollector (INTERFACE_CONTRACTS §5)."""

import asyncio
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from ghost.collectors.system_collector import SystemCollector
from ghost.core.events import Event, EventBus, EventSeverity
from ghost.core.system_state import (
    HardwareVitals,
    StateSnapshot,
    SystemStateManager,
)


class TestSystemState(unittest.TestCase):

    def test_hardware_vitals_serialization(self):
        """HardwareVitals dataclass serializes and deserializes accurately."""
        v = HardwareVitals(
            cpu_percent=42.5,
            ram_used_mb=4096.0,
            ram_total_mb=8192.0,
            ram_percent=50.0,
            disk_free_gb=120.5,
            gpu_utilization_percent=15.0,
            gpu_temp_celsius=45.0,
            gpu_vram_used_mb=1200.0,
            gpu_vram_total_mb=4096.0,
        )
        data = v.to_dict()
        self.assertEqual(data["cpu_percent"], 42.5)
        self.assertEqual(data["gpu_vram_used_mb"], 1200.0)

        restored = HardwareVitals.from_dict(data)
        self.assertEqual(restored.cpu_percent, 42.5)
        self.assertEqual(restored.ram_percent, 50.0)
        self.assertEqual(restored.gpu_utilization_percent, 15.0)

    def test_state_snapshot_summary_text(self):
        """StateSnapshot generates clean, formatted summary text for LLM prompts."""
        v = HardwareVitals(
            cpu_percent=12.0,
            ram_used_mb=6000.0,
            ram_total_mb=8000.0,
            ram_percent=75.0,
            disk_free_gb=50.0,
            gpu_utilization_percent=5.0,
            gpu_temp_celsius=40.0,
            gpu_vram_used_mb=1000.0,
            gpu_vram_total_mb=4096.0,
        )
        snap = StateSnapshot(
            timestamp=time.time(),
            vitals=v,
            focused_window="Alacritty - ghost",
            active_workspace=2,
            active_agents=["watchdog", "lead_hunter"],
            running_tasks=2,
            queue_pending=4,
            halt_active=False,
            integrations_healthy={"omniroute": True},
        )
        text = snap.summary_text()
        self.assertIn("GHOST System Status", text)
        self.assertIn("ACTIVE", text)
        self.assertIn("watchdog, lead_hunter", text)
        self.assertIn("2 running, 4 queued", text)
        self.assertIn("Alacritty - ghost", text)
        self.assertIn("GTX 1650", text)

    def test_state_manager_lifecycle_and_updates(self):
        """SystemStateManager manages thread-safe state mutations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_events.db")
            bus = EventBus(db_path=db_path)
            mgr = SystemStateManager(event_bus=bus)

            # Initially healthy and active
            snap0 = mgr.get_snapshot()
            self.assertEqual(snap0.running_tasks, 0)
            self.assertEqual(snap0.active_agents, [])

            # Register agents and task counts
            mgr.register_agent("qa_worker")
            mgr.register_agent("outreach_drafter")
            mgr.update_tasks_count(running=3, pending=10)
            mgr.update_desktop_context(focused_window="VS Code", active_workspace=3)

            snap1 = mgr.get_snapshot()
            self.assertEqual(snap1.active_agents, ["outreach_drafter", "qa_worker"])
            self.assertEqual(snap1.running_tasks, 3)
            self.assertEqual(snap1.queue_pending, 10)
            self.assertEqual(snap1.focused_window, "VS Code")
            self.assertEqual(snap1.active_workspace, 3)

            # Unregister agent
            mgr.unregister_agent("outreach_drafter")
            snap2 = mgr.get_snapshot()
            self.assertEqual(snap2.active_agents, ["qa_worker"])

    def test_state_manager_halt_detection(self):
        """SystemStateManager dynamically detects active halt switch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_events.db")
            bus = EventBus(db_path=db_path)
            mgr = SystemStateManager(event_bus=bus)

            with patch("ghost.core.system_state.is_halted", return_value=True):
                snap = mgr.get_snapshot()
                self.assertTrue(snap.halt_active)
                self.assertIn("⚠️ HALTED", snap.summary_text())

    def test_state_manager_auto_subscribes_to_telemetry(self):
        """SystemStateManager automatically updates vitals upon system.telemetry.sample events."""
        async def _run():
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = os.path.join(tmpdir, "test_events.db")
                bus = EventBus(db_path=db_path)
                mgr = SystemStateManager(event_bus=bus)

                # Publish new telemetry sample event
                v = HardwareVitals(
                    cpu_percent=33.3,
                    ram_used_mb=5120.0,
                    ram_total_mb=8192.0,
                    ram_percent=62.5,
                    disk_free_gb=80.0,
                )
                ev = Event(
                    event_type="system.telemetry.sample",
                    source="collector.system",
                    payload=v.to_dict(),
                )
                await bus.publish(ev)

                # Check manager's snapshot vitals
                snap = mgr.get_snapshot()
                self.assertEqual(snap.vitals.cpu_percent, 33.3)
                self.assertEqual(snap.vitals.ram_percent, 62.5)

        asyncio.run(_run())

    def test_system_collector_sampling_and_alerts(self):
        """SystemCollector samples procfs, detects threshold breach, and emits alert."""
        async def _run():
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = os.path.join(tmpdir, "test_events.db")
                bus = EventBus(db_path=db_path)

                # Collector with low thresholds to trigger test alert
                collector = SystemCollector(
                    event_bus=bus,
                    alert_cpu_threshold=1.0,  # Any CPU > 1% will alert
                    alert_ram_threshold=1.0,  # Any RAM > 1% will alert
                )

                events_received = []
                alerts_received = []

                bus.subscribe("system.telemetry.sample", lambda ev: events_received.append(ev))
                bus.subscribe("system.resource.alert", lambda ev: alerts_received.append(ev))

                # Real hardware sample on host
                sample = collector.sample()
                self.assertGreaterEqual(sample.ram_total_mb, 100.0)
                self.assertGreater(sample.disk_free_gb, 0.0)

                # Sample and publish
                await collector.sample_and_publish()

                self.assertEqual(len(events_received), 1)
                self.assertGreaterEqual(len(alerts_received), 1)
                alert = alerts_received[0]
                self.assertEqual(alert.event_type, "system.resource.alert")
                self.assertIn("alerts", alert.payload)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
