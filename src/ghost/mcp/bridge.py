"""Comprehensive execution bridge for Brave CDP Automation Engine, WhatsApp Web, Media/MPRIS, Desktop Apps, REA, and Shell."""
import asyncio
import os
import json
import base64
import re
import urllib.request
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple, Union
import aiohttp
import websockets

REA_PATH = "/home/pranc/projects/rea"
REA_BIN = os.path.join(REA_PATH, "scripts/rea.mjs")

# Global Tactical Authorization Handler (Human-In-The-Loop)
AUTH_CALLBACK = None

def register_auth_callback(cb):
    global AUTH_CALLBACK
    AUTH_CALLBACK = cb

async def request_tactical_authorization(description: str, action_type: str = "danger") -> bool:
    """
    Request authorization from the user via the Ghost GUI popup.
    Focuses the Ghost window on Hyprland, presents description, and awaits Allow/Deny.
    """
    await SystemAppBridge.hyprctl_dispatch("focuswindow", "class:Brave")
    await SystemAppBridge.notify("Tactical Authorization Required", description)

    if AUTH_CALLBACK:
        try:
            return await AUTH_CALLBACK(description, action_type)
        except Exception as e:
            logger.error(f"Tactical authorization callback failed: {e}")
            return False
    # CRITICAL: Default to False (DENY). Never auto-allow when frontend is disconnected!
    logger.warning("No active GUI WebSocket callback connected for Tactical Authorization. Action DENIED.")
    return False

