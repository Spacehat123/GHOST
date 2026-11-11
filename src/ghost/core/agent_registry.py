"""Canonical Agent Capability Registry for GHOST.

Conforms to DETERMINISTIC AGENT ROUTING & CONTEXT ISOLATION:
- Programmatic discovery of all specialized agents across the 7 operational domains.
- Strict derivation of registered tool IDs from the actual ToolRegistry (zero invented IDs).
- Compact capability summaries for Central GHOST context without token bloat.
- Scoped tool schema delivery ensuring specialist agents receive ONLY their permitted tools.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

from ghost.core.tool_registry import ToolDefinition, ToolRegistry, get_tool_registry

logger = logging.getLogger("ghost.core.agent_registry")


@dataclass(frozen=True)
class AgentDescriptor:
    """Canonical declaration of a specialized domain agent's capabilities and boundaries."""
    agent_id: str
    name: str
    domain: str
    description: str
    supported_task_types: List[str]
    capabilities: List[str]
    registered_tool_ids: List[str]
    required_inputs: List[str]
    optional_inputs: List[str] = field(default_factory=list)
    permission_requirements: List[str] = field(default_factory=list)
    resource_requirements: Dict[str, Any] = field(default_factory=lambda: {"max_memory_mb": 512, "timeout_s": 180.0})
    output_schema: Dict[str, Any] = field(default_factory=dict)
    verification_requirements: List[str] = field(default_factory=list)
    health_status: str = "healthy"
    is_available: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AgentCapabilityRegistry:
    """Central registry holding canonical descriptors for all specialized intelligence agents."""

    def __init__(self, tool_registry: Optional[ToolRegistry] = None):
        self._tool_registry = tool_registry or get_tool_registry()
        self._agents: Dict[str, AgentDescriptor] = {}
        self._domain_map: Dict[str, str] = {}
        self._register_default_agents()

    def register(self, descriptor: AgentDescriptor) -> None:
        """Registers an agent descriptor, validating tool IDs against ToolRegistry."""
        # Validate that all registered tool IDs exist in ToolRegistry
        existing_tools = set(t.name for t in self._tool_registry.list_tools())
        invalid_tools = set(descriptor.registered_tool_ids) - existing_tools
        if invalid_tools:
            logger.warning(
                f"Agent '{descriptor.agent_id}' references unregistered tools: {invalid_tools}. "
                "Only registered tools will be available at runtime."
            )

        self._agents[descriptor.agent_id] = descriptor
        self._domain_map[descriptor.domain.lower()] = descriptor.agent_id
        logger.debug(f"Registered agent descriptor '{descriptor.agent_id}' ({descriptor.domain})")

    def get(self, agent_id: str) -> Optional[AgentDescriptor]:
        return self._agents.get(agent_id)

    def get_by_domain(self, domain: str) -> Optional[AgentDescriptor]:
        agent_id = self._domain_map.get(domain.lower())
        return self.get(agent_id) if agent_id else None

    def list_descriptors(self) -> List[AgentDescriptor]:
        return list(self._agents.values())

    def list_all(self) -> List[AgentDescriptor]:
        """Alias for list_descriptors."""
        return self.list_descriptors()

    def get_tools_for_agent(self, agent_id: str) -> List[Dict[str, Any]]:
        """Returns OpenAI-compatible tool schemas for ONLY the tools permitted for this agent."""
        descriptor = self.get(agent_id)
        if not descriptor:
            return []

        scoped_tools: List[Dict[str, Any]] = []
        for tool_name in descriptor.registered_tool_ids:
            tool_def = self._tool_registry.get(tool_name)
            if tool_def:
                scoped_tools.append(tool_def.to_openai_tool())
        return scoped_tools

    def get_compact_capability_summary(self) -> str:
        """Generates a compact, high-signal capability summary with live operational status (~350 tokens)."""
        import shutil
        from ghost.core import secrets
        scad_ok = bool(shutil.which("openscad") or secrets.get("OPENSCAD_BINARY_PATH"))

        lines = [
            "### SPECIALIZED DOMAIN AGENTS & CAPABILITY MAP",
            "Central GHOST delegates domain-specific work to specialized agents. Never execute domain tasks directly in central context.",
            "| Domain | Agent ID | Status | Core Capabilities | Supported Task Types |",
            "|---|---|---|---|---|",
        ]
        for desc in self._agents.values():
            caps = ", ".join(desc.capabilities[:3])
            tasks = ", ".join(desc.supported_task_types[:3])
            status = "READY"
            if desc.domain == "creative":
                status = "READY (OpenSCAD active)" if scad_ok else "UNAVAILABLE (OpenSCAD missing)"
            elif desc.domain == "physical":
                status = "READY (Simulated/Active)"
            lines.append(f"| **{desc.domain.capitalize()}** | `{desc.agent_id}` | `{status}` | {caps} | {tasks} |")
        lines.append("\nUse `delegate_task(assigned_agent, objective, inputs)` or `execute_workflow(workflow_name, parameters)` to dispatch work.")
        return "\n".join(lines)

    def _register_default_agents(self) -> None:
        """Registers canonical descriptors for all 7 intelligence domains."""
        # 1. System Intelligence Agent
        self.register(AgentDescriptor(
            agent_id="system_agent",
            name="System Intelligence Agent",
            domain="system",
            description="Monitors host vitals, hardware bottlenecks, sustained resource anomalies, and process health.",
            supported_task_types=["diagnose_hardware_spike", "cleanup_zombies", "verify_vitals", "process_management"],
            capabilities=["procfs telemetry readback", "killswitch verification", "memory/CPU profiling", "zombie cleanup"],
            registered_tool_ids=["system_run", "agent_desktop_command", "desktop_control", "media_control", "desktop_notify"],
            required_inputs=["objective_id"],
            optional_inputs=["target", "metric", "threshold"],
            permission_requirements=["system.read", "process.manage"],
            resource_requirements={"max_memory_mb": 256, "timeout_s": 60.0},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}, "findings": {"type": "array"}}},
            verification_requirements=["procfs_readback", "resource_normalization"],
        ))

        # 2. Development Intelligence Agent
        self.register(AgentDescriptor(
            agent_id="dev_agent",
            name="Development Intelligence Agent",
            domain="development",
            description="Inspects git repositories, diagnoses test suite failures, isolates regressions, and stages fixes.",
            supported_task_types=["diagnose_test_failure", "review_git_diff", "stage_fix", "run_test_suite"],
            capabilities=["git tracking", "unittest/pytest diagnostic", "sidecar patch verification", "syntax inspection"],
            registered_tool_ids=["git_action", "github_action", "database_query", "rea_action", "system_run"],
            required_inputs=["objective_id"],
            optional_inputs=["test_module", "failure_trace", "repo_path"],
            permission_requirements=["dev.git", "dev.test_exec", "dev.fixer"],
            resource_requirements={"max_memory_mb": 512, "timeout_s": 120.0},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}, "fixed": {"type": "boolean"}}},
            verification_requirements=["test_report", "git_clean_diff"],
        ))

        # 3. Research Intelligence Agent
        self.register(AgentDescriptor(
            agent_id="research_agent",
            name="Research Intelligence Agent",
            domain="research",
            description="Executes web reconnaissance, verifies digital presence vs aggregators, audits pricing, and gathers authentic media with SHA-256 provenance.",
            supported_task_types=["recon_prospect", "audit_sources", "compile_dossier", "venue_recon"],
            capabilities=["Brave CDP web recon", "aggregator vs official domain verification", "menu pricing audit", "SHA-256 photo provenance"],
            registered_tool_ids=["web_search", "browser_action", "scout_expensive_venues_without_website", "capture_screen"],
            required_inputs=["query"],
            optional_inputs=["category", "location", "target_count", "min_price"],
            permission_requirements=["research.web", "research.dossier_write"],
            resource_requirements={"max_memory_mb": 512, "timeout_s": 180.0},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}, "venues": {"type": "array"}}},
            verification_requirements=["source_ledger_provenance", "aggregator_domain_filter", "sha256_checksum"],
        ))

        # 4. Business Intelligence Agent
        self.register(AgentDescriptor(
            agent_id="business_agent",
            name="Business Intelligence Agent",
            domain="business",
            description="Evaluates lead qualification, calculates high-ticket upgrade opportunity, synchronizes SQLite CRM, and creates Obsidian dossiers.",
            supported_task_types=["qualify_lead", "crm_record", "build_luxury_demo", "prepare_closing_pack"],
            capabilities=["lead qualification", "CRM database tracking", "Obsidian vault bi-directional sync", "Section Kit demo building"],
            registered_tool_ids=["crm_get_client", "crm_update_client", "crm_list_pipeline", "qualify_venue_lead", "build_luxury_demo", "deploy_demo_vercel", "email_check_inbox"],
            required_inputs=["action"],
            optional_inputs=["slug", "opportunity_score", "client_data", "venue_research"],
            permission_requirements=["biz.crm", "biz.demo_build", "biz.outreach"],
            resource_requirements={"max_memory_mb": 512, "timeout_s": 300.0},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}, "crm_id": {"type": "string"}}},
            verification_requirements=["crm_db_entry", "obsidian_file_written", "qa_report"],
        ))

        # 5. Personal Intelligence Agent
        self.register(AgentDescriptor(
            agent_id="personal_agent",
            name="Personal Intelligence Agent",
            domain="personal",
            description="Synthesizes executive daily briefings, tracks persistent user objectives, and aligns task schedules.",
            supported_task_types=["daily_briefing", "objective_review", "schedule_sync"],
            capabilities=["executive markdown briefings", "directive tracking", "schedule rhythm monitoring"],
            registered_tool_ids=["personal_create_briefing", "manage_directives"],
            required_inputs=["objective_id"],
            optional_inputs=["briefing_date", "schedule_block"],
            permission_requirements=["personal.briefing", "personal.memory"],
            resource_requirements={"max_memory_mb": 256, "timeout_s": 60.0},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}, "briefing_file": {"type": "string"}}},
            verification_requirements=["briefing_file_written"],
        ))

        # 6. Physical Intelligence Agent
        self.register(AgentDescriptor(
            agent_id="physical_agent",
            name="Physical Intelligence Agent",
            domain="physical",
            description="Interacts with Home Assistant and MQTT smart home infrastructure with readback device state verification.",
            supported_task_types=["query_states", "call_service", "mqtt_publish", "verify_device_state"],
            capabilities=["Home Assistant REST/WS control", "MQTT topic messaging", "independent readback verification"],
            registered_tool_ids=["ha_get_states", "ha_call_service", "mqtt_publish"],
            required_inputs=["action"],
            optional_inputs=["entity_id", "service", "domain", "topic", "payload"],
            permission_requirements=["physical.control", "physical.mqtt"],
            resource_requirements={"max_memory_mb": 256, "timeout_s": 60.0},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}, "readback_state": {"type": "string"}}},
            verification_requirements=["readback_state_match"],
        ))

        # 7. Creative Intelligence Agent
        self.register(AgentDescriptor(
            agent_id="creative_agent",
            name="Creative Intelligence Agent",
            domain="creative",
            description="Generates parametric 3D CAD models via OpenSCAD and Blender with geometry verification.",
            supported_task_types=["generate_scad_model", "render_3d_mesh", "inspect_geometry"],
            capabilities=["OpenSCAD CSG code generation", "STL mesh export", "bounding box verification"],
            registered_tool_ids=[
                "scad_render_model", "glb_inspect", "glb_optimize", "find_glb", "download_glb",
                "generate_image_asset", "assess_3d_fit", "build_3d_site", "qa_3d_site",
                "build_motion_site", "qa_motion_site"
            ],
            required_inputs=["action", "model_name"],
            optional_inputs=["parameters", "target_format"],
            permission_requirements=["creative.cad", "creative.render"],
            resource_requirements={"max_memory_mb": 512, "timeout_s": 120.0},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}, "stl_path": {"type": "string"}}},
            verification_requirements=["3d_geometry_stl_exists"],
        ))


# Global singleton
_GLOBAL_AGENT_REGISTRY: Optional[AgentCapabilityRegistry] = None


def get_agent_registry() -> AgentCapabilityRegistry:
    """Returns the process-wide AgentCapabilityRegistry singleton."""
    global _GLOBAL_AGENT_REGISTRY
    if _GLOBAL_AGENT_REGISTRY is None:
        _GLOBAL_AGENT_REGISTRY = AgentCapabilityRegistry()
    return _GLOBAL_AGENT_REGISTRY


def reset_agent_registry(registry: Optional[AgentCapabilityRegistry] = None) -> None:
    """Testing helper to reset or inject AgentCapabilityRegistry."""
    global _GLOBAL_AGENT_REGISTRY
    _GLOBAL_AGENT_REGISTRY = registry
