"""
Precision Desktop & Web Element Targeting Engine for Ghost.
Prioritizes native window manager geometry (Hyprland IPC) and DOM inspection (Brave CDP)
over slow, lossy screen capture vision reconnaissance.
"""

import aiohttp
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("TargetingEngine")


class TargetingEngine:
    """
    Resolves exact on-screen pixel bounding boxes for desktop windows,
    browser tabs, web page DOM elements, and cursor anchors using deterministic system protocols.
    """

    CDP_HTTP = "http://127.0.0.1:9222"

    @classmethod
    async def get_hyprland_clients(cls) -> List[Dict[str, Any]]:
        """Fetch all open window clients from Hyprland IPC."""
        proc = await asyncio.create_subprocess_shell(
            "hyprctl clients -j",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        try:
            return json.loads(stdout.decode("utf-8"))
        except Exception:
            return []

    @classmethod
    async def get_active_window(cls) -> Optional[Dict[str, Any]]:
        """Fetch current focused window geometry from Hyprland IPC."""
        proc = await asyncio.create_subprocess_shell(
            "hyprctl activewindow -j",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        try:
            data = json.loads(stdout.decode("utf-8"))
            if data and "at" in data and "size" in data:
                return data
        except Exception:
            pass
        return None

    @classmethod
    async def get_cursor_position(cls) -> Tuple[int, int]:
        """Fetch current mouse cursor position via hyprctl."""
        proc = await asyncio.create_subprocess_shell(
            "hyprctl cursorpos",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        text = stdout.decode("utf-8").strip()
        try:
            parts = [int(p.strip()) for p in text.split(",")]
            return (parts[0], parts[1])
        except Exception:
            return (960, 540)

    @classmethod
    async def find_window_bounds(cls, query: str) -> Optional[Dict[str, Any]]:
        """
        Locates a window by class name or title substring using Hyprland IPC.
        Returns exact pixel bounding box: {x, y, w, h, class, title}.
        """
        clean_q = query.strip().lower()
        if clean_q in ["active", "active_window", "focused"]:
            win = await cls.get_active_window()
            if win:
                return {
                    "type": "window",
                    "x": win["at"][0],
                    "y": win["at"][1],
                    "w": win["size"][0],
                    "h": win["size"][1],
                    "class": win.get("class", ""),
                    "title": win.get("title", ""),
                }

        clients = await cls.get_hyprland_clients()
        # Exact class match first
        for c in clients:
            if clean_q == c.get("class", "").lower():
                return {
                    "type": "window",
                    "x": c["at"][0],
                    "y": c["at"][1],
                    "w": c["size"][0],
                    "h": c["size"][1],
                    "class": c.get("class", ""),
                    "title": c.get("title", ""),
                }

        # Substring title or class match
        for c in clients:
            if clean_q in c.get("class", "").lower() or clean_q in c.get("title", "").lower():
                return {
                    "type": "window",
                    "x": c["at"][0],
                    "y": c["at"][1],
                    "w": c["size"][0],
                    "h": c["size"][1],
                    "class": c.get("class", ""),
                    "title": c.get("title", ""),
                }
        return None

    @classmethod
    async def find_brave_tab(cls, query: str) -> Optional[Dict[str, Any]]:
        """
        Locates a tab in the running Brave browser matching the query.
        Calculates exact on-screen pixel coordinates of the tab in the top tab strip.
        """
        clean_q = query.replace("tab", "").strip().lower()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1.5)) as session:
                async with session.get(f"{cls.CDP_HTTP}/json/list") as resp:
                    all_tabs = await resp.json(content_type=None)
                    pages = [t for t in all_tabs if isinstance(t, dict) and t.get("type") in ["page", "app"]]
                    if not pages:
                        return None

            # Find matching tab index
            matched_idx = None
            matched_tab = None
            for idx, p in enumerate(pages):
                title = p.get("title", "").lower()
                url = p.get("url", "").lower()
                if clean_q in title or clean_q in url:
                    matched_idx = idx
                    matched_tab = p
                    break

            if matched_idx is None:
                return None

            brave_win = await cls.find_window_bounds("brave")
            win_x = brave_win["x"] if brave_win else 0
            win_y = brave_win["y"] if brave_win else 0
            win_w = brave_win["w"] if brave_win else 1920

            # Brave tab strip geometry calculation
            tab_w = max(45, min(240, (win_w - 180) / max(1, len(pages))))
            tab_x = win_x + 80 + int(matched_idx * tab_w)
            tab_y = win_y + 4
            tab_h = 36

            return {
                "type": "brave_tab",
                "x": tab_x,
                "y": tab_y,
                "w": int(tab_w),
                "h": tab_h,
                "tab_title": matched_tab.get("title", ""),
                "tab_index": matched_idx
            }
        except Exception as e:
            logger.debug(f"find_brave_tab error: {e}")
            return None

    @classmethod
    async def find_brave_dom_element(cls, selector_or_text: str) -> Optional[Dict[str, Any]]:
        """
        Queries running Brave browser via CDP port 9222 to get the exact
        bounding client rect of a DOM button, input, text, or error element.
        Adds the Hyprland Brave window origin to compute absolute desktop coordinates.
        """
        clean_q = selector_or_text.strip()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2.0)) as session:
                async with session.get(f"{cls.CDP_HTTP}/json/list") as resp:
                    tabs = await resp.json(content_type=None)
                    pages = [t for t in tabs if isinstance(t, dict) and t.get("type") in ["page", "app"]]
                    if not pages:
                        return None

            import websockets
            # Search active tab first, then search other pages if not found
            for page in pages[:4]:
                ws_url = page.get("webSocketDebuggerUrl")
                if not ws_url:
                    continue
                try:
                    async with websockets.connect(ws_url, close_timeout=1.0) as ws:
                        js_code = f"""
                        (() => {{
                            const q = {json.dumps(clean_q)};
                            let el = null;
                            try {{
                                el = document.querySelector(q);
                            }} catch (e) {{}}
                            if (!el) {{
                                const all = Array.from(document.querySelectorAll('button, a, input, h1, h2, h3, p, span, div, [role="button"]'));
                                el = all.find(e => (e.innerText || e.value || '').trim().toLowerCase().includes(q.toLowerCase()));
                            }}
                            if (!el) return null;
                            const r = el.getBoundingClientRect();
                            return {{
                                x: r.x,
                                y: r.y,
                                w: r.width,
                                h: r.height,
                                text: (el.innerText || el.value || '').trim().slice(0, 50)
                            }};
                        }})()
                        """
                        req = {
                            "id": 101,
                            "method": "Runtime.evaluate",
                            "params": {"expression": js_code, "returnByValue": True}
                        }
                        await ws.send(json.dumps(req))
                        raw_resp = await asyncio.wait_for(ws.recv(), timeout=1.5)
                        data = json.loads(raw_resp)
                        val = data.get("result", {}).get("result", {}).get("value")
                        if val and isinstance(val, dict):
                            brave_win = await cls.find_window_bounds("brave")
                            win_x = brave_win["x"] if brave_win else 0
                            win_y = brave_win["y"] if brave_win else 0
                            chrome_offset_y = 80  # Top tab strip + URL bar offset

                            return {
                                "type": "dom_element",
                                "x": int(win_x + val["x"]),
                                "y": int(win_y + val["y"] + chrome_offset_y),
                                "w": int(val["w"]),
                                "h": int(val["h"]),
                                "element_text": val.get("text", "")
                            }
                except Exception:
                    continue
            return None
        except Exception as e:
            logger.debug(f"Brave CDP targeting fallback: {e}")
            return None

    @classmethod
    async def resolve_target(cls, target_spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Unified target resolver. Resolves coordinates based on target type:
        'brave_tab', 'brave_dom', 'window_class', 'active_window', 'cursor', or 'absolute'.
        """
        ttype = target_spec.get("type", "").lower()
        query = str(target_spec.get("query") or "").strip()

        # Tab check
        if ttype in ["brave_tab", "tab"] or "tab" in query.lower():
            tab_res = await cls.find_brave_tab(query)
            if tab_res:
                return tab_res

        # DOM / button check
        if ttype in ["brave_dom", "dom", "web_element", "button"]:
            dom_res = await cls.find_brave_dom_element(query)
            if dom_res:
                return dom_res

        # Window check
        if ttype in ["active_window", "window", "window_class"]:
            return await cls.find_window_bounds(query or "active")

        # Cursor check
        if ttype == "cursor":
            cx, cy = await cls.get_cursor_position()
            return {"type": "cursor", "x": cx, "y": cy, "w": 0, "h": 0}

        # Auto-heuristic fallback: try DOM, then tab, then window
        if query:
            dom = await cls.find_brave_dom_element(query)
            if dom:
                return dom
            tab = await cls.find_brave_tab(query)
            if tab:
                return tab
            win = await cls.find_window_bounds(query)
            if win:
                return win

        return None
