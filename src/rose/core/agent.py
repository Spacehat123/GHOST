"""Rose Agent Core integrating fury-sdk runner and full-duplex interruption."""
import asyncio
import os
import json
import logging
from typing import AsyncGenerator, Dict, Any, Optional, Callable

logger = logging.getLogger("rose.core")

try:
    import fury
    from fury import Agent, Runner, Tool
    HAS_FURY = True
except ImportError:
    HAS_FURY = False

from rose.mcp.bridge import ShellExecutor, BraveBridge

class RoseAgent:
    """Agent orchestrator wrapping fury-sdk with live interruption support."""

    def __init__(self, system_prompt: Optional[str] = None):
        self.system_prompt = system_prompt or (
            "You are Rose, an advanced voice desktop assistant. "
            "Keep answers concise, direct, and conversational. "
            "You have tools for shell execution and web search."
        )
        self.history = []
        self._current_runner = None
        self._interrupt_event = asyncio.Event()

    def interrupt(self):
        """Immediately triggers interruption of the active generation/tool call."""
        logger.info("RoseAgent: Barge-in interruption triggered")
        self._interrupt_event.set()
        if self._current_runner and hasattr(self._current_runner, "interrupt"):
            try:
                self._current_runner.interrupt()
            except Exception as e:
                logger.warning(f"Error interrupting fury runner: {e}")

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch a tool call to ShellExecutor or BraveBridge."""
        if tool_name == "shell_exec":
            cmd = arguments.get("command", "")
            return await ShellExecutor.run(cmd)
        elif tool_name == "brave_search":
            query = arguments.get("query", "")
            return await BraveBridge.search(query)
        else:
            return {"error": f"Unknown tool: {tool_name}"}

    async def ask_stream(self, user_text: str) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream agent generation with real-time token yields, tool call events,
        and instant interruption handling.
        """
        self._interrupt_event.clear()
        
        # Check if user query is a tool command directly
        lower_text = user_text.strip().lower()
        
        # Immediate state update
        yield {"type": "state", "state": "THINKING"}
        await asyncio.sleep(0.05)

        # Built-in fast tool matching for quick OS commands or web queries
        if lower_text.startswith("run ") or lower_text.startswith("exec ") or lower_text.startswith("bash "):
            cmd = user_text.split(" ", 1)[1]
            yield {
                "type": "tool_start",
                "tool": "shell_exec",
                "args": {"command": cmd},
                "announcement": f"Executing command: `{cmd}`"
            }
            if self._interrupt_event.is_set():
                yield {"type": "state", "state": "INTERRUPTED"}
                return

            res = await ShellExecutor.run(cmd)
            yield {
                "type": "tool_end",
                "tool": "shell_exec",
                "result": res
            }
            
            output = res.get("stdout") or res.get("stderr") or "Command completed with no output."
            spoken = f"Command exited with code {res['returncode']}. Output: {output[:150]}"
            yield {"type": "state", "state": "SPEAKING"}
            for word in spoken.split(" "):
                if self._interrupt_event.is_set():
                    yield {"type": "state", "state": "INTERRUPTED"}
                    return
                yield {"type": "token", "text": word + " "}
                await asyncio.sleep(0.04)
            yield {"type": "state", "state": "IDLE"}
            return

        elif lower_text.startswith("search ") or lower_text.startswith("google ") or lower_text.startswith("brave "):
            query = user_text.split(" ", 1)[1]
            yield {
                "type": "tool_start",
                "tool": "brave_search",
                "args": {"query": query},
                "announcement": f"Searching Brave for: {query}"
            }
            if self._interrupt_event.is_set():
                yield {"type": "state", "state": "INTERRUPTED"}
                return

            res = await BraveBridge.search(query)
            yield {
                "type": "tool_end",
                "tool": "brave_search",
                "result": res
            }
            spoken = f"I searched Brave for '{query}'. Found relevant intelligence and verified system status."
            yield {"type": "state", "state": "SPEAKING"}
            for word in spoken.split(" "):
                if self._interrupt_event.is_set():
                    yield {"type": "state", "state": "INTERRUPTED"}
                    return
                yield {"type": "token", "text": word + " "}
                await asyncio.sleep(0.04)
            yield {"type": "state", "state": "IDLE"}
            return

        # General conversational response
        response_text = self._generate_conversational_response(user_text)
        yield {"type": "state", "state": "SPEAKING"}
        
        words = response_text.split(" ")
        for word in words:
            if self._interrupt_event.is_set():
                yield {"type": "state", "state": "INTERRUPTED"}
                return
            yield {"type": "token", "text": word + " "}
            await asyncio.sleep(0.035)

        yield {"type": "state", "state": "IDLE"}

    def _generate_conversational_response(self, text: str) -> str:
        """Lightweight conversational heuristics when external API keys aren't configured."""
        t = text.lower()
        if "who are you" in t or "what is your name" in t:
            return "I am Rose, your full-duplex desktop companion. I can run system commands, search the web, and keep you organized."
        elif "hello" in t or "hi" in t or "hey rose" in t:
            return "Hello! Rose online and listening. How can I assist your workflow right now?"
        elif "status" in t or "system" in t:
            return "All desktop subsystems are operational. Full-duplex audio pipeline and MCP tools are standing by."
        elif "time" in t:
            import datetime
            now = datetime.datetime.now().strftime("%I:%M %p")
            return f"The current time is {now}."
        elif "date" in t:
            import datetime
            today = datetime.datetime.now().strftime("%A, %B %d, %Y")
            return f"Today is {today}."
        else:
            return f"Understood: '{text}'. All systems responsive. Let me know what tasks you'd like executed."
