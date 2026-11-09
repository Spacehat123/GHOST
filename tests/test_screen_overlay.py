"""
Unit Tests for Ghost Screen Overlay & Targeting Subsystem.
Tests IPC client communication, command dispatch, shape specs, and targeting resolution.
"""

import asyncio
import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ghost.screen.client import ScreenOverlayClient
from ghost.screen.targeting import TargetingEngine


class TestScreenOverlay(unittest.IsolatedAsyncioTestCase):

    async def test_ipc_ping_and_draw(self):
        """Test sending draw and ping commands over IPC to overlay daemon."""
        is_running = await ScreenOverlayClient.ensure_daemon_running()
        self.assertTrue(is_running, "Overlay daemon should be running")

        ping_res = await ScreenOverlayClient.send_command({"cmd": "ping"})
        self.assertEqual(ping_res.get("status"), "ok")
        self.assertEqual(ping_res.get("message"), "pong")

        draw_res = await ScreenOverlayClient.draw([
            {
                "type": "circle",
                "coords": [200, 200, 30],
                "color": "#00FF66",
                "width": 3.0,
                "duration": 0.5
            }
        ])
        self.assertEqual(draw_res.get("status"), "ok")
        self.assertIn("created_ids", draw_res)
        self.assertEqual(len(draw_res["created_ids"]), 1)

    async def test_undo_redo_clear(self):
        """Test canvas state transitions: draw -> undo -> redo -> clear."""
        await ScreenOverlayClient.clear()

        # Draw two elements
        d_res = await ScreenOverlayClient.draw([
            {"type": "rect", "coords": [10, 10, 100, 50], "color": "#0099FF"},
            {"type": "arrow", "coords": [50, 50, 150, 150], "color": "#FF9900"}
        ])
        self.assertEqual(d_res.get("status"), "ok")
        self.assertEqual(len(d_res["created_ids"]), 2)

        # Undo
        u_res = await ScreenOverlayClient.undo()
        self.assertEqual(u_res.get("status"), "ok")
        self.assertEqual(u_res.get("active"), 0)

        # Redo
        r_res = await ScreenOverlayClient.redo()
        self.assertEqual(r_res.get("status"), "ok")
        self.assertEqual(r_res.get("active"), 2)

        # Clear
        c_res = await ScreenOverlayClient.clear()
        self.assertEqual(c_res.get("status"), "ok")
        self.assertEqual(c_res.get("count"), 0)

    async def test_targeting_engine_cursor_and_active_window(self):
        """Test targeting engine cursor and active window resolution."""
        cursor = await TargetingEngine.get_cursor_position()
        self.assertIsInstance(cursor, tuple)
        self.assertEqual(len(cursor), 2)

        active = await TargetingEngine.get_active_window()
        if active:
            self.assertIn("at", active)
            self.assertIn("size", active)

        # Test resolve_target
        target_res = await TargetingEngine.resolve_target({"type": "cursor"})
        self.assertIsNotNone(target_res)
        self.assertEqual(target_res["type"], "cursor")


if __name__ == "__main__":
    unittest.main()
