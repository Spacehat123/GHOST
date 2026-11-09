"""
Async IPC Client for Ghost Screen Overlay Daemon.
Connects over /tmp/ghost_screen_overlay.sock and automatically
spawns the daemon subprocess if not currently running.
"""

import asyncio
import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

SOCKET_PATH = "/tmp/ghost_screen_overlay.sock"
PID_FILE = "/tmp/ghost_screen_overlay.pid"
VENV_PYTHON = "/home/pranc/.venvs/ghost-gpu/bin/python"


class ScreenOverlayClient:
    """Async client communicating with the Wayland GtkLayerShell overlay daemon."""

    @classmethod
    async def ensure_daemon_running(cls, max_wait_sec: float = 2.0) -> bool:
        """Verifies daemon is responsive on IPC socket, spawning if necessary."""
        if os.path.exists(SOCKET_PATH):
            try:
                pong = await cls.send_command({"cmd": "ping"}, timeout=0.4)
                if pong.get("status") == "ok":
                    return True
            except Exception:
                pass

        # Socket not responsive or missing, launch daemon
        env = os.environ.copy()
        env["PYTHONPATH"] = "/home/pranc/projects/ghost/src"
        python_bin = VENV_PYTHON if os.path.exists(VENV_PYTHON) else "python3"

        subprocess.Popen(
            [python_bin, "-m", "ghost.screen.overlay"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setpgrp,
            env=env
        )

        start_t = time.time()
        while time.time() - start_t < max_wait_sec:
            await asyncio.sleep(0.08)
            if os.path.exists(SOCKET_PATH):
                try:
                    pong = await cls.send_command({"cmd": "ping"}, timeout=0.3)
                    if pong.get("status") == "ok":
                        return True
                except Exception:
                    pass

        return False

    @classmethod
    async def send_command(cls, payload: Dict[str, Any], timeout: float = 2.0) -> Dict[str, Any]:
        """Send a JSON command line to the overlay socket and return JSON response."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(SOCKET_PATH),
            timeout=timeout
        )
        try:
            cmd_bytes = (json.dumps(payload) + "\n").encode("utf-8")
            writer.write(cmd_bytes)
            await writer.drain()

            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not line:
                return {"status": "error", "message": "Overlay daemon closed socket"}
            return json.loads(line.decode("utf-8").strip())
        finally:
            writer.close()
            await writer.wait_closed()

    @classmethod
    async def draw(cls, elements: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Draw one or more vector elements on screen overlay."""
        await cls.ensure_daemon_running()
        return await cls.send_command({"cmd": "draw", "elements": elements})

    @classmethod
    async def clear(cls) -> Dict[str, Any]:
        """Clear all active elements on screen overlay."""
        await cls.ensure_daemon_running()
        return await cls.send_command({"cmd": "clear"})

    @classmethod
    async def undo(cls) -> Dict[str, Any]:
        """Undo last drawing operation."""
        await cls.ensure_daemon_running()
        return await cls.send_command({"cmd": "undo"})

    @classmethod
    async def redo(cls) -> Dict[str, Any]:
        """Redo last undone operation."""
        await cls.ensure_daemon_running()
        return await cls.send_command({"cmd": "redo"})

    @classmethod
    async def erase(cls, element_id: str) -> Dict[str, Any]:
        """Erase specific element by ID."""
        await cls.ensure_daemon_running()
        return await cls.send_command({"cmd": "erase", "id": element_id})

    @classmethod
    async def list_elements(cls) -> Dict[str, Any]:
        """List all active elements."""
        await cls.ensure_daemon_running()
        return await cls.send_command({"cmd": "list"})
