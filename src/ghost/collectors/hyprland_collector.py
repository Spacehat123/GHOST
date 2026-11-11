"""Hyprland Desktop Collector for GHOST Universal Intelligence System.

Conforms to ARCHITECTURE_TARGET.md §Layer 1 & PHASE 4 AWARENESS:
- Connects to Hyprland's IPC socket (.socket2.sock) using asynchronous UNIX socket stream.
- Listens for real-time compositor events (activewindow, workspace, openwindow, closewindow).
- Dispatches desktop.window.focused and desktop.workspace.changed events to EventBus.
- Synchronously updates SystemStateManager desktop context.
- Graceful reconnection on compositor restart; fallback polling via hyprctl if socket is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.core.system_state import SystemStateManager, get_system_state_manager

logger = logging.getLogger("ghost.collectors.hyprland")


class HyprlandCollector:
    """Passively listens to Hyprland socket2 events and tracks focused windows and workspaces."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        state_manager: Optional[SystemStateManager] = None,
        socket_path: Optional[str] = None,
    ):
        self.event_bus = event_bus or get_event_bus()
        self.state_manager = state_manager or get_system_state_manager()
        self._custom_socket_path = socket_path
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_window: Optional[str] = None
        self._last_workspace: Optional[int] = None

    def find_socket2_path(self) -> Optional[Path]:
        """Locates Hyprland .socket2.sock on the host system."""
        if self._custom_socket_path:
            p = Path(self._custom_socket_path)
            if p.is_socket():
                return p

        # 1. Check standard environment variables
        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        his = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if xdg_runtime and his:
            sock = Path(xdg_runtime) / "hypr" / his / ".socket2.sock"
            if sock.is_socket():
                return sock

        # 2. Search XDG_RUNTIME_DIR/hypr/*/
        if xdg_runtime:
            hypr_dir = Path(xdg_runtime) / "hypr"
            if hypr_dir.is_dir():
                for sub in hypr_dir.iterdir():
                    candidate = sub / ".socket2.sock"
                    if candidate.is_socket():
                        return candidate

        # 3. Fallback /run/user/<uid>/hypr/
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        default_dir = Path(f"/run/user/{uid}/hypr")
        if default_dir.is_dir():
            for sub in default_dir.iterdir():
                candidate = sub / ".socket2.sock"
                if candidate.is_socket():
                    return candidate

        return None

    def sample_active_window_sync(self) -> Tuple[Optional[str], Optional[int]]:
        """Fallback synchronous query via hyprctl activewindow -j or xdotool."""
        if shutil.which("hyprctl"):
            try:
                res = subprocess.run(
                    ["hyprctl", "activewindow", "-j"],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                    check=False,
                )
                if res.returncode == 0 and res.stdout.strip():
                    data = json.loads(res.stdout)
                    cls_name = data.get("class", "").strip()
                    title = data.get("title", "").strip()
                    ws_data = data.get("workspace", {})
                    ws_id = ws_data.get("id") if isinstance(ws_data, dict) else None

                    window_label = f"{cls_name}: {title}" if cls_name and title else (cls_name or title or None)
                    return window_label, ws_id
            except Exception as e:
                logger.debug(f"hyprctl activewindow query failed: {e}")

        # Fallback to xdotool
        if shutil.which("xdotool"):
            try:
                res = subprocess.run(
                    ["xdotool", "getwindowfocus", "getwindowname"],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                    check=False,
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip(), 1
            except Exception:
                pass

        return None, None

    async def _handle_activewindow_event(self, raw_data: str) -> None:
        """Processes 'activewindow>>windowclass,windowtitle' event."""
        if "," in raw_data:
            cls_name, title = raw_data.split(",", 1)
        else:
            cls_name, title = raw_data, ""

        cls_name = cls_name.strip()
        title = title.strip()
        label = f"{cls_name}: {title}" if cls_name and title else (cls_name or title or "Desktop")

        if label != self._last_window:
            self._last_window = label
            self.state_manager.update_desktop_context(focused_window=label)
            event = Event(
                event_type="desktop.window.focused",
                source="collector.hyprland",
                severity=EventSeverity.INFO,
                payload={
                    "window_class": cls_name,
                    "window_title": title,
                    "window_label": label,
                },
            )
            await self.event_bus.publish(event)
            logger.debug(f"Hyprland focused window changed: {label}")

    async def _handle_workspace_event(self, raw_data: str) -> None:
        """Processes 'workspace>>workspacename' event."""
        ws_str = raw_data.strip()
        try:
            ws_id = int(ws_str)
        except ValueError:
            ws_id = 1

        if ws_id != self._last_workspace:
            self._last_workspace = ws_id
            self.state_manager.update_desktop_context(active_workspace=ws_id)
            event = Event(
                event_type="desktop.workspace.changed",
                source="collector.hyprland",
                severity=EventSeverity.INFO,
                payload={"workspace_id": ws_id, "workspace_name": ws_str},
            )
            await self.event_bus.publish(event)
            logger.debug(f"Hyprland active workspace changed: {ws_id}")

    async def _parse_socket_line(self, line: str) -> None:
        """Parses a single raw line from socket2.sock stream."""
        if ">>" not in line:
            return
        event_name, data = line.split(">>", 1)
        event_name = event_name.strip()
        data = data.strip()

        if event_name == "activewindow":
            await self._handle_activewindow_event(data)
        elif event_name in ("workspace", "workspacev2"):
            await self._handle_workspace_event(data)
        elif event_name == "openwindow":
            parts = data.split(",", 3)
            if len(parts) >= 4:
                _, _, cls_name, title = parts
                await self.event_bus.publish(
                    Event(
                        event_type="desktop.window.opened",
                        source="collector.hyprland",
                        severity=EventSeverity.DEBUG,
                        payload={"window_class": cls_name, "window_title": title},
                    )
                )

    async def run_loop(self) -> None:
        """Main loop that streams from socket2.sock or falls back to polling."""
        self._running = True
        logger.info("HyprlandCollector starting...")

        # Perform initial sync sample
        try:
            init_win, init_ws = await asyncio.get_running_loop().run_in_executor(
                None, self.sample_active_window_sync
            )
            if init_win:
                self._last_window = init_win
                self.state_manager.update_desktop_context(focused_window=init_win, active_workspace=init_ws)
        except Exception as e:
            logger.debug(f"Initial window sample exception: {e}")

        while self._running:
            sock_path = self.find_socket2_path()
            if sock_path:
                try:
                    logger.info(f"Connecting to Hyprland socket2 at {sock_path}")
                    reader, writer = await asyncio.open_unix_connection(str(sock_path))
                    logger.info("Connected to Hyprland socket2 stream.")

                    while self._running:
                        line_bytes = await reader.readline()
                        if not line_bytes:
                            logger.warning("Hyprland socket2 connection EOF, reconnecting...")
                            break
                        line_str = line_bytes.decode("utf-8", errors="replace").strip()
                        if line_str:
                            await self._parse_socket_line(line_str)

                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"Error reading Hyprland socket2 stream: {e}. Backing off 3s.")
                    await asyncio.sleep(3.0)
            else:
                # Socket not available (e.g. headless or non-Hyprland session), poll periodically
                try:
                    win, ws = await asyncio.get_running_loop().run_in_executor(
                        None, self.sample_active_window_sync
                    )
                    if win and win != self._last_window:
                        await self._handle_activewindow_event(win)
                    if ws and ws != self._last_workspace:
                        await self._handle_workspace_event(str(ws))
                except Exception as e:
                    logger.debug(f"Fallback window poll error: {e}")

                await asyncio.sleep(4.0)

        self._running = False
        logger.info("HyprlandCollector stopped.")

    def start(self) -> asyncio.Task:
        """Starts collector loop as an asyncio Task."""
        if self._task and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run_loop())
        return self._task

    def stop(self) -> None:
        """Stops collector loop and cancels task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
