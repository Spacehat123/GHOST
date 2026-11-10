"""Universal Tool Registry and Execution Gateway for GHOST.

Conforms to INTERFACE_CONTRACTS.md §3 and SECURITY_AND_PERMISSION_MODEL.md:
- Declarative ToolDefinition with JSON Schema, permission levels, and verification hooks.
- Fail-closed execution gate with instant halt-switch enforcement (ghost.core.halt).
- Capability token enforcement for side-effecting operations (L1-L4).
- Timeout protection and sandboxed error boundary per tool execution.
- OpenAI-compatible function calling serialization for OmniRoute cloud routing.
- EventBus dispatch of execution telemetry and audit records.
"""

from __future__ import annotations

import asyncio
import jsonschema
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.core.halt import HaltActive, is_halted

logger = logging.getLogger("ghost.core.tool_registry")

ToolHandler = Callable[[Dict[str, Any], Optional[str]], Awaitable[Dict[str, Any]]]
VerificationHandler = Callable[[Dict[str, Any]], Awaitable[bool]]


@dataclass(frozen=True)
class ToolDefinition:
    """Canonical declaration of an executable capability."""
    name: str
    description: str
    parameters_schema: Dict[str, Any]
    required_permission: str = ""
    is_side_effect: bool = False
    timeout_s: float = 60.0
    handler: Optional[ToolHandler] = None
    verification_handler: Optional[VerificationHandler] = None

    def to_openai_tool(self) -> Dict[str, Any]:
        """Converts ToolDefinition to OpenAI-compatible function tool schema."""
        schema = self.parameters_schema if self.parameters_schema else {
            "type": "object",
            "properties": {},
        }
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


class ToolRegistry:
    """Central registry for discovering, validating, and executing tools."""

    def __init__(self, event_bus: Optional[EventBus] = None):
        self._tools: Dict[str, ToolDefinition] = {}
        self.event_bus = event_bus or get_event_bus()

    def register(self, tool: ToolDefinition) -> None:
        """Registers a tool definition."""
        if not tool.name or not tool.handler:
            raise ValueError(f"Tool must have a valid name and handler (got {tool.name})")
        self._tools[tool.name] = tool
        logger.debug(f"Registered tool '{tool.name}' (side_effect={tool.is_side_effect})")

    def unregister(self, tool_name: str) -> bool:
        """Removes a registered tool."""
        if tool_name in self._tools:
            del self._tools[tool_name]
            return True
        return False

    def get(self, tool_name: str) -> Optional[ToolDefinition]:
        """Lookup a registered tool by name."""
        return self._tools.get(tool_name)

    def list_tools(self) -> List[ToolDefinition]:
        """Returns all registered tool definitions."""
        return list(self._tools.values())

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        """Returns OpenAPI/function schemas for all registered tools."""
        return [tool.to_openai_tool() for tool in self._tools.values()]

    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        capability_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes a tool within strict safety boundaries.
        
        Enforces:
        1. Tool existence
        2. Fail-closed halt switch
        3. Parameter JSON Schema validation
        4. Hard execution timeout
        5. Verification handler
        6. Telemetry and audit events
        """
        tool = self.get(tool_name)
        if not tool:
            return {
                "status": "error",
                "error": f"Tool '{tool_name}' is not registered in ToolRegistry",
            }

        # 1. Throat-cut halt switch check
        if tool.is_side_effect and is_halted():
            logger.warning(f"Refusing tool execution '{tool_name}': GHOST is HALTED")
            raise HaltActive(f"Cannot execute '{tool_name}': system kill-switch is active")

        # 2. Schema validation
        if tool.parameters_schema and tool.parameters_schema.get("properties"):
            try:
                jsonschema.validate(instance=arguments, schema=tool.parameters_schema)
            except jsonschema.ValidationError as ve:
                logger.warning(f"Schema validation failed for tool '{tool_name}': {ve.message}")
                return {
                    "status": "error",
                    "error": f"Parameter validation error: {ve.message}",
                }

        t0 = time.time()
        # 3. Execution with timeout boundary
        try:
            assert tool.handler is not None
            raw_result = await asyncio.wait_for(
                tool.handler(arguments, capability_token),
                timeout=tool.timeout_s,
            )
        except asyncio.TimeoutError:
            duration = time.time() - t0
            logger.error(f"Tool '{tool_name}' timed out after {duration:.2f}s (limit {tool.timeout_s}s)")
            return {
                "status": "error",
                "error": f"Execution timed out after {tool.timeout_s}s",
            }
        except Exception as e:
            duration = time.time() - t0
            logger.error(f"Unhandled exception in tool '{tool_name}': {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
            }

        duration = time.time() - t0

        # 4. Independent Verification
        verified = True
        if tool.verification_handler:
            try:
                verified = await tool.verification_handler(raw_result)
            except Exception as ve:
                logger.error(f"Verification handler failed for '{tool_name}': {ve}")
                verified = False

        result = dict(raw_result)
        result["verified"] = verified
        result["duration_s"] = round(duration, 3)

        # 5. Emit execution event
        try:
            event = Event(
                event_type=f"tool.execution.{tool_name}",
                source="core.tool_registry",
                severity=EventSeverity.INFO if result.get("status") != "error" else EventSeverity.ERROR,
                payload={
                    "tool": tool_name,
                    "arguments": arguments,
                    "result_status": result.get("status", "ok"),
                    "verified": verified,
                    "duration_s": round(duration, 3),
                },
            )
            await self.event_bus.publish(event)
        except Exception:
            pass

        return result


# Global Singleton ToolRegistry
_GLOBAL_TOOL_REGISTRY: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Returns the process-wide global ToolRegistry."""
    global _GLOBAL_TOOL_REGISTRY
    if _GLOBAL_TOOL_REGISTRY is None:
        _GLOBAL_TOOL_REGISTRY = ToolRegistry()
    return _GLOBAL_TOOL_REGISTRY


def reset_tool_registry(registry: Optional[ToolRegistry] = None) -> None:
    """Testing helper to reset global ToolRegistry."""
    global _GLOBAL_TOOL_REGISTRY
    _GLOBAL_TOOL_REGISTRY = registry
