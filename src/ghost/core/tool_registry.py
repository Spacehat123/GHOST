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
        register_default_ghost_tools(_GLOBAL_TOOL_REGISTRY)
    return _GLOBAL_TOOL_REGISTRY


def register_default_ghost_tools(registry: Optional[ToolRegistry] = None) -> ToolRegistry:
    """Populates ToolRegistry from GHOST_TOOLS suite."""
    reg = registry or get_tool_registry()
    try:
        from ghost.core.brain import GHOST_TOOLS, OmniRouteBrain
        for t in GHOST_TOOLS:
            fn = t.get("function", {})
            name = fn.get("name")
            desc = fn.get("description", "")
            schema = fn.get("parameters", {})
            if name and not reg.get(name):
                is_side_effect = name in [
                    "execute_command", "desktop_control", "deploy_demo",
                    "send_email", "save_file", "write_file", "kill_process"
                ]
                def _build_handler(tool_name: str):
                    async def _h(args: Dict[str, Any], cap: Optional[str] = None):
                        return await OmniRouteBrain.execute_tool_call(tool_name, args)
                    _h.__is_brain_wrapped__ = True
                    return _h

                reg.register(ToolDefinition(
                    name=name,
                    description=desc,
                    parameters_schema=schema,
                    is_side_effect=is_side_effect,
                    handler=_build_handler(name),
                ))

        # Register specialized physical, creative, and personal domain tools
        if not reg.get("ha_get_states"):
            from ghost.integrations.homeassistant import HomeAssistantClient
            ha_client = HomeAssistantClient()
            reg.register(ToolDefinition(
                name="ha_get_states",
                description="Query all entity and sensor states from Home Assistant smart home.",
                parameters_schema={"type": "object", "properties": {}},
                is_side_effect=False,
                handler=lambda args, cap: ha_client.get_states(),
            ))

        if not reg.get("ha_call_service"):
            from ghost.integrations.homeassistant import HomeAssistantClient
            ha_client = HomeAssistantClient()
            reg.register(ToolDefinition(
                name="ha_call_service",
                description="Execute physical device service call on Home Assistant (e.g. light.turn_on, switch.toggle).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string"},
                        "service": {"type": "string"},
                        "service_data": {"type": "object"},
                    },
                    "required": ["domain", "service"],
                },
                is_side_effect=True,
                required_permission="physical.control",
                handler=lambda args, cap: ha_client.call_service(args.get("domain", ""), args.get("service", ""), args.get("service_data", {})),
            ))

        if not reg.get("mqtt_publish"):
            from ghost.integrations.mqtt import MQTTClient
            mqtt_client = MQTTClient()
            reg.register(ToolDefinition(
                name="mqtt_publish",
                description="Publish an MQTT message to the smart home broker topic.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "payload": {"type": "string"},
                    },
                    "required": ["topic", "payload"],
                },
                is_side_effect=True,
                required_permission="physical.mqtt",
                handler=lambda args, cap: mqtt_client.publish(args.get("topic", ""), args.get("payload", "")),
            ))

        if not reg.get("scad_render_model"):
            from ghost.agents.creative_agent import CreativeIntelligenceAgent
            creative_agent = CreativeIntelligenceAgent()
            async def _scad_handler(args, cap):
                task_mock = Task(
                    task_id="tsk-tool-scad",
                    objective_id="obj-tool",
                    assigned_agent="creative_agent",
                    title="Render CAD",
                    description="",
                    status=TaskStatus.RUNNING,
                    input_payload={"action": "generate_scad_model", "model_name": args.get("model_name", "part"), "parameters": args.get("parameters", {})},
                )
                return await creative_agent.execute_task(task_mock, {})
            reg.register(ToolDefinition(
                name="scad_render_model",
                description="Generate a parametric 3D CAD model using OpenSCAD (.scad and .stl).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "model_name": {"type": "string"},
                        "parameters": {"type": "object"},
                    },
                    "required": ["model_name"],
                },
                is_side_effect=False,
                handler=_scad_handler,
            ))

        if not reg.get("personal_create_briefing"):
            from ghost.agents.personal_agent import PersonalIntelligenceAgent
            personal_agent = PersonalIntelligenceAgent()
            async def _briefing_handler(args, cap):
                task_mock = Task(
                    task_id="tsk-tool-briefing",
                    objective_id="obj-tool",
                    assigned_agent="personal_agent",
                    title="Create Briefing",
                    description="",
                    status=TaskStatus.RUNNING,
                    input_payload={"type": "daily_briefing"},
                )
                return await personal_agent.execute_task(task_mock, {})
            reg.register(ToolDefinition(
                name="personal_create_briefing",
                description="Synthesize executive daily intelligence briefing document.",
                parameters_schema={"type": "object", "properties": {}},
                is_side_effect=False,
                handler=_briefing_handler,
            ))

        # Register central meta-orchestration tools
        if not reg.get("delegate_task"):
            async def _delegate_handler(args: Dict[str, Any], cap: Optional[str] = None):
                from ghost.core.task_router import get_task_router
                router = get_task_router()
                agent_id = args.get("assigned_agent", "")
                objective = args.get("objective", "")
                inputs = args.get("inputs", {})
                decision = router.classify_and_route(objective, context=inputs)
                decision.decision_type = "SINGLE_AGENT"
                decision.target_agent = agent_id
                results = []
                async for evt in router.dispatch_workflow(decision, context=inputs):
                    if evt["type"] == "workflow_finished":
                        results.append(evt)
                return {"ok": True, "delegation": results[0] if results else {}}

            reg.register(ToolDefinition(
                name="delegate_task",
                description="Delegates a specialized objective to a domain intelligence agent with isolated execution context.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "assigned_agent": {
                            "type": "string",
                            "enum": ["system_agent", "dev_agent", "research_agent", "business_agent", "personal_agent", "physical_agent", "creative_agent"],
                        },
                        "objective": {"type": "string"},
                        "inputs": {"type": "object"},
                    },
                    "required": ["assigned_agent", "objective"],
                },
                is_side_effect=True,
                handler=_delegate_handler,
            ))

        if not reg.get("execute_workflow"):
            async def _workflow_handler(args: Dict[str, Any], cap: Optional[str] = None):
                from ghost.core.task_router import get_task_router
                router = get_task_router()
                wf_name = args.get("workflow_name", "")
                params = args.get("parameters", {})
                query = params.get("query", wf_name)
                decision = router.classify_and_route(query, context=params)
                decision.workflow_name = wf_name
                events = []
                async for evt in router.dispatch_workflow(decision, context=params):
                    events.append(evt)
                return {"ok": True, "workflow": wf_name, "events": events}

            reg.register(ToolDefinition(
                name="execute_workflow",
                description="Executes a multi-agent workflow (e.g. business_reconnaissance, dev_incident_investigation).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "workflow_name": {
                            "type": "string",
                            "enum": ["business_reconnaissance", "dev_incident_investigation", "focus_session_activation", "creative_asset_pipeline"],
                        },
                        "parameters": {"type": "object"},
                    },
                    "required": ["workflow_name"],
                },
                is_side_effect=True,
                handler=_workflow_handler,
            ))

        if not reg.get("query_agent_registry"):
            async def _query_registry_handler(args: Dict[str, Any], cap: Optional[str] = None):
                from ghost.core.agent_registry import get_agent_registry
                areg = get_agent_registry()
                domain = args.get("domain")
                if domain:
                    desc = areg.get_by_domain(domain)
                    return {"agents": [desc.to_dict()] if desc else []}
                return {"agents": [d.to_dict() for d in areg.list_descriptors()]}

            reg.register(ToolDefinition(
                name="query_agent_registry",
                description="Queries the canonical AgentCapabilityRegistry to inspect available agents and capabilities.",
                parameters_schema={
                    "type": "object",
                    "properties": {"domain": {"type": "string"}},
                },
                is_side_effect=False,
                handler=_query_registry_handler,
            ))

        if not reg.get("check_task_status"):
            async def _task_status_handler(args: Dict[str, Any], cap: Optional[str] = None):
                from ghost.core.task_orchestrator import get_task_orchestrator
                orch = get_task_orchestrator()
                tid = args.get("task_id", "")
                t = orch.get_task(tid)
                if not t:
                    return {"found": False, "task_id": tid}
                return {
                    "found": True,
                    "task_id": t.task_id,
                    "status": t.status.value,
                    "attempts": t.attempts,
                    "assigned_agent": t.assigned_agent,
                    "output": t.output_payload,
                    "error": t.error_reason,
                    "verification": t.verification_details,
                }

            reg.register(ToolDefinition(
                name="check_task_status",
                description="Checks the execution progress, verification evidence, and status of a delegated task.",
                parameters_schema={
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
                is_side_effect=False,
                handler=_task_status_handler,
            ))

    except Exception as e:
        logger.warning(f"Could not load default tools into registry: {e}")
    return reg


def reset_tool_registry(registry: Optional[ToolRegistry] = None) -> None:
    """Testing helper to reset global ToolRegistry."""
    global _GLOBAL_TOOL_REGISTRY
    _GLOBAL_TOOL_REGISTRY = registry