class ShellExecutor:
    """Executes safe bash/shell commands locally for Ghost."""

    @staticmethod
    async def run(command: str, timeout: float = 30.0) -> Dict[str, Any]:
        """Execute a shell command asynchronously with timeout protection."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=True
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "success": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "command": command
            }
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {
                "success": False,
                "returncode": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds",
                "command": command
            }
        except Exception as e:
            return {
                "success": False,
                "returncode": -1,
                "stdout": "",
                "stderr": str(e),
                "command": command
            }


class CDPAutomationEngine:
    """
    Direct CDP Input Protocol engine connecting to Brave debugging port 9222.
    Dispatches genuine mouse events (Input.dispatchMouseEvent) and keystrokes
    (Input.dispatchKeyEvent), allowing reliable interaction on complex React/SPA apps.
    """

    @staticmethod
    async def send_command(ws, req_id: int, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 8.0) -> Dict[str, Any]:
        payload = {"id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        await ws.send(json.dumps(payload))

        async def _recv_loop():
            while True:
                resp = await ws.recv()
                try:
                    data = json.loads(resp)
                except Exception:
                    continue
                if data.get("id") == req_id:
                    return data

        return await asyncio.wait_for(_recv_loop(), timeout=timeout)

    @classmethod
    async def dispatch_mouse_click(cls, ws, x: float, y: float, click_count: int = 1):
        """Dispatches real mouse move, press, and release events at coordinate (x, y)."""
        await cls.send_command(ws, 1001, "Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y
        })
        await cls.send_command(ws, 1002, "Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": click_count
        })
        await asyncio.sleep(0.04)
        await cls.send_command(ws, 1003, "Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": click_count
        })

    @classmethod
    async def dispatch_type_text(cls, ws, text: str, press_enter: bool = False):
        """Types text character-by-character via Input.dispatchKeyEvent."""
        req_id = 2000
        for char in text:
            req_id += 1
            await cls.send_command(ws, req_id, "Input.dispatchKeyEvent", {
                "type": "keyDown", "text": char
            })
            req_id += 1
            await cls.send_command(ws, req_id, "Input.dispatchKeyEvent", {
                "type": "keyUp", "text": char
            })
            await asyncio.sleep(0.015)

        if press_enter:
            req_id += 1
            await cls.send_command(ws, req_id, "Input.dispatchKeyEvent", {
                "type": "rawKeyDown", "windowsVirtualKeyCode": 13, "unmodifiedText": "\r", "text": "\r"
            })
            req_id += 1
            await cls.send_command(ws, req_id, "Input.dispatchKeyEvent", {
                "type": "keyUp", "windowsVirtualKeyCode": 13, "unmodifiedText": "\r", "text": "\r"
            })

    @classmethod
    async def evaluate_js(cls, ws, expression: str) -> Any:
        """Evaluate JS expression in target context."""
        res = await cls.send_command(ws, 3000, "Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True
        })
        return res.get("result", {}).get("result", {}).get("value")

    @classmethod
    async def find_element_rect(cls, ws, selector_or_text: str) -> Optional[Dict[str, float]]:
        """
        Locates an element's bounding rect by CSS selector, text content, or aria-label.
        Returns {'x': center_x, 'y': center_y, 'width': w, 'height': h} or None.
        """
        script = f"""
        (function() {{
            const query = {json.dumps(selector_or_text)};
            let el = null;

            // 1. Try direct CSS selector
            try {{
                el = document.querySelector(query);
            }} catch(e) {{}}

            // 2. Try aria-label / placeholder / title
            if (!el) {{
                el = document.querySelector('[aria-label*="' + query + '" i]') ||
                     document.querySelector('[placeholder*="' + query + '" i]') ||
                     document.querySelector('[title*="' + query + '" i]');
            }}

            // 3. Try visible text match across interactive elements
            if (!el) {{
                const candidates = Array.from(document.querySelectorAll('button, [role="button"], a, input, div, span'));
                el = candidates.find(c => {{
                    const t = (c.innerText || c.textContent || '').trim().toLowerCase();
                    return t === query.toLowerCase() && (c.getAttribute('role') === 'button' || c.tagName === 'BUTTON' || c.tagName === 'A');
                }}) || candidates.find(c => {{
                    const t = (c.innerText || c.textContent || '').trim().toLowerCase();
                    return t === query.toLowerCase() || (t.length < 40 && t.includes(query.toLowerCase()));
                }});

                if (el) {{
                    const clickableAncestor = el.closest('[role="button"], button, a, [data-testid*="profile"]');
                    if (clickableAncestor) {{
                        el = clickableAncestor;
                    }}
                }}
            }}

            if (!el) return null;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return null;
            return {{
                x: r.x + r.width / 2,
                y: r.y + r.height / 2,
                width: r.width,
                height: r.height
            }};
        }})();
        """
        return await cls.evaluate_js(ws, script)

    @classmethod
    async def click_element(cls, ws, selector_or_text: str) -> bool:
        """Finds element and dispatches mouse click to its exact center."""
        rect = await cls.find_element_rect(ws, selector_or_text)
        if not rect:
            return False
        await cls.dispatch_mouse_click(ws, rect["x"], rect["y"])
        return True


class BraveCDPBridge:
    """Interfaces with running Brave browser instance via Chrome DevTools Protocol (CDP) on port 9222."""

    BASE_URL = "http://127.0.0.1:9222"

    @classmethod
    async def get_version(cls) -> Dict[str, Any]:
        """Get browser details and debugger endpoints."""
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{cls.BASE_URL}/json/version") as resp:
                    return await resp.json(content_type=None)
        except Exception as e:
            return {"error": f"Failed to connect to Brave CDP: {e}"}

    @classmethod
    async def list_tabs(cls) -> List[Dict[str, Any]]:
        """List all active pages and tabs in Brave."""
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{cls.BASE_URL}/json/list") as resp:
                    tabs = await resp.json(content_type=None)
                    if not isinstance(tabs, list):
                        return []
                    return [
                        {
                            "id": t.get("id"),
                            "title": t.get("title"),
                            "url": t.get("url"),
                            "type": t.get("type"),
                            "wsUrl": t.get("webSocketDebuggerUrl")
                        }
                        for t in tabs if isinstance(t, dict) and t.get("type") in ["page", "app"]
                    ]
        except Exception as e:
            return [{"error": f"Failed to list tabs: {e}"}]

    @classmethod
    async def new_tab(cls, url: str = "about:blank") -> Dict[str, Any]:
        """Open a new tab at the specified URL."""
        try:
            encoded = urllib.parse.quote(url)
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.put(f"{cls.BASE_URL}/json/new?{encoded}") as resp:
                    return await resp.json(content_type=None)
        except Exception as e:
            return {"error": f"Failed to open tab: {e}"}

    @classmethod
    async def activate_tab(cls, tab_id_or_match: str) -> Dict[str, Any]:
        """Bring a specific tab to the foreground by ID or search match (title/URL)."""
        try:
            target_id = tab_id_or_match
            # If target looks like a search query rather than a hex ID
            tabs = await cls.list_tabs()
            found = False
            for t in tabs:
                if t.get("id") == tab_id_or_match:
                    target_id = t.get("id")
                    found = True
                    break

            if not found:
                query = tab_id_or_match.lower()
                for t in tabs:
                    url = t.get("url", "").lower()
                    title = t.get("title", "").lower()
                    if query in url or query in title:
                        target_id = t.get("id")
                        found = True
                        break

            timeout = aiohttp.ClientTimeout(total=3)
            clean_id = urllib.parse.quote(str(target_id))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{cls.BASE_URL}/json/activate/{clean_id}") as resp:
                    if resp.status == 200:
                        return {"success": True, "status": resp.status, "activated_tab": target_id}
                    return {"success": False, "status": resp.status, "error": f"Brave returned HTTP {resp.status}"}
        except Exception as e:
            return {"error": f"Failed to activate tab: {e}"}

    @classmethod
    async def close_tab(cls, tab_id_or_match: Optional[str] = None) -> Dict[str, Any]:
        """Close a specific tab by ID or search match (URL or title keyword), or active tab."""
        try:
            tabs = await cls.list_tabs()
            target_id = None
            target_title = None

            if not tab_id_or_match or tab_id_or_match == "active":
                if tabs:
                    target_id = tabs[0].get("id")
                    target_title = tabs[0].get("title")
            else:
                # 1. Exact tab ID match
                for t in tabs:
                    if t.get("id") == tab_id_or_match:
                        target_id = t.get("id")
                        target_title = t.get("title")
                        break
                # 2. Case-insensitive substring match on URL or title
                if not target_id:
                    query = tab_id_or_match.lower()
                    for t in tabs:
                        url = t.get("url", "").lower()
                        title = t.get("title", "").lower()
                        if query in url or query in title:
                            target_id = t.get("id")
                            target_title = t.get("title")
                            break

            if not target_id:
                return {"success": False, "error": f"No tab matching '{tab_id_or_match}' found."}

            timeout = aiohttp.ClientTimeout(total=3)
            clean_id = urllib.parse.quote(str(target_id))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{cls.BASE_URL}/json/close/{clean_id}") as resp:
                    if resp.status == 200:
                        return {"success": True, "status": resp.status, "closed_tab": target_title, "id": target_id}
                    return {"success": False, "status": resp.status, "error": f"Brave returned HTTP {resp.status}"}
        except Exception as e:
            return {"error": f"Failed to close tab: {e}"}

    @classmethod
    async def click_and_type(cls, selector_or_label: str, text: Optional[str] = None, press_enter: bool = False, tab_id: Optional[str] = None) -> Dict[str, Any]:
        """
        General Web Automation: Click an element by CSS or label, optionally type text with real keystrokes.
        """
        tabs = await cls.list_tabs()
        target_tab = None
        if tab_id:
            for t in tabs:
                if t.get("id") == tab_id or tab_id.lower() in t.get("url", "").lower() or tab_id.lower() in t.get("title", "").lower():
                    target_tab = t
                    break
        if not target_tab and tabs:
            # If any tab is a video streaming / hotstar tab, prioritize it over the Ghost HUD tab
            for t in tabs:
                u = t.get("url", "").lower()
                if "hotstar.com" in u or "youtube.com" in u or "netflix.com" in u:
                    target_tab = t
                    break
            if not target_tab:
                # Prioritize non-Ghost tabs
                for t in tabs:
                    if ":8765" not in t.get("url", ""):
                        target_tab = t
                        break
            if not target_tab:
                target_tab = tabs[0]

        if not target_tab or not target_tab.get("wsUrl"):
            return {"success": False, "error": "No debuggable tab available"}

        try:
            async with websockets.connect(target_tab["wsUrl"], close_timeout=3) as ws:
                clicked = await CDPAutomationEngine.click_element(ws, selector_or_label)
                if not clicked:
                    return {"success": False, "error": f"Element '{selector_or_label}' not found on page"}

                if text:
                    await asyncio.sleep(0.08)
                    await CDPAutomationEngine.dispatch_type_text(ws, text, press_enter=press_enter)

                return {"success": True, "tab": target_tab.get("title"), "clicked": selector_or_label, "typed": text}
        except Exception as e:
            return {"success": False, "error": str(e)}


class WhatsAppBridge:
    """Advanced WhatsApp Web automation using direct CDP mouse & keyboard dispatch."""

    @classmethod
    async def find_whatsapp_tab(cls) -> Optional[Dict[str, Any]]:
        tabs = await BraveCDPBridge.list_tabs()
        for t in tabs:
            url = t.get("url", "").lower()
            title = t.get("title", "").lower()
            if "web.whatsapp.com" in url or "whatsapp" in title:
                return t
        return None

    @classmethod
    async def action(cls, action_type: str, target: Optional[str] = None, message_text: Optional[str] = None) -> Dict[str, Any]:
        """
        WhatsApp Web operations via CDP:
        - 'open': ensure web.whatsapp.com tab is active
        - 'status': check if WhatsApp is logged in
        - 'search_contact': search for a person/chat and press Enter
        - 'message': search for contact, open chat, and type message
        - 'call' / 'voice_call': search contact, open chat, and click voice call button
        """
        wa_tab = await cls.find_whatsapp_tab()

        if not wa_tab:
            new_tab = await BraveCDPBridge.new_tab("https://web.whatsapp.com")
            return {
                "status": "opened_whatsapp_web",
                "message": "WhatsApp Web deployed in new tab. Standing by for QR sync, sir."
            }

        # Bring tab to foreground focus
        await BraveCDPBridge.activate_tab(wa_tab["id"])
        ws_url = wa_tab.get("wsUrl")
        if not ws_url:
            return {"status": "error", "message": "WhatsApp tab debugger endpoint unavailable"}

        async with websockets.connect(ws_url, close_timeout=5) as ws:
            # Check login state
            login_check = await CDPAutomationEngine.evaluate_js(ws, '!!document.querySelector("canvas[aria-label*=Scan]") || !!document.querySelector("[data-ref]")')
            if login_check:
                return {
                    "status": "needs_login",
                    "message": "WhatsApp Web is open but requires QR code login, boss."
                }

            if action_type in ["open", "status"]:
                return {
                    "status": "ready",
                    "message": f"WhatsApp Web active and connected on tab '{wa_tab.get('title')}', sir."
                }

            # Locate search box or direct contact item in chat list
            search_rect = await CDPAutomationEngine.find_element_rect(ws, 'input[placeholder*="Search"], input[aria-label*="Search"]')
            if not search_rect:
                search_rect = await CDPAutomationEngine.find_element_rect(ws, 'input')
            if not search_rect:
                search_rect = await CDPAutomationEngine.find_element_rect(ws, 'div[contenteditable="true"][data-tab="3"], div[role="textbox"]')

            if target:
                chat_opened = False

                # 1. First try directly finding the contact name in the visible chat list
                contact_rect = await CDPAutomationEngine.find_element_rect(ws, target)
                if contact_rect:
                    await CDPAutomationEngine.dispatch_mouse_click(ws, contact_rect["x"], contact_rect["y"])
                    await asyncio.sleep(0.6)
                    chat_opened = True

                # 2. If not visible or direct click failed, use search box
                if not chat_opened and search_rect:
                    await CDPAutomationEngine.dispatch_mouse_click(ws, search_rect["x"], search_rect["y"])
                    await asyncio.sleep(0.08)
                    await CDPAutomationEngine.evaluate_js(ws, 'if (document.activeElement) { document.activeElement.value = ""; document.activeElement.dispatchEvent(new Event("input", {bubbles: true})); }')
                    await CDPAutomationEngine.dispatch_type_text(ws, target, press_enter=True)
                    await asyncio.sleep(0.85)
                    chat_opened = True

                if not chat_opened:
                    return {
                        "status": "target_not_found",
                        "message": f"Could not locate search field or contact '{target}' in WhatsApp Web."
                    }

                # 3. If action is voice call, click the call button in chat header
                if action_type in ["call", "voice_call"]:
                    call_btn_rect = await CDPAutomationEngine.find_element_rect(ws, 'button[aria-label*="Voice call"], button[aria-label*="Call"], [data-testid="audio-call"]')
                    if not call_btn_rect:
                        # Try generalized voice call selector
                        call_btn_rect = await CDPAutomationEngine.find_element_rect(ws, '[aria-label*="call" i]')

                    if call_btn_rect:
                        await CDPAutomationEngine.dispatch_mouse_click(ws, call_btn_rect["x"], call_btn_rect["y"])
                        return {
                            "status": "calling",
                            "target": target,
                            "message": f"Contact '{target}' selected on WhatsApp Web. Voice call dispatched, boss."
                        }
                    else:
                        return {
                            "status": "chat_opened_call_button_unsupported",
                            "target": target,
                            "message": f"Chat with '{target}' opened on WhatsApp Web. Note: WhatsApp Web calling may require the native desktop client."
                        }

                # 4. If action is message, locate input box, type message, and click Send button
                if action_type == "message" and message_text:
                    msg_rect = await CDPAutomationEngine.find_element_rect(ws, 'footer div[contenteditable="true"], div[data-tab="10"], div[role="textbox"]')
                    if msg_rect:
                        # Click input field
                        await CDPAutomationEngine.dispatch_mouse_click(ws, msg_rect["x"], msg_rect["y"])
                        await asyncio.sleep(0.08)

                        # Clear any existing text
                        await CDPAutomationEngine.evaluate_js(ws, 'if (document.activeElement) { document.activeElement.innerHTML = ""; document.activeElement.dispatchEvent(new Event("input", {bubbles: true})); }')

                        # Type message text
                        await CDPAutomationEngine.dispatch_type_text(ws, message_text, press_enter=False)
                        await asyncio.sleep(0.3)

                        # Explicitly click the green Send button
                        send_btn_rect = await CDPAutomationEngine.find_element_rect(ws, 'footer button[aria-label="Send"], footer span[data-icon="send"], footer [data-testid="send"]')
                        if not send_btn_rect:
                            # Fallback: try finding any button in footer with Send
                            send_btn_rect = await CDPAutomationEngine.find_element_rect(ws, 'footer button:has(span[data-icon="send"]), footer button[aria-label*="Send" i]')

                        if send_btn_rect:
                            await CDPAutomationEngine.dispatch_mouse_click(ws, send_btn_rect["x"], send_btn_rect["y"])
                        else:
                            # Final fallback: dispatch explicit Enter key
                            await CDPAutomationEngine.dispatch_type_text(ws, "\r", press_enter=True)

                        await asyncio.sleep(0.6)

                        return {
                            "status": "message_sent",
                            "target": target,
                            "message_text": message_text,
                            "message": f"Message '{message_text}' dispatched and sent to '{target}' on WhatsApp Web, boss."
                        }

                # 5. Delete last sent message (send and then delete)
                if action_type in ["delete_last_message", "delete"]:
                    # Method A: Check if a message is ALREADY selected (selection bar with trash icon visible at bottom)
                    script_find_selection_trash = """
                    (function() {
                        // Look for trash can / delete icon in the selection action bar
                        const trashIcons = Array.from(document.querySelectorAll('span[data-icon="delete"], span[data-icon="trash"], button[aria-label="Delete"], div[aria-label="Delete"]'));
                        for (const el of trashIcons) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) {
                                return { x: r.x + r.width / 2, y: r.y + r.height / 2, type: 'selection_trash' };
                            }
                        }
                        return null;
                    })();
                    """
                    trash_btn = await CDPAutomationEngine.evaluate_js(ws, script_find_selection_trash)

                    if trash_btn:
                        # Click the trash icon in the selection bar
                        await CDPAutomationEngine.dispatch_mouse_click(ws, trash_btn["x"], trash_btn["y"])
                        await asyncio.sleep(0.5)
                    else:
                        # Method B: Not selected yet. Try to click message bubble or right click
                        script_find_last = """
                        (function() {
                            const msgs = Array.from(document.querySelectorAll('[data-testid="msg-container"]'));
                            if (!msgs.length) return null;
                            const lastMsg = msgs[msgs.length - 1];
                            const r = lastMsg.getBoundingClientRect();
                            return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height };
                        })();
                        """
                        msg_rect = await CDPAutomationEngine.evaluate_js(ws, script_find_last)
                        if msg_rect:
                            # 1. Dispatch right-click directly on the message bubble to open context menu
                            await CDPAutomationEngine.send_command(ws, 9001, "Input.dispatchMouseEvent", {
                                "type": "mousePressed", "x": msg_rect["x"], "y": msg_rect["y"], "button": "right", "clickCount": 1
                            })
                            await asyncio.sleep(0.04)
                            await CDPAutomationEngine.send_command(ws, 9002, "Input.dispatchMouseEvent", {
                                "type": "mouseReleased", "x": msg_rect["x"], "y": msg_rect["y"], "button": "right", "clickCount": 1
                            })
                            await asyncio.sleep(0.4)

                            # 2. Locate and click 'Delete' in the opened context menu
                            script_find_delete = """
                            (function() {
                                const items = Array.from(document.querySelectorAll('div[role="application"] [role="button"], ul li, [role="menuitem"], button'));
                                const delItem = items.find(i => (i.innerText || '').trim() === 'Delete' || i.getAttribute('aria-label') === 'Delete');
                                if (!delItem) return null;
                                const r = delItem.getBoundingClientRect();
                                return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
                            })();
                            """
                            del_pos = await CDPAutomationEngine.evaluate_js(ws, script_find_delete)
                            if del_pos:
                                await CDPAutomationEngine.dispatch_mouse_click(ws, del_pos["x"], del_pos["y"])
                                await asyncio.sleep(0.5)
                            else:
                                # Fallback: check again if selection trash can appeared
                                trash_btn = await CDPAutomationEngine.evaluate_js(ws, script_find_selection_trash)
                                if trash_btn:
                                    await CDPAutomationEngine.dispatch_mouse_click(ws, trash_btn["x"], trash_btn["y"])
                                    await asyncio.sleep(0.5)

                    # Step 2: Now handle the deletion confirmation modal ("Delete for everyone")
                    script_find_confirm = """
                    (function() {
                        const btns = Array.from(document.querySelectorAll('button, div[role="button"]'));
                        // Prioritize "Delete for everyone"
                        const everyoneBtn = btns.find(b => (b.innerText || '').toLowerCase().includes('delete for everyone'));
                        if (everyoneBtn) {
                            const r = everyoneBtn.getBoundingClientRect();
                            return { text: everyoneBtn.innerText, x: r.x + r.width / 2, y: r.y + r.height / 2 };
                        }
                        const forMeBtn = btns.find(b => (b.innerText || '').toLowerCase().includes('delete for me'));
                        if (forMeBtn) {
                            const r = forMeBtn.getBoundingClientRect();
                            return { text: forMeBtn.innerText, x: r.x + r.width / 2, y: r.y + r.height / 2 };
                        }
                        return null;
                    })();
                    """
                    conf_pos = await CDPAutomationEngine.evaluate_js(ws, script_find_confirm)
                    if conf_pos:
                        await CDPAutomationEngine.dispatch_mouse_click(ws, conf_pos["x"], conf_pos["y"])
                        await asyncio.sleep(0.4)
                        return {
                            "status": "message_deleted",
                            "target": target,
                            "message": f"Target message deleted ({conf_pos.get('text')}) on WhatsApp Web, boss."
                        }

                    return {
                        "status": "error_deleting",
                        "message": "Could not complete message deletion sequence on WhatsApp Web."
                    }

                return {
                    "status": "chat_opened",
                    "target": target,
                    "message": f"Chat locked in for '{target}' on WhatsApp Web, sir."
                }

        return {"status": "success", "message": "WhatsApp action completed."}


class MediaBridge:
    """Controls Spotify and system media playback using MPRIS (playerctl) and WirePlumber (wpctl)."""

    @staticmethod
    async def play_pause() -> Dict[str, Any]:
        """Toggle playback on the active media player."""
        return await ShellExecutor.run("playerctl play-pause")

    @staticmethod
    async def play(player: Optional[str] = None) -> Dict[str, Any]:
        p_arg = f"--player={player} " if player else ""
        return await ShellExecutor.run(f"playerctl {p_arg}play")

    @staticmethod
    async def pause(player: Optional[str] = None) -> Dict[str, Any]:
        p_arg = f"--player={player} " if player else ""
        return await ShellExecutor.run(f"playerctl {p_arg}pause")

    @staticmethod
    async def next_track(player: Optional[str] = None) -> Dict[str, Any]:
        p_arg = f"--player={player} " if player else ""
        return await ShellExecutor.run(f"playerctl {p_arg}next")

    @staticmethod
    async def previous_track(player: Optional[str] = None) -> Dict[str, Any]:
        p_arg = f"--player={player} " if player else ""
        return await ShellExecutor.run(f"playerctl {p_arg}previous")

    @classmethod
    async def resolve_spotify_track(cls, query: str) -> Optional[str]:
        """Resolve a song query to a direct Spotify URI (spotify:track:<id>)."""
        clean_q = query.strip()
        if clean_q.startswith("spotify:"):
            return clean_q
        url_match = re.search(r"open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)", clean_q)
        if url_match:
            return f"spotify:{url_match.group(1)}:{url_match.group(2)}"

        # Non-blocking search lookup via DDG Lite to find open.spotify.com track link
        try:
            encoded = urllib.parse.urlencode({"q": f"site:open.spotify.com/track {clean_q}"})
            url = f"https://lite.duckduckgo.com/lite/?{encoded}"
            headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0"}
            timeout = aiohttp.ClientTimeout(total=4)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        matches = re.findall(r"open\.spotify\.com(?:%2F|/)track(?:%2F|/)([a-zA-Z0-9]{22})", html)
                        if matches:
                            return f"spotify:track:{matches[0]}"
        except Exception as e:
            logger.debug(f"Direct track resolution lookup failed: {e}")
        return None

    @classmethod
    async def spotify_action(cls, action: str = "play", query: Optional[str] = None) -> Dict[str, Any]:
        """
        Native Spotify control via spotify-launcher, DBus OpenUri with track resolution, and playerctl.
        Ensures Spotify is running, launching it if necessary, then loads/plays songs or queues cleanly.
        """
        # Check if spotify is running
        check = await ShellExecutor.run("pgrep -f spotify")
        is_running = check.get("returncode") == 0

        if not is_running:
            # Launch native spotify-launcher
            await ShellExecutor.run("spotify-launcher &")
            # Poll until Spotify is responsive on DBus or playerctl with short interval
            for _ in range(25):
                await asyncio.sleep(0.1)
                p_check = await ShellExecutor.run("playerctl -p spotify status")
                if p_check.get("returncode") in [0, 1]:
                    break

        act = action.lower().strip()
        if act in ["play_query", "play_song", "search"] or (query and act in ["play", "liked", "liked_songs"]):
            search_str = query or "liked songs"
            resolved_uri = await cls.resolve_spotify_track(search_str)
            if resolved_uri:
                uri = resolved_uri
            elif search_str.startswith("spotify:"):
                uri = search_str
            else:
                uri = f"spotify:search:{urllib.parse.quote(search_str)}"

            safe_uri = uri.replace('"', '\\"')
            dbus_cmd = (
                f'dbus-send --print-reply --dest=org.mpris.MediaPlayer2.spotify '
                f'/org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player.OpenUri string:"{safe_uri}"'
            )
            res = await ShellExecutor.run(dbus_cmd)

            # Direct playerctl playback and verification loop (clean native Wayland/DBus, zero xdotool)
            for attempt in range(4):
                await asyncio.sleep(0.2)
                await ShellExecutor.run("playerctl --player=spotify,spotify_player play")
                stat_check = await ShellExecutor.run("playerctl --player=spotify,spotify_player status")
                current_stat = stat_check.get("stdout", "").strip()
                if current_stat.lower() == "playing":
                    return {"status": "playing_query", "query": search_str, "uri": uri, "verified": True, "attempts": attempt + 1}

            return {"status": "dispatched", "query": search_str, "uri": uri, "verified": False, "note": "Dispatched to Spotify via DBus OpenUri."}

        elif act in ["play", "resume", "liked", "liked_songs"]:
            # Play via playerctl targeting spotify with zero-token verification loop
            for attempt in range(4):
                res = await ShellExecutor.run("playerctl --player=spotify,spotify_player play")
                if res.get("returncode") != 0:
                    dbus_cmd = (
                        'dbus-send --print-reply --dest=org.mpris.MediaPlayer2.spotify '
                        '/org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player.OpenUri string:"spotify:collection:tracks"'
                    )
                    await ShellExecutor.run(dbus_cmd)
                    await asyncio.sleep(0.25)
                    await ShellExecutor.run("playerctl --player=spotify,spotify_player play")

                await asyncio.sleep(0.2)
                stat_check = await ShellExecutor.run("playerctl -p spotify status")
                if stat_check.get("stdout", "").strip().lower() == "playing":
                    return {"status": "playing", "player": "spotify", "verified": True, "attempts": attempt + 1}

            return {"status": "playing", "player": "spotify", "verified": False}
        elif act in ["pause", "stop"]:
            return await ShellExecutor.run("playerctl --player=spotify,spotify_player pause")
        elif act in ["next", "skip"]:
            return await ShellExecutor.run("playerctl --player=spotify,spotify_player next")
        elif act in ["prev", "previous"]:
            return await ShellExecutor.run("playerctl --player=spotify,spotify_player previous")
        elif act == "play_pause":
            return await ShellExecutor.run("playerctl --player=spotify,spotify_player play-pause")
        else:
            return {"error": f"Unknown spotify action: {action}"}

    @staticmethod
    async def get_current_track() -> Dict[str, Any]:
        """Retrieve the currently playing track title, artist, and status."""
        status_res = await ShellExecutor.run("playerctl status")
        metadata_res = await ShellExecutor.run('playerctl metadata --format "{{title}} by {{artist}} [{{album}}]"')
        return {
            "status": status_res.get("stdout", "").strip() or "Stopped",
            "metadata": metadata_res.get("stdout", "").strip() or "No media playing"
        }

    @staticmethod
    async def adjust_volume(delta: str = "+5%") -> Dict[str, Any]:
        """Adjust system audio volume using wpctl (e.g. '+5%', '-5%')."""
        return await ShellExecutor.run(f"wpctl set-volume @DEFAULT_AUDIO_SINK@ {delta}")

    @staticmethod
    async def set_volume(level: str = "50%") -> Dict[str, Any]:
        """Set system audio volume using wpctl (e.g. '50%')."""
        try:
            val = float(level.replace("%", "")) / 100.0
            return await ShellExecutor.run(f"wpctl set-volume @DEFAULT_AUDIO_SINK@ {val:.2f}")
        except Exception as e:
            return {"error": f"Invalid volume format: {e}"}


class SystemAppBridge:
    """Controls safe desktop applications, Hyprland window manager, and notifications."""

    SAFE_APPS = {
        "obsidian": "obsidian",
        "solidcraft": "solidcraft",
        "kitty": "kitty",
        "dolphin": "dolphin",
        "blender": "blender",
        "freecad": "freecad",
        "brave": "brave",
        "spotify": "spotify-launcher",
        "spotify-launcher": "spotify-launcher",
        "code": "code",
        "code-oss": "code-oss"
    }

    @classmethod
    async def launch_app(cls, app_name: str, workspace: Optional[Union[int, str]] = None) -> Dict[str, Any]:
        """
        Safely launch a recognized desktop application, assigning to a specific Hyprland workspace
        using deterministic zero-token verification and retry loops.
        """
        app_clean = app_name.lower().strip()
        binary = cls.SAFE_APPS.get(app_clean, app_clean)

        if workspace is not None:
            ws_target = str(workspace)
            # Switch to workspace first using Hyprland Lua dispatch
            await cls.hyprctl_dispatch("workspace", ws_target)
            await asyncio.sleep(0.08)
            cmd = f"{binary} &"
            res = await ShellExecutor.run(cmd)

            # Zero-token verification loop: ensure window matching app appears on requested workspace
            for attempt in range(3):
                await asyncio.sleep(0.4)
                clients_raw = await ShellExecutor.run("hyprctl clients -j")
                try:
                    clients = json.loads(clients_raw.get("stdout", "[]"))
                except Exception:
                    clients = []

                found = False
                for c in clients:
                    cls_name = c.get("class", "").lower()
                    init_cls = c.get("initialClass", "").lower()
                    title = c.get("title", "").lower()
                    if app_clean in cls_name or app_clean in init_cls or app_clean in title or binary in cls_name:
                        cur_ws = str(c.get("workspace", {}).get("name", ""))
                        if cur_ws == ws_target:
                            found = True
                            break
                        else:
                            # Move it to target workspace directly using movetoworkspacesilent
                            target_window = f"class:{c.get('class')}"
                            await cls.hyprctl_dispatch("movetoworkspacesilent", f"{ws_target},{target_window}")
                            found = True
                            break

                if found:
                    await cls.notify("Ghost System", f"Launched {binary} on workspace {ws_target}")
                    return {"status": "launched", "app": binary, "workspace": ws_target, "verified": True, "attempts": attempt + 1}

            await cls.notify("Ghost System", f"Launched {binary}")
            return {"status": "launched", "app": binary, "workspace": ws_target, "verified": False}
        else:
            cmd = f"{binary} &"
            res = await ShellExecutor.run(cmd)
            await cls.notify("Ghost System", f"Launched {binary}")
            return {"status": "launched", "app": binary, "details": res}

    @staticmethod
    async def hyprctl_dispatch(dispatcher: str, args: str = "") -> Dict[str, Any]:
        """
        Execute any Hyprland dispatcher safely using standard Hyprland CLI dispatch syntax
        with automatic Lua dispatch translation for Hyprland 0.56+ compatibility.
        """
        clean_disp = dispatcher.strip().lower()
        clean_args = str(args).strip()

        # 1. Try standard CLI dispatch first (e.g. hyprctl dispatch movetoworkspacesilent 1,class:kitty)
        cmd = f"hyprctl dispatch {clean_disp} {clean_args}".strip()
        res = await ShellExecutor.run(cmd)
        if res.get("returncode") == 0:
            return {"status": "dispatched", "command": cmd, "result": res}

        # 2. If CLI dispatch failed (e.g. Hyprland 0.56+ requiring Lua syntax), map to hl.dsp:
        lua_call = None
        if clean_disp in ["workspace", "switch_workspace"]:
            lua_call = f"hl.dsp.focus({{ workspace = {clean_args} }})"
        elif clean_disp in ["movetoworkspacesilent", "move_to_workspace_silent"]:
            parts = [p.strip() for p in clean_args.split(",", 1)]
            ws = parts[0]
            win_arg = f', window = "{parts[1]}"' if len(parts) > 1 and parts[1] else ""
            lua_call = f"hl.dsp.window.move({{ workspace = {ws}, silent = true{win_arg} }})"
        elif clean_disp in ["movetoworkspace", "move_to_workspace"]:
            parts = [p.strip() for p in clean_args.split(",", 1)]
            ws = parts[0]
            win_arg = f', window = "{parts[1]}"' if len(parts) > 1 and parts[1] else ""
            lua_call = f"hl.dsp.window.move({{ workspace = {ws}{win_arg} }})"
        elif clean_disp in ["focuswindow", "focus_window"]:
            lua_call = f'hl.dsp.focus({{ window = "{clean_args}" }})'
        elif clean_disp in ["killactive", "close_window", "closewindow"]:
            if clean_args:
                lua_call = f'hl.dsp.window.close({{ window = "{clean_args}" }})'
            else:
                lua_call = "hl.dsp.window.close()"
        elif clean_disp in ["togglefloating"]:
            lua_call = "hl.dsp.window.float()"
        elif clean_disp in ["fullscreen", "toggle_fullscreen"]:
            lua_call = "hl.dsp.window.fullscreen()"

        if lua_call:
            lua_code = f"return hl.dispatch({lua_call})"
            lua_res = await ShellExecutor.run(f"hyprctl repl '{lua_code}'")
            if lua_res.get("returncode") == 0:
                return {"status": "dispatched", "command": lua_code, "result": lua_res}
            return {"status": "error", "command": lua_code, "result": lua_res}

        return {"status": "error", "command": cmd, "result": res}

    @classmethod
    async def set_wallpaper(cls, query_or_path: str) -> Dict[str, Any]:
        """
        Direct wallpaper switcher using Quickshell's native theme-system.sh script.
        Matches wallpaper names or takes absolute path.
        """
        script_path = os.path.expanduser("~/.config/quickshell/cool-shell/island/scripts/theme-system.sh")
        target_path = query_or_path.strip()

        # If it's not an existing absolute path, search theme-system wallpapers
        if not os.path.exists(target_path):
            list_res = await ShellExecutor.run(f'bash "{script_path}" wallpapers')
            try:
                wallpapers = json.loads(list_res.get("stdout", "[]"))
            except Exception:
                wallpapers = []

            q = query_or_path.lower()
            matched = None
            for w in wallpapers:
                name = w.get("name", "").lower()
                path = w.get("path", "").lower()
                if q in name or q in path:
                    matched = w.get("path")
                    break

            if matched:
                target_path = matched
            else:
                # Direct fallback check in Wallpapers folder
                wall_dir = os.path.expanduser("~/Pictures/Wallpapers")
                for root, _, files in os.walk(wall_dir):
                    for f in files:
                        if q in f.lower():
                            target_path = os.path.join(root, f)
                            break
                    if os.path.exists(target_path):
                        break

        if not os.path.exists(target_path):
            # Fallback: toggle wallpaper menu in Quickshell
            await ShellExecutor.run("qs -c cool-shell ipc call notch toggle wallpaper")
            return {"status": "menu_toggled", "message": f"Could not find wallpaper matching '{query_or_path}'. Toggled wallpaper selector menu, sir."}

        res = await ShellExecutor.run(f'bash "{script_path}" wallpaper "{target_path}"')
        await cls.notify("Ghost System", f"Wallpaper updated: {os.path.basename(target_path)}")

        # Check if file has a messy/long name and can be cleanly indexed
        base_name = os.path.basename(target_path)
        dir_name = os.path.dirname(target_path)
        ext = os.path.splitext(base_name)[1]
        clean_alias = None

        if "mw2" in base_name.lower() or "ghost" in base_name.lower():
            if base_name != f"ghost{ext}":
                clean_alias = "ghost"

        if clean_alias:
            new_name = f"{clean_alias}{ext}"
            new_path = os.path.join(dir_name, new_name)
            # Ask tactical authorization before renaming
            allowed = await request_tactical_authorization(
                f"Rename '{base_name}' to '{new_name}' for faster instant deployment?"
            )
            if allowed:
                try:
                    os.rename(target_path, new_path)
                    target_path = new_path
                    # Re-apply to refresh theme-system cache
                    await ShellExecutor.run(f'bash "{script_path}" wallpaper "{target_path}"')
                    await cls.notify("Ghost System", f"Indexed wallpaper as {new_name}")
                except Exception as e:
                    pass

        return {"status": "success", "wallpaper": target_path, "name": os.path.basename(target_path), "result": res}

    @staticmethod
    async def notify(title: str, message: str) -> Dict[str, Any]:
        """Send a native Linux desktop notification via notify-send."""
        safe_title = title.replace('"', '\\"')
        safe_msg = message.replace('"', '\\"')
        return await ShellExecutor.run(f'notify-send -a "Ghost" "{safe_title}" "{safe_msg}"')

    @staticmethod
    async def hyprctl_command(cmd: str) -> Dict[str, Any]:
        """Execute a hyprctl command to inspect or switch workspaces/windows."""
        return await ShellExecutor.run(f"hyprctl {cmd}")

    @staticmethod
    async def get_active_window() -> Dict[str, Any]:
        """Get information on the currently active Hyprland window."""
        res = await ShellExecutor.run("hyprctl activewindow -j")
        try:
            return json.loads(res.get("stdout", "{}"))
        except Exception:
            return {"raw": res.get("stdout", "")}

    @classmethod
    async def capture_screen(cls, region: str = "full", window: Optional[str] = None) -> Dict[str, Any]:
        """
        Fast desktop reconnaissance snapshot using grim + lightweight downscaling.
        Returns compressed base64 data URI (1280x720 JPEG, ~65% quality) to conserve LLM tokens.
        """
        raw_path = "/tmp/ghost_screen_raw.jpg"
        save_path = "/tmp/ghost_screen.jpg"
        if region in ["active_window", "window"] or window:
            active = await cls.get_active_window()
            if active and "at" in active and "size" in active:
                at_x, at_y = active["at"][0], active["at"][1]
                w, h = active["size"][0], active["size"][1]
                cmd = f'grim -g "{at_x},{at_y} {w}x{h}" -t jpeg -q 75 {raw_path}'
            else:
                cmd = f"grim -t jpeg -q 75 {raw_path}"
        else:
            cmd = f"grim -t jpeg -q 75 {raw_path}"

        res = await ShellExecutor.run(cmd)
        if res.get("returncode") != 0 or not os.path.exists(raw_path):
            return {"status": "error", "message": "Failed to capture screen with grim", "details": res}

        # Downscale and compress to max 1280x720 to keep vision tokens ultra-low (~200 tokens)
        await ShellExecutor.run(f"ffmpeg -y -i {raw_path} -vf 'scale=1280:-1' -q:v 8 {save_path} 2>/dev/null || cp {raw_path} {save_path}")

        try:
            with open(save_path, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode("utf-8")
            data_uri = f"data:image/jpeg;base64,{b64_data}"
            return {
                "status": "success",
                "path": save_path,
                "data_uri": data_uri,
                "region": region,
                "size_bytes": os.path.getsize(save_path)
            }
        except Exception as e:
            return {"status": "error", "message": f"Failed reading screenshot: {e}"}

    @staticmethod
    async def clipboard_action(operation: str = "read", text: Optional[str] = None) -> Dict[str, Any]:
        """
        Direct Wayland clipboard interaction via wl-paste and wl-copy.
        """
        op = operation.lower().strip()
        if op in ["write", "copy", "set"]:
            if text is None:
                return {"status": "error", "message": "Text required for clipboard write"}
            # Write via wl-copy
            safe_text = text.replace('"', '\\"')
            res = await ShellExecutor.run(f'wl-copy "{safe_text}"')
            return {"status": "success", "operation": "write", "text": text, "result": res}
        else:
            # Read via wl-paste
            res = await ShellExecutor.run("wl-paste --no-newline")
            content = res.get("stdout", "")
            return {"status": "success", "operation": "read", "content": content}

    @staticmethod
    async def input_simulation(sim_type: str, keys: Optional[str] = None, x: Optional[int] = None, y: Optional[int] = None, window_query: Optional[str] = None) -> Dict[str, Any]:
        """
        Universal input simulation for native Wayland and XWayland windows.
        Uses Hyprland's native compositor send_shortcut dispatcher (zero root / daemon dependency).
        sim_type: 'key', 'shortcut', 'type_text', 'click'
        """
        clean_type = sim_type.lower().strip()

        # If a window query is supplied, focus it first
        if window_query:
            await cls.hyprctl_dispatch("focuswindow", window_query)
            await asyncio.sleep(0.1)

        if clean_type in ["key", "shortcut"]:
            key_val = (keys or "Return").strip()

            # Parse modifiers like "SUPER + M", "CTRL + ALT + T", "SUPER + SHIFT + 1"
            parts = [p.strip() for p in key_val.replace("+", " ").split()]
            mod_list = []
            main_key = parts[-1] if parts else "Return"
            if len(parts) > 1:
                for p in parts[:-1]:
                    up = p.upper()
                    if up in ["SUPER", "MOD4", "WIN"]:
                        mod_list.append("SUPER")
                    elif up in ["CTRL", "CONTROL"]:
                        mod_list.append("CTRL")
                    elif up in ["ALT", "MOD1"]:
                        mod_list.append("ALT")
                    elif up in ["SHIFT"]:
                        mod_list.append("SHIFT")

            mod_str = " ".join(mod_list)
            if mod_str:
                lua_call = f'return hl.dispatch(hl.dsp.send_shortcut({{ mod = "{mod_str}", key = "{main_key}" }}))'
            else:
                lua_call = f'return hl.dispatch(hl.dsp.send_shortcut({{ key = "{main_key}" }}))'

            res = await ShellExecutor.run(f"hyprctl repl '{lua_call}'")
            return {"status": "simulated", "type": clean_type, "mod": mod_str, "key": main_key, "result": res}

        elif clean_type == "type_text":
            text_val = keys or ""
            # Native Wayland text injection via wl-copy and compositor paste
            safe_text = text_val.replace('"', '\\"')
            await ShellExecutor.run(f'wl-copy "{safe_text}"')
            await asyncio.sleep(0.04)
            lua_paste = 'return hl.dispatch(hl.dsp.send_shortcut({ mod = "CTRL", key = "v" }))'
            res = await ShellExecutor.run(f"hyprctl repl '{lua_paste}'")
            return {"status": "simulated", "type": "type_text", "text": text_val, "result": res}

        elif clean_type in ["click", "mouse_click", "mouse_move"]:
            if x is not None and y is not None:
                # Move cursor via Hyprland Lua dispatch
                res = await ShellExecutor.run(f"hyprctl repl 'return hl.dispatch(hl.dsp.cursor.move({{ x = {int(x)}, y = {int(y)} }}))'")
                return {"status": "simulated", "type": "cursor_move", "x": x, "y": y, "result": res}
            else:
                # Dispatch Return as standard activation without xdotool
                res = await ShellExecutor.run("hyprctl repl 'return hl.dispatch(hl.dsp.send_shortcut({ key = \"Return\" }))'")
                return {"status": "simulated", "type": "click", "result": res}

        return {"status": "error", "message": f"Unsupported simulation type: {sim_type}"}

    @staticmethod
    async def file_operation(action: str, source: str, destination: Optional[str] = None) -> Dict[str, Any]:
        """
        Safe file and folder operations (move, rename, copy, delete, mkdir) with mandatory
        human-in-the-loop tactical authorization in the Ghost GUI.
        """
        import shutil
        clean_act = action.lower().strip()
        src = os.path.abspath(os.path.expanduser(source))

        if clean_act in ["move", "mv", "rename"]:
            if not destination:
                return {"status": "error", "message": "Destination required for move/rename"}
            dst = os.path.abspath(os.path.expanduser(destination))
            if not os.path.exists(src):
                return {"status": "error", "message": f"Source does not exist: {src}"}

            desc = f"Move/Rename '{src}' -> '{dst}'"
            allowed = await request_tactical_authorization(desc)
            if not allowed:
                return {"status": "denied", "message": "Operation aborted per your orders, boss."}

            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            await SystemAppBridge.notify("Ghost File System", f"Moved: {os.path.basename(src)} -> {os.path.basename(dst)}")
            return {"status": "success", "action": "move", "source": src, "destination": dst}

        elif clean_act in ["copy", "cp"]:
            if not destination:
                return {"status": "error", "message": "Destination required for copy"}
            dst = os.path.abspath(os.path.expanduser(destination))
            if not os.path.exists(src):
                return {"status": "error", "message": f"Source does not exist: {src}"}

            desc = f"Copy '{src}' -> '{dst}'"
            allowed = await request_tactical_authorization(desc)
            if not allowed:
                return {"status": "denied", "message": "Operation aborted per your orders, boss."}

            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            return {"status": "success", "action": "copy", "source": src, "destination": dst}

        elif clean_act in ["delete", "remove", "rm"]:
            if not os.path.exists(src):
                return {"status": "error", "message": f"Target does not exist: {src}"}

            desc = f"PERMANENTLY DELETE '{src}'"
            allowed = await request_tactical_authorization(desc)
            if not allowed:
                return {"status": "denied", "message": "Operation aborted per your orders, boss."}

            if os.path.isdir(src):
                shutil.rmtree(src)
            else:
                os.remove(src)
            await SystemAppBridge.notify("Ghost File System", f"Deleted: {os.path.basename(src)}")
            return {"status": "success", "action": "delete", "target": src}

        elif clean_act in ["mkdir", "create_dir"]:
            os.makedirs(src, exist_ok=True)
            return {"status": "success", "action": "mkdir", "path": src}

        return {"status": "error", "message": f"Unsupported file operation: {action}"}

    @staticmethod
    async def desktop_notify(title: str, message: str, urgency: str = "normal") -> Dict[str, Any]:
        """
        Send a desktop notification to the user's HUD via notify-send.
        urgency: 'low', 'normal', 'critical'
        """
        safe_title = title.replace('"', '\\"')
        safe_msg = message.replace('"', '\\"')
        clean_urg = urgency if urgency in ["low", "normal", "critical"] else "normal"
        cmd = f'notify-send -a "Ghost" -u {clean_urg} "{safe_title}" "{safe_msg}"'
        res = await ShellExecutor.run(cmd)
        return {"status": "notified", "title": title, "message": message, "urgency": clean_urg, "result": res}


class ReaBridge:
    """Interfaces with REA reverse engineering CLI."""

    @staticmethod
    async def run_command(command: str, args: List[str] = None, timeout: float = 30.0) -> Dict[str, Any]:
        args_str = " ".join(args) if args else ""
        cmd = f"node {REA_BIN} {command} {args_str}".strip()
        return await ShellExecutor.run(cmd, timeout=timeout)

    @staticmethod
    async def analyze(target_path: str) -> Dict[str, Any]:
        return await ReaBridge.run_command("analyze", [f'"{target_path}"'])

    @staticmethod
    async def doctor() -> Dict[str, Any]:
        return await ReaBridge.doctor()

    @staticmethod
    async def capabilities() -> Dict[str, Any]:
        return await ReaBridge.capabilities()


class WebSearchBridge:
    """Universal Web Search MCP capability using Brave Search API with DuckDuckGo fallback."""

    @staticmethod
    async def search(query: str, max_results: int = 5) -> Dict[str, Any]:
        """Search the web and return concise titles, snippets, and URLs."""
        clean_query = query.strip()
        if not clean_query:
            return {"status": "error", "message": "Search query cannot be empty"}

        # 1. Check for Brave Search API Key if configured
        brave_key = os.environ.get("BRAVE_SEARCH_API_KEY")
        if brave_key:
            try:
                def _brave():
                    encoded = urllib.parse.quote(clean_query)
                    req = urllib.request.Request(
                        f"https://api.search.brave.com/res/v1/web/search?q={encoded}&count={max_results}",
                        headers={"Accept": "application/json", "X-Subscription-Token": brave_key}
                    )
                    with urllib.request.urlopen(req, timeout=6) as resp:
                        data = json.loads(resp.read().decode())
                        results = []
                        for item in data.get("web", {}).get("results", [])[:max_results]:
                            results.append({
                                "title": item.get("title"),
                                "url": item.get("url"),
                                "snippet": item.get("description")
                            })
                        return {"status": "success", "engine": "brave", "count": len(results), "results": results}
                return await asyncio.to_thread(_brave)
            except Exception as e:
                logger.warning(f"Brave Search API failed: {e}. Falling back to DuckDuckGo.")

        # 2. DuckDuckGo Fast Clean Scrape Fallback
        try:
            import re
            def _ddg():
                data = urllib.parse.urlencode({"q": clean_query}).encode("utf-8")
                req = urllib.request.Request(
                    "https://lite.duckduckgo.com/lite/",
                    data=data,
                    headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0"}
                )
                with urllib.request.urlopen(req, timeout=7) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")

                raw_links = re.findall(r'<a rel=[\'"]nofollow[\'"][^>]*href=[\'"]([^\'"]+)[\'"][^>]*>(.*?)</a>', html, re.IGNORECASE)
                snippets = re.findall(r'<td class=[\'"]result-snippet[\'"][^>]*>(.*?)</td>', html, re.DOTALL | re.IGNORECASE)

                results = []
                count = min(len(raw_links), len(snippets), max_results)
                for i in range(count):
                    title = re.sub(r'<[^>]+>', '', raw_links[i][1]).strip()
                    snip = re.sub(r'<[^>]+>', '', snippets[i]).strip()
                    url = raw_links[i][0]
                    # Clean DDG redirect URL if present
                    if "duckduckgo.com/l/?uddg=" in url:
                        try:
                            parsed = urllib.parse.urlparse(url)
                            url = urllib.parse.parse_qs(parsed.query).get("uddg", [url])[0]
                        except Exception:
                            pass
                    results.append({"title": title, "url": url, "snippet": snip})

                return {"status": "success", "engine": "duckduckgo", "query": clean_query, "count": len(results), "results": results}

            return await asyncio.to_thread(_ddg)
        except Exception as e:
            return {"status": "error", "message": f"Web search failed: {e}"}


class DatabaseBridge:
    """Database query primitive for SQLite and PostgreSQL MCP."""

    @staticmethod
    async def query_sqlite(db_path: str, query: str) -> Dict[str, Any]:
        """Execute safe read-only SQL queries against local SQLite database files."""
        clean_path = os.path.abspath(os.path.expanduser(db_path.strip()))
        if not os.path.exists(clean_path):
            return {"status": "error", "message": f"Database file not found: {clean_path}"}

        clean_sql = query.strip()
        # Enforce read-only constraint by default for safety
        first_word = clean_sql.split()[0].upper() if clean_sql else ""
        if first_word not in ["SELECT", "PRAGMA", "EXPLAIN"]:
            return {
                "status": "error",
                "message": f"Operation '{first_word}' blocked. Database bridge permits read-only queries (SELECT, PRAGMA) for safety."
            }

        def _exec():
            import sqlite3
            uri = f"file:{clean_path}?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=5)
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute(clean_sql)
            rows = cur.fetchall()
            col_names = [d[0] for d in cur.description] if cur.description else []
            data = [dict(r) for r in rows[:100]]
            con.close()
            return {
                "status": "success",
                "database": clean_path,
                "columns": col_names,
                "row_count": len(rows),
                "rows": data
            }

        try:
            return await asyncio.to_thread(_exec)
        except Exception as e:
            return {"status": "error", "message": f"Database query error: {e}"}


class GitHubBridge:
    """Direct GitHub API & MCP primitive for repositories, issues, code, and profiles."""

    BASE_URL = "https://api.github.com"

    @classmethod
    async def run_action(cls, action: str, repo: Optional[str] = None, path: Optional[str] = None, query: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute GitHub operations:
        - get_repo: metadata, stars, description
        - search_repos: query public repositories
        - get_file: view contents of a file in a repository
        - list_issues: open issues for a repository
        - get_user: user profile stats
        """
        clean_action = action.lower().strip()
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

        def _request(endpoint: str, query_params: Optional[Dict[str, Any]] = None):
            url = f"{cls.BASE_URL}{endpoint}"
            if query_params:
                url += "?" + urllib.parse.urlencode(query_params)
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Ghost-Assistant-141"
            }
            if token:
                headers["Authorization"] = f"token {token}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                return json.loads(resp.read().decode())

        try:
            if clean_action in ["get_repo", "repo"]:
                if not repo:
                    return {"status": "error", "message": "Repository (owner/repo) required"}
                data = await asyncio.to_thread(_request, f"/repos/{repo}")
                return {
                    "status": "success",
                    "repo": data.get("full_name"),
                    "description": data.get("description"),
                    "stars": data.get("stargazers_count"),
                    "forks": data.get("forks_count"),
                    "language": data.get("language"),
                    "open_issues": data.get("open_issues_count"),
                    "url": data.get("html_url")
                }

            elif clean_action in ["search_repos", "search"]:
                q = query or repo or ""
                data = await asyncio.to_thread(_request, "/search/repositories", {"q": q, "per_page": 5})
                items = [
                    {
                        "name": r.get("full_name"),
                        "stars": r.get("stargazers_count"),
                        "description": r.get("description"),
                        "url": r.get("html_url")
                    }
                    for r in data.get("items", [])[:5]
                ]
                return {"status": "success", "count": len(items), "repositories": items}

            elif clean_action in ["get_file", "file"]:
                if not repo or not path:
                    return {"status": "error", "message": "Both repo and path parameters are required to read a file"}
                import base64
                data = await asyncio.to_thread(_request, f"/repos/{repo}/contents/{path}")
                encoded_content = data.get("content", "")
                decoded = base64.b64decode(encoded_content).decode("utf-8", errors="replace")
                return {
                    "status": "success",
                    "repo": repo,
                    "path": path,
                    "size": data.get("size"),
                    "content": decoded[:4000]
                }

            elif clean_action in ["list_issues", "issues"]:
                if not repo:
                    return {"status": "error", "message": "Repository required to list issues"}
                data = await asyncio.to_thread(_request, f"/repos/{repo}/issues", {"state": "open", "per_page": 5})
                issues = [
                    {
                        "number": i.get("number"),
                        "title": i.get("title"),
                        "author": i.get("user", {}).get("login"),
                        "created_at": i.get("created_at"),
                        "url": i.get("html_url")
                    }
                    for i in data if "pull_request" not in i
                ]
                return {"status": "success", "repo": repo, "issues": issues}

            elif clean_action in ["get_user", "user"]:
                u = query or repo or "torvalds"
                data = await asyncio.to_thread(_request, f"/users/{u}")
                return {
                    "status": "success",
                    "username": data.get("login"),
                    "name": data.get("name"),
                    "bio": data.get("bio"),
                    "public_repos": data.get("public_repos"),
                    "followers": data.get("followers"),
                    "url": data.get("html_url")
                }

            return {"status": "error", "message": f"Unsupported GitHub action: {action}"}
        except Exception as e:
            return {"status": "error", "message": f"GitHub API error: {e}"}


