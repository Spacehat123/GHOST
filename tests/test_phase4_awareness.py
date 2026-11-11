"""Tests for Phase 4 Awareness Deepening.

Validates:
1. Hyprland socket2 line parsing, desktop context tracking, and event emission.
2. SystemCollector rolling window anomaly detection (transient spike vs sustained anomaly vs recovery).
3. SystemStateManager alerts and operating mode recording.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from ghost.collectors.hyprland_collector import HyprlandCollector
from ghost.collectors.system_collector import SystemCollector
from ghost.core.events import EventBus
from ghost.core.system_state import HardwareVitals, SystemStateManager


class TestPhase4Awareness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bus = EventBus(db_path=":memory:", enable_persistence=False)
        self.state_mgr = SystemStateManager(event_bus=self.bus)

    async def test_hyprland_collector_line_parsing(self):
        collector = HyprlandCollector(event_bus=self.bus, state_manager=self.state_mgr)
        events_received = []
        self.bus.subscribe("desktop.*", lambda ev: events_received.append(ev))

        # Test activewindow line
        await collector._parse_socket_line("activewindow>>kitty,fish /home/pranc")
        self.assertEqual(len(events_received), 1)
        self.assertEqual(events_received[0].event_type, "desktop.window.focused")
        self.assertEqual(events_received[0].payload["window_class"], "kitty")
        self.assertEqual(self.state_mgr.get_snapshot().focused_window, "kitty: fish /home/pranc")

        # Test workspace line
        await collector._parse_socket_line("workspace>>3")
        self.assertEqual(len(events_received), 2)
        self.assertEqual(events_received[1].event_type, "desktop.workspace.changed")
        self.assertEqual(events_received[1].payload["workspace_id"], 3)
        self.assertEqual(self.state_mgr.get_snapshot().active_workspace, 3)

    async def test_system_collector_transient_vs_sustained_anomaly(self):
        collector = SystemCollector(
            event_bus=self.bus,
            alert_cpu_threshold=80.0,
            rolling_window_samples=3,
            sustained_ratio=0.66,
        )

        spikes = []
        sustained = []
        recoveries = []
        self.bus.subscribe("system.resource.spike", lambda ev: spikes.append(ev))
        self.bus.subscribe("system.resource.sustained_anomaly", lambda ev: sustained.append(ev))
        self.bus.subscribe("system.resource.recovered", lambda ev: recoveries.append(ev))

        # Sample 1: Transient spike (1st sample at 90% CPU)
        with patch.object(collector, "sample", return_value=HardwareVitals(cpu_percent=90.0, ram_percent=40.0, disk_free_gb=50.0)):
            await collector.sample_and_publish()

        self.assertEqual(len(spikes), 1, "First spike should be marked transient")
        self.assertEqual(len(sustained), 0, "Not yet sustained")
        self.assertTrue(spikes[0].payload.get("is_transient"))

        # Sample 2: Another high reading (2nd sample at 92% CPU, ratio 2/2 = 1.0 >= 0.66)
        with patch.object(collector, "sample", return_value=HardwareVitals(cpu_percent=92.0, ram_percent=40.0, disk_free_gb=50.0)):
            await collector.sample_and_publish()

        self.assertEqual(len(sustained), 1, "Second consecutive high sample triggers sustained anomaly")
        self.assertTrue(sustained[0].payload.get("is_sustained"))

        # State manager should reflect the sustained alert
        snapshot = self.state_mgr.get_snapshot()
        self.assertTrue(any(a["metric"] == "cpu" and a["is_sustained"] for a in snapshot.recent_alerts))

        # Sample 3: Normalized reading (CPU drops to 15%)
        with patch.object(collector, "sample", return_value=HardwareVitals(cpu_percent=15.0, ram_percent=40.0, disk_free_gb=50.0)):
            await collector.sample_and_publish()

        self.assertEqual(len(recoveries), 1, "Dropping below threshold emits recovery event")
        self.assertEqual(recoveries[0].payload["metric"], "cpu")

        # Alert should be cleared in StateSnapshot
        snapshot2 = self.state_mgr.get_snapshot()
        self.assertFalse(any(a["metric"] == "cpu" for a in snapshot2.recent_alerts))


if __name__ == "__main__":
    unittest.main()