class AgentDesktopBridge:
    """
    Wraps Vercel Labs native accessibility OS engine (`agent-desktop`).
    Enables reliable semantic inspection and manipulation of Linux desktop applications
    (Obsidian, Figma, Discord, Kitty, system dialogs) without vision token bloat.
    """
    BIN_PATH = "/home/pranc/.local/bin/agent-desktop"

    @classmethod
    async def snapshot(
        cls,
        app: Optional[str] = None,
        window_id: Optional[str] = None,
        compact: bool = True,
        interactive: bool = True
    ) -> Dict[str, Any]:
        """
        Captures structured UI tree with stable @ref IDs.
        """
        args = [cls.BIN_PATH, "snapshot"]
        if app:
            args.extend(["--app", app])
        if window_id:
            args.extend(["--window-id", window_id])
        if interactive:
            args.append("-i")
        if compact:
            args.append("--compact")

        cmd = " ".join(f'"{a}"' if " " in a else a for a in args)
        res = await ShellExecutor.run(cmd)
        stdout = res.get("stdout", "").strip()
        try:
            return json.loads(stdout)
        except Exception:
            return {"ok": res.get("success", False), "raw": stdout, "stderr": res.get("stderr")}

    @classmethod
    async def find(
        cls,
        query: Optional[str] = None,
        role: Optional[str] = None,
        name: Optional[str] = None,
        app: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Searches UI accessibility tree elements by query, role, or name.
        """
        args = [cls.BIN_PATH, "find"]
        if app:
            args.extend(["--app", app])
        if role:
            args.extend(["--role", role])
        if name:
            args.extend(["--name", name])
        if query:
            args.extend(["--text", query])

        cmd = " ".join(f'"{a}"' if " " in a else a for a in args)
        res = await ShellExecutor.run(cmd)
        stdout = res.get("stdout", "").strip()
        try:
            return json.loads(stdout)
        except Exception:
            return {"ok": res.get("success", False), "raw": stdout, "stderr": res.get("stderr")}

    @classmethod
    async def action(
        cls,
        command: str,
        target_ref: Optional[str] = None,
        value: Optional[str] = None,
        headed: bool = False,
        app: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes click, type, press, scroll, or focus on stable @ref targets with auto-waiting.
        Commands: 'click', 'double-click', 'right-click', 'type', 'press', 'scroll', 'focus', 'clear'
        """
        cmd_clean = command.lower().strip()
        args = [cls.BIN_PATH, cmd_clean]

        if headed:
            args.append("--headed")

        if cmd_clean == "press":
            combo = value or target_ref or "Return"
            if app:
                args.extend(["--app", app])
            args.append(combo)
        elif cmd_clean == "type":
            if not target_ref:
                return {"ok": False, "error": {"message": "target_ref required for type action"}}
            text_val = value or ""
            args.extend([target_ref, text_val])
        elif cmd_clean in ["click", "double-click", "right-click", "focus", "clear"]:
            if not target_ref:
                return {"ok": False, "error": {"message": f"target_ref required for {cmd_clean} action"}}
            args.append(target_ref)
        elif cmd_clean == "scroll":
            if not target_ref:
                return {"ok": False, "error": {"message": "target_ref required for scroll action"}}
            direction = value or "down"
            args.extend(["--direction", direction, target_ref])
        cmd = " ".join(f'"{a}"' if " " in a else a for a in args)
        res = await ShellExecutor.run(cmd)
        stdout = res.get("stdout", "").strip()
        try:
            return json.loads(stdout)
        except Exception:
            return {"ok": res.get("success", False), "raw": stdout, "stderr": res.get("stderr")}


class GitBridge:
    """Deterministic Git bridge with headless SSH key passphrase support."""

    @classmethod
    async def run_git(
        cls,
        args: List[str],
        repo_dir: str = "/home/pranc/projects/ghost",
        passphrase: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes git commands with automatic headless SSH passkey handling.
        Captures exact stdout, stderr, and return code.
        """
        env = os.environ.copy()
        temp_askpass = None

        if passphrase:
            temp_askpass = f"/tmp/git_askpass_{os.getpid()}.sh"
            try:
                with open(temp_askpass, "w") as f:
                    f.write(f'#!/bin/sh\necho "{passphrase}"\n')
                os.chmod(temp_askpass, 0o700)
                env["SSH_ASKPASS"] = temp_askpass
                env["SSH_ASKPASS_REQUIRE"] = "force"
                env["DISPLAY"] = ":0"
            except Exception as e:
                return {"success": False, "error": f"Failed creating askpass script: {e}"}

        try:
            cmd = "git " + " ".join(args)
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=repo_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            stdout, stderr = await proc.communicate()
            return {
                "success": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace").strip(),
                "stderr": stderr.decode("utf-8", errors="replace").strip(),
                "command": cmd
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if temp_askpass and os.path.exists(temp_askpass):
                try:
                    os.remove(temp_askpass)
                except Exception:
                    pass



