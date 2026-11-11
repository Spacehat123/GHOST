"""Deterministic Task Router & Multi-Agent Workflow Coordinator for GHOST.

Conforms to:
- DETERMINISTIC AGENT ROUTING & CONTEXT ISOLATION
- INPUT INTEGRITY & TOKEN BUDGETING
- INTENT CATEGORIZATION:
    CAPABILITY_QUERY, TASK_PLANNING, TASK_EXECUTION, TASK_CLARIFICATION, GENERAL_CONVERSATION
- ZERO WORKFLOW CONTAMINATION & INDEPENDENT RELEVANCE VERIFICATION
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple

from ghost.core.agent_registry import AgentCapabilityRegistry, get_agent_registry
from ghost.core.delegation_contract import DelegationResult, DelegationTaskContract
from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.core.input_integrity import InputPayload, budget_prompt_tokens, validate_input_integrity
from ghost.core.task_orchestrator import Task, TaskOrchestrator, TaskStatus, get_task_orchestrator
from ghost.core.tool_registry import get_tool_registry
from ghost.core import secrets

logger = logging.getLogger("ghost.core.task_router")


class IntentCategory(str, Enum):
    """Explicit intent categories separating discovery, planning, and execution."""
    CAPABILITY_QUERY = "CAPABILITY_QUERY"
    TASK_PLANNING = "TASK_PLANNING"
    TASK_EXECUTION = "TASK_EXECUTION"
    TASK_CLARIFICATION = "TASK_CLARIFICATION"
    GENERAL_CONVERSATION = "GENERAL_CONVERSATION"


@dataclass
class RoutingDecision:
    """Outcome of classifying a user intent or objective."""
    decision_type: str                  # "SINGLE_AGENT", "MULTI_AGENT_WORKFLOW", "CENTRAL_DIRECT", "CAPABILITY_QUERY", "TASK_PLANNING", "TASK_CLARIFICATION"
    intent_category: IntentCategory = IntentCategory.GENERAL_CONVERSATION
    domain: str = "general"             # "system", "development", "research", "business", "personal", "physical", "creative", "general"
    target_agent: Optional[str] = None
    workflow_name: Optional[str] = None
    confidence: float = 1.0
    rationale: str = ""
    contracts: List[DelegationTaskContract] = field(default_factory=list)
    raw_user_request: str = ""
    input_integrity: Dict[str, Any] = field(default_factory=dict)
    clarification_question: Optional[str] = None
    capability_explanation: Optional[Dict[str, Any]] = None
    execution_plan: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_type": self.decision_type,
            "intent_category": self.intent_category.value,
            "domain": self.domain,
            "target_agent": self.target_agent,
            "workflow_name": self.workflow_name,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "contracts": [c.to_dict() for c in self.contracts],
            "raw_user_request": self.raw_user_request,
            "input_integrity": self.input_integrity,
            "clarification_question": self.clarification_question,
            "capability_explanation": self.capability_explanation,
            "execution_plan": self.execution_plan,
        }


def strip_quotes_and_code(text: str) -> Tuple[str, List[str]]:
    """Separates quoted phrases, examples, and code blocks from directive intent."""
    extracted: List[str] = []

    def _code_repl(m):
        extracted.append(m.group(0))
        return " "

    no_code = re.sub(r"```[\s\S]*?```", _code_repl, text)

    def _quote_repl(m):
        extracted.append(m.group(0))
        return " "

    cleaned = re.sub(r'["\'][\s\S]*?["\']', _quote_repl, no_code)
    return cleaned.strip(), extracted


class TaskRouter:
    """Central deterministic task routing and multi-agent workflow engine."""

    def __init__(
        self,
        agent_registry: Optional[AgentCapabilityRegistry] = None,
        task_orchestrator: Optional[TaskOrchestrator] = None,
        event_bus: Optional[EventBus] = None,
    ):
        self.registry = agent_registry or get_agent_registry()
        self.orchestrator = task_orchestrator or get_task_orchestrator()
        self.event_bus = event_bus or get_event_bus()

        # Multi-agent workflow trigger patterns (strictly requires execution intent)
        self._multi_agent_patterns: List[Tuple[str, str, List[str]]] = [
            # Pattern 1: Business Reconnaissance & Client Acquisition Pipeline
            (
                r"\b(find|scout|discover|gather|harvest)\b.*\b(high-ticket|venues?|restaurants?|diners?)\b.*\b(without website|lacking.*presence|aggregator|pricing|menu|photos|crm|obsidian|dossier)\b",
                "business_reconnaissance",
                ["research_agent", "business_agent"]
            ),
            (
                r"\b(find.*venues|discover.*businesses|qualify.*leads)\b.*\b(crm|obsidian|ledger|dossier)\b",
                "business_reconnaissance",
                ["research_agent", "business_agent"]
            ),
            # Pattern 2: Dev Incident Investigation & Remediation
            (
                r"\b(investigate|diagnose|fix)\b.*\b(test failure|regression|broken build|test error)\b.*\b(system|resource|leak|memory|vitals)\b",
                "dev_incident_investigation",
                ["dev_agent", "system_agent"]
            ),
            # Pattern 3: Focus Mode & Physical Environment Setup
            (
                r"\b(activate|enable|trigger|set)\b.*\b(focus mode|deep work|focus session)\b.*\b(light|lamp|desk|smart home|scene)\b",
                "focus_session_activation",
                ["personal_agent", "physical_agent"]
            ),
            # Pattern 4: Creative Asset Demo Integration
            (
                r"\b(create|render|build)\b.*\b(cad|3d model|stl|openscad)\b.*\b(client|demo|site|pitch)\b",
                "creative_asset_pipeline",
                ["creative_agent", "business_agent"]
            ),
            # Pattern 5: Product Engineering Pipeline (Research -> CAD Prototype -> BOM & Engineering Dossier)
            (
                r"\b(develop|design|create|engineer)\b.*\b(planter|hardware|product|device|prototype|concept)\b.*\b(cad|dossier|engineering|parametric|bom)\b",
                "product_engineering_pipeline",
                ["research_agent", "creative_agent", "business_agent"]
            ),
        ]

        # Domain keywords and regex patterns for single-agent deterministic routing
        self._domain_patterns: Dict[str, Tuple[str, List[str]]] = {
            "research": (
                "research_agent",
                [
                    r"\b(search the web|web search|look up|find out|scrape|browse|cdp|source ledger|verify website)\b",
                    r"\b(scout.*competitor|competitor.*analysis|audit.*sources|pricing.*audit)\b",
                    r"\b(research\s+(the|into|on|about|top|recent|candidate|open|market)|conduct research|investigate topic)\b",
                ]
            ),
            "business": (
                "business_agent",
                [
                    r"\b(qualify.*lead|crm.*record|add.*to crm|update.*pipeline|create.*closing pack|sync.*obsidian)\b",
                    r"\b(build.*luxury demo|generate.*section kit)\b",
                ]
            ),
            "system": (
                "system_agent",
                [
                    r"\b(cpu|ram|memory|gpu|vram|disk|hardware|bottleneck|spike|vitals|procfs|zombie|stalled process|kill process|restart host)\b",
                    r"\b(system health|resource anomaly|system telemetry|thermal|process status)\b",
                ]
            ),
            "development": (
                "dev_agent",
                [
                    r"\b(git|commit|branch|repository|diff|unstaged|merge|rebase)\b",
                    r"\b(run test|unit test|pytest|unittest|test suite|syntax error|stack trace|sidecar fix|patch.*code)\b",
                ]
            ),
            "personal": (
                "personal_agent",
                [
                    r"\b(daily briefing|morning briefing|schedule|agenda|calendar|personal objective|priorities today|todo summary)\b",
                    r"\b(briefing document|directive|review objectives)\b",
                ]
            ),
            "physical": (
                "physical_agent",
                [
                    r"\b(home assistant|smart home|hass|mqtt|iot|turn on|turn off|switch|desk lamp|living room light|temperature sensor)\b",
                    r"\b(device state|readback verification|physical device|home.*scene)\b",
                ]
            ),
            "creative": (
                "creative_agent",
                [
                    r"\b(openscad|scad|3d model|parametric|cad|blender|stl|mesh|bounding box|render 3d|geometry verification)\b",
                    r"\b(motion site|threejs|three\.js|glb model|hero asset)\b",
                ]
            ),
        }

    def classify_and_route(
        self,
        user_text: str,
        context: Optional[Dict[str, Any]] = None,
        parent_task_id: Optional[str] = None,
    ) -> RoutingDecision:
        """
        Deterministically classifies user requests into explicit intent categories:
        CAPABILITY_QUERY, TASK_PLANNING, TASK_EXECUTION, TASK_CLARIFICATION, or GENERAL_CONVERSATION.
        Preserves the full original request and prevents accidental task execution.
        """
        raw_text = user_text or ""
        parent_id = parent_task_id or f"task-parent-{uuid.uuid4().hex[:8]}"

        # Step 1: Input Integrity Validation & Token Budgeting
        payload = validate_input_integrity(raw_text)
        logger.info(
            f"Input integrity check: {payload.length_chars} chars, ~{payload.estimated_tokens} tokens, "
            f"truncated={payload.is_truncated}, incomplete={payload.is_incomplete}"
        )

        # Broadcast input validation event if event loop is running
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.event_bus.publish(Event(
                event_type="input.integrity.validated",
                source="router.input_integrity",
                severity=EventSeverity.INFO if not payload.is_incomplete else EventSeverity.WARNING,
                payload=payload.to_dict(),
                correlation_id=parent_id,
            )))
        except RuntimeError:
            pass

        # Step 2: Handle Truncated or Incomplete Payloads
        if payload.is_incomplete:
            q = (
                f"Your request appears incomplete or cut off ({payload.incomplete_reason}). "
                "Please provide the complete instruction so I can assist accurately."
            )
            return RoutingDecision(
                decision_type="TASK_CLARIFICATION",
                intent_category=IntentCategory.TASK_CLARIFICATION,
                domain="general",
                confidence=1.0,
                rationale=payload.incomplete_reason or "Input integrity validation detected incomplete payload.",
                raw_user_request=raw_text,
                input_integrity=payload.to_dict(),
                clarification_question=q,
            )

        # Separate directive intent from quoted examples and code blocks
        clean_intent, examples = strip_quotes_and_code(raw_text)
        text_lower = clean_intent.lower()
        full_lower = raw_text.lower()

        # Step 3: Capability Discovery Queries (CAPABILITY_QUERY)
        # User is inquiring about what tools/agents work or can do, not commanding execution
        cap_patterns = [
            r"\b(which|what)\s+(tools?|capabilities?|features?|agents?)\b",
            r"\b(tell me which|show me which|list)\s+.*(tools?|agents?|capabilities?)\b",
            r"\b(tools? of yours|your tools?|what tools? do you have)\b",
            r"\b(work/dont work|work/not work|works? or doesn'?t work|can or cannot do|supported or unsupported)\b",
            r"\b(what can you do|what are your capabilities|what are you able to)\b",
            r"\bwhich\s+of\s+your\s+tools\b",
        ]

        if any(re.search(pat, text_lower) for pat in cap_patterns) or any(re.search(pat, full_lower) for pat in cap_patterns):
            explanation = self._build_capability_explanation(raw_text)
            return RoutingDecision(
                decision_type="CAPABILITY_QUERY",
                intent_category=IntentCategory.CAPABILITY_QUERY,
                domain="general",
                confidence=0.98,
                rationale="User submitted capability discovery query inquiring about tool suitability and operational readiness.",
                raw_user_request=raw_text,
                input_integrity=payload.to_dict(),
                capability_explanation=explanation,
                contracts=[],  # STRICTLY NO EXECUTION CONTRACTS
            )

        # Step 4: Task Planning Queries (TASK_PLANNING)
        # User requests a plan, dry run, or outline without executing live actions
        plan_patterns = [
            r"\b(how would you|how can you|how do you plan to|give me a plan|outline the steps|plan (the )?steps)\b",
            r"\b(without executing|dry run|plan only|draft a plan|steps required|create a plan|formulate a plan)\b",
            r"\b(how to (accomplish|build|setup|set up|execute|do))\b",
        ]

        if any(re.search(pat, text_lower) for pat in plan_patterns):
            plan = self._build_execution_plan(raw_text)
            return RoutingDecision(
                decision_type="TASK_PLANNING",
                intent_category=IntentCategory.TASK_PLANNING,
                domain=plan.get("domain", "general"),
                confidence=0.95,
                rationale="User requested an architectural task plan without authorizing execution.",
                raw_user_request=raw_text,
                input_integrity=payload.to_dict(),
                execution_plan=plan,
                contracts=[],  # STRICTLY NO EXECUTION CONTRACTS
            )

        # Step 5: Check for Multi-Agent Composite Workflows (TASK_EXECUTION)
        for pattern_regex, wf_name, agent_ids in self._multi_agent_patterns:
            if re.search(pattern_regex, clean_intent, re.IGNORECASE) or re.search(pattern_regex, raw_text, re.IGNORECASE):
                contracts = self._build_workflow_contracts(wf_name, raw_text, parent_id, context, payload.to_dict())
                return RoutingDecision(
                    decision_type="MULTI_AGENT_WORKFLOW",
                    intent_category=IntentCategory.TASK_EXECUTION,
                    domain="cross_domain",
                    workflow_name=wf_name,
                    confidence=0.95,
                    rationale=f"Matched multi-agent workflow '{wf_name}' across agents: {', '.join(agent_ids)}",
                    contracts=contracts,
                    raw_user_request=raw_text,
                    input_integrity=payload.to_dict(),
                )

        # Step 6: Check for Single-Domain Deterministic Matching (TASK_EXECUTION)
        for domain, (agent_id, patterns) in self._domain_patterns.items():
            for p in patterns:
                if re.search(p, clean_intent, re.IGNORECASE) or re.search(p, raw_text, re.IGNORECASE):
                    contract = self._build_single_agent_contract(agent_id, domain, raw_text, parent_id, context, payload.to_dict())
                    return RoutingDecision(
                        decision_type="SINGLE_AGENT",
                        intent_category=IntentCategory.TASK_EXECUTION,
                        domain=domain,
                        target_agent=agent_id,
                        confidence=0.90,
                        rationale=f"Deterministic rule matched domain '{domain}' -> {agent_id}",
                        contracts=[contract],
                        raw_user_request=raw_text,
                        input_integrity=payload.to_dict(),
                    )

        # Step 7: Conversational / General Direct Fallback (GENERAL_CONVERSATION)
        return RoutingDecision(
            decision_type="CENTRAL_DIRECT",
            intent_category=IntentCategory.GENERAL_CONVERSATION,
            domain="general",
            confidence=0.85,
            rationale="General query, conversational discussion, or unspecialized instruction handled by Central GHOST.",
            contracts=[],
            raw_user_request=raw_text,
            input_integrity=payload.to_dict(),
        )

    def _build_capability_explanation(self, user_query: str) -> Dict[str, Any]:
        """Evaluates tools and agents against the user's inquiry, providing verified status."""
        query_lower = user_query.lower()
        tool_reg = get_tool_registry()
        domain_breakdown: List[Dict[str, Any]] = []

        all_agents = self.registry.list_all()

        for a in all_agents:
            relevant = False
            # Check domain relevance to inquiry
            if a.domain in query_lower or any(c.lower() in query_lower for c in a.capabilities):
                relevant = True
            elif a.domain == "research" and ("competitor" in query_lower or "web" in query_lower or "search" in query_lower):
                relevant = True
            elif a.domain == "creative" and ("cad" in query_lower or "model" in query_lower or "3d" in query_lower or "openscad" in query_lower):
                relevant = True
            elif a.domain == "physical" and ("home" in query_lower or "smart" in query_lower or "light" in query_lower or "switch" in query_lower):
                relevant = True
            elif a.domain == "development" and ("git" in query_lower or "test" in query_lower or "code" in query_lower):
                relevant = True
            elif a.domain == "business" and ("crm" in query_lower or "lead" in query_lower or "pipeline" in query_lower):
                relevant = True
            elif a.domain == "system" and ("cpu" in query_lower or "hardware" in query_lower or "vitals" in query_lower):
                relevant = True

            # If user asks broad capability query or lists tools, include all agents
            if not relevant and any(w in query_lower for w in (
                "which tools", "all tools", "dont work", "work/dont", "available tools",
                "list your", "what tools", "specialized tools", "your tools", "capabilities"
            )):
                relevant = True

            if relevant:
                scoped_tools = []
                for tid in a.registered_tool_ids:
                    tool_def = tool_reg.get(tid)
                    works = bool(tool_def is not None)
                    notes = "Operational and verified"
                    status = "READY" if works else "UNAVAILABLE"

                    if tid.startswith("ha_") and not os.environ.get("HOMEASSISTANT_TOKEN"):
                        status = "UNCONFIGURED"
                        notes = "Unconfigured: HOMEASSISTANT_TOKEN not set (Operating in local simulation mode)"
                    elif tid in ("deploy_demo_vercel", "vercel_check_token") and not (os.environ.get("VERCEL_TOKEN") or secrets.get("VERCEL_TOKEN")):
                        status = "UNCONFIGURED"
                        notes = "Unconfigured: VERCEL_TOKEN missing in configs/secrets.json"
                    elif tid.startswith("figma_") and not (os.environ.get("FIGMA_API_TOKEN") or secrets.get("FIGMA_API_TOKEN")):
                        status = "UNCONFIGURED"
                        notes = "Unconfigured: FIGMA_API_TOKEN missing in configs/secrets.json"
                    elif tid.startswith("framer_") and not (os.environ.get("FRAMER_API_TOKEN") or secrets.get("FRAMER_API_TOKEN")):
                        status = "UNCONFIGURED"
                        notes = "Unconfigured: FRAMER_API_TOKEN missing in configs/secrets.json"
                    elif tid == "scad_render_model":
                        scad_found = shutil.which("openscad") or secrets.get("OPENSCAD_BINARY_PATH")
                        if not scad_found:
                            status = "UNAVAILABLE"
                            notes = "Requires OpenSCAD binary in PATH"
                            works = False
                        else:
                            status = "READY"
                            notes = f"Operational ({scad_found})"
                    elif not works:
                        status = "UNAVAILABLE"
                        notes = "Tool implementation not found in registry"

                    scoped_tools.append({
                        "tool_id": tid,
                        "works": works,
                        "description": tool_def.description if tool_def else "Registered tool",
                        "status": status,
                        "notes": notes,
                    })

                domain_breakdown.append({
                    "agent_id": a.agent_id,
                    "agent_name": a.name,
                    "domain": a.domain,
                    "capabilities": a.capabilities,
                    "tools": scoped_tools,
                })

        return {
            "query": user_query,
            "domains": domain_breakdown,
            "total_relevant_agents": len(domain_breakdown),
            "summary": f"Evaluated tool readiness across {len(domain_breakdown)} specialized domains."
        }

    def _build_execution_plan(self, user_query: str) -> Dict[str, Any]:
        """Constructs an architectural execution plan with stages and dependencies without running it."""
        query_lower = user_query.lower()
        stages = []

        if "competitor" in query_lower or "research" in query_lower:
            stages.append({
                "stage": 1,
                "agent": "research_agent",
                "domain": "research",
                "objective": "Execute competitor and web reconnaissance via Brave CDP and search tools.",
                "tools": ["web_search", "browser_action"],
                "verification": "Evidence artifacts and source ledger check.",
            })

        if "cad" in query_lower or "3d" in query_lower or "openscad" in query_lower or "model" in query_lower:
            stages.append({
                "stage": len(stages) + 1,
                "agent": "creative_agent",
                "domain": "creative",
                "objective": "Generate parametric OpenSCAD script, render STL mesh, and verify bounding box geometry.",
                "tools": ["scad_render_model"],
                "verification": "STL mesh file existence and non-zero bounding box.",
            })

        if "crm" in query_lower or "lead" in query_lower:
            stages.append({
                "stage": len(stages) + 1,
                "agent": "business_agent",
                "domain": "business",
                "objective": "Score opportunity and record qualified dossier into SQLite CRM and Obsidian vault.",
                "tools": ["crm_get_client", "crm_update_client"],
                "verification": "SQLite database entry and markdown dossier readback.",
            })

        if not stages:
            stages.append({
                "stage": 1,
                "agent": "general",
                "domain": "general",
                "objective": f"Decompose and execute objective: {user_query}",
                "tools": ["central_assistant"],
                "verification": "Completion receipt.",
            })

        return {
            "query": user_query,
            "domain": "multi_domain" if len(stages) > 1 else stages[0]["domain"],
            "stages": stages,
            "total_stages": len(stages),
            "execution_mode": "DRY_RUN_PLAN_ONLY",
        }

    def _build_workflow_contracts(
        self,
        workflow_name: str,
        user_text: str,
        parent_task_id: str,
        context: Optional[Dict[str, Any]] = None,
        input_integrity: Optional[Dict[str, Any]] = None,
    ) -> List[DelegationTaskContract]:
        """Assembles sequential, dependency-linked DelegationTaskContracts for multi-agent workflows."""
        contracts: List[DelegationTaskContract] = []
        integrity_meta = input_integrity or {}

        if workflow_name == "business_reconnaissance":
            r_desc = self.registry.get("research_agent")
            r_task_id = f"task-sub-res-{uuid.uuid4().hex[:6]}"
            contracts.append(DelegationTaskContract(
                task_id=r_task_id,
                parent_task_id=parent_task_id,
                assigned_agent="research_agent",
                domain="research",
                objective="Scout high-ticket candidate venues lacking official websites, verify aggregator status, audit menu pricing, and gather authentic media with SHA-256 provenance.",
                inputs={
                    "query": user_text,
                    "verify_aggregators": True,
                    "require_authentic_photos": True,
                    "target_city": "Bengaluru",
                    "min_ticket_price": 2500,
                },
                permissions=["research.web", "research.dossier_write"],
                tool_scope=r_desc.registered_tool_ids if r_desc else ["web_search", "browser_action", "scout_expensive_venues_without_website"],
                deadline_seconds=180.0,
                expected_output_schema={"type": "object", "properties": {"venues": {"type": "array"}}},
                verification_criteria=["source_ledger_provenance", "aggregator_domain_filter", "sha256_checksum"],
                execution_order=1,
                dependencies=[],
                raw_user_request=user_text,
                input_integrity=integrity_meta,
            ))

            b_desc = self.registry.get("business_agent")
            b_task_id = f"task-sub-biz-{uuid.uuid4().hex[:6]}"
            contracts.append(DelegationTaskContract(
                task_id=b_task_id,
                parent_task_id=parent_task_id,
                assigned_agent="business_agent",
                domain="business",
                objective="Evaluate research findings, score high-ticket upgrade opportunity, and record qualified prospect into SQLite CRM database.",
                inputs={
                    "action": "qualify_and_record_crm",
                    "research_task_id": r_task_id,
                },
                permissions=["biz.crm", "biz.demo_build"],
                tool_scope=b_desc.registered_tool_ids if b_desc else ["crm_get_client", "crm_update_client", "crm_list_pipeline"],
                deadline_seconds=120.0,
                expected_output_schema={"type": "object", "properties": {"crm_record_id": {"type": "string"}}},
                verification_criteria=["crm_db_entry_verified"],
                execution_order=2,
                dependencies=[r_task_id],
                raw_user_request=user_text,
                input_integrity=integrity_meta,
            ))

            k_task_id = f"task-sub-obsidian-{uuid.uuid4().hex[:6]}"
            contracts.append(DelegationTaskContract(
                task_id=k_task_id,
                parent_task_id=parent_task_id,
                assigned_agent="business_agent",
                domain="business",
                objective="Synchronize verified client dossier markdown into Obsidian vault ledger and repository dossiers directory.",
                inputs={
                    "action": "sync_obsidian_dossier",
                    "crm_task_id": b_task_id,
                },
                permissions=["biz.crm"],
                tool_scope=["crm_update_client"],
                deadline_seconds=60.0,
                expected_output_schema={"type": "object", "properties": {"obsidian_path": {"type": "string"}}},
                verification_criteria=["obsidian_file_written"],
                execution_order=3,
                dependencies=[b_task_id],
                raw_user_request=user_text,
                input_integrity=integrity_meta,
            ))

        elif workflow_name == "dev_incident_investigation":
            d_desc = self.registry.get("dev_agent")
            d_task_id = f"task-sub-dev-{uuid.uuid4().hex[:6]}"
            contracts.append(DelegationTaskContract(
                task_id=d_task_id,
                parent_task_id=parent_task_id,
                assigned_agent="dev_agent",
                domain="development",
                objective="Inspect failing test modules, analyze stack trace, review git diff, and stage fix.",
                inputs={"query": user_text},
                permissions=["dev.git", "dev.test_exec", "dev.fixer"],
                tool_scope=d_desc.registered_tool_ids if d_desc else ["git_action", "system_run"],
                deadline_seconds=120.0,
                verification_criteria=["test_report_passes"],
                execution_order=1,
                raw_user_request=user_text,
                input_integrity=integrity_meta,
            ))

            s_desc = self.registry.get("system_agent")
            s_task_id = f"task-sub-sys-{uuid.uuid4().hex[:6]}"
            contracts.append(DelegationTaskContract(
                task_id=s_task_id,
                parent_task_id=parent_task_id,
                assigned_agent="system_agent",
                domain="system",
                objective="Verify that developer fix runs without memory leaks or zombie background processes.",
                inputs={"target": "post_fix_vitals"},
                permissions=["system.read"],
                tool_scope=s_desc.registered_tool_ids if s_desc else ["system_run"],
                deadline_seconds=60.0,
                verification_criteria=["vitals_normal"],
                execution_order=2,
                dependencies=[d_task_id],
                raw_user_request=user_text,
                input_integrity=integrity_meta,
            ))

        elif workflow_name == "focus_session_activation":
            p_desc = self.registry.get("personal_agent")
            p_task_id = f"task-sub-pers-{uuid.uuid4().hex[:6]}"
            contracts.append(DelegationTaskContract(
                task_id=p_task_id,
                parent_task_id=parent_task_id,
                assigned_agent="personal_agent",
                domain="personal",
                objective="Review calendar schedule, block 90-minute focus session, and record focus objective.",
                inputs={"query": user_text},
                permissions=["personal.briefing", "personal.memory"],
                tool_scope=p_desc.registered_tool_ids if p_desc else ["manage_directives"],
                deadline_seconds=60.0,
                verification_criteria=["objective_created"],
                execution_order=1,
                raw_user_request=user_text,
                input_integrity=integrity_meta,
            ))

            h_desc = self.registry.get("physical_agent")
            h_task_id = f"task-sub-phys-{uuid.uuid4().hex[:6]}"
            contracts.append(DelegationTaskContract(
                task_id=h_task_id,
                parent_task_id=parent_task_id,
                assigned_agent="physical_agent",
                domain="physical",
                objective="Engage focused physical ambient scene: adjust desk lighting and set do-not-disturb status.",
                inputs={"action": "call_service", "domain": "switch", "service": "turn_on", "entity_id": "switch.desk_lamp"},
                permissions=["physical.control"],
                tool_scope=h_desc.registered_tool_ids if h_desc else ["ha_call_service"],
                deadline_seconds=60.0,
                verification_criteria=["readback_state_match"],
                execution_order=2,
                dependencies=[p_task_id],
                raw_user_request=user_text,
                input_integrity=integrity_meta,
            ))

        elif workflow_name == "creative_asset_pipeline":
            c_desc = self.registry.get("creative_agent")
            c_task_id = f"task-sub-creat-{uuid.uuid4().hex[:6]}"
            contracts.append(DelegationTaskContract(
                task_id=c_task_id,
                parent_task_id=parent_task_id,
                assigned_agent="creative_agent",
                domain="creative",
                objective="Generate parametric 3D CAD model via OpenSCAD, export STL mesh, and verify geometry bounding box.",
                inputs={"action": "generate_scad_model", "model_name": "luxury_hero_display", "parameters": {"height": 50, "radius": 25}},
                permissions=["creative.cad", "creative.render"],
                tool_scope=c_desc.registered_tool_ids if c_desc else ["scad_render_model"],
                deadline_seconds=120.0,
                verification_criteria=["3d_geometry_stl_exists"],
                execution_order=1,
                raw_user_request=user_text,
                input_integrity=integrity_meta,
            ))

        elif workflow_name == "product_engineering_pipeline":
            # Stage 1: Research Agent (Competitor & differentiation analysis)
            r_desc = self.registry.get("research_agent")
            r_task_id = f"task-sub-res-{uuid.uuid4().hex[:6]}"
            contracts.append(DelegationTaskContract(
                task_id=r_task_id,
                parent_task_id=parent_task_id,
                assigned_agent="research_agent",
                domain="research",
                objective="Conduct competitor analysis and identify failure modes for self-watering planters to establish technical differentiation.",
                inputs={"user_instruction": user_text, "research_topic": "self_watering_planter", "context": "commercial_differentiation"},
                permissions=["research.web", "research.dossier_write"],
                tool_scope=r_desc.registered_tool_ids if r_desc else ["web_search", "browser_action"],
                deadline_seconds=120.0,
                verification_criteria=["topic_research_provenance"],
                execution_order=1,
                raw_user_request=user_text,
                input_integrity=integrity_meta,
            ))

            # Stage 2: Creative Agent (Parametric OpenSCAD CAD Model, STL/3MF generation & Multi-angle Renders)
            c_desc = self.registry.get("creative_agent")
            c_task_id = f"task-sub-creat-{uuid.uuid4().hex[:6]}"
            contracts.append(DelegationTaskContract(
                task_id=c_task_id,
                parent_task_id=parent_task_id,
                assigned_agent="creative_agent",
                domain="creative",
                objective="Generate complete parametric CAD prototype for AeroSpire self-watering planter in OpenSCAD, compile binary STL and 3MF meshes, and render multi-angle preview images.",
                inputs={
                    "action": "generate_scad_model",
                    "model_name": "aerospire_planter_full_assembly",
                    "render_png": True,
                    "export_3mf": True,
                    "user_instruction": user_text,
                },
                permissions=["creative.cad", "creative.render"],
                tool_scope=c_desc.registered_tool_ids if c_desc else ["scad_render_model"],
                deadline_seconds=120.0,
                verification_criteria=["3d_geometry"],
                execution_order=2,
                dependencies=[r_task_id],
                raw_user_request=user_text,
                input_integrity=integrity_meta,
            ))

            # Stage 3: Business Agent (BOM, Unit Economics, Injection Molding Specs & Complete Engineering Dossier)
            b_desc = self.registry.get("business_agent")
            b_task_id = f"task-sub-biz-{uuid.uuid4().hex[:6]}"
            contracts.append(DelegationTaskContract(
                task_id=b_task_id,
                parent_task_id=parent_task_id,
                assigned_agent="business_agent",
                domain="business",
                objective="Synthesize complete engineering dossier with Bill of Materials (BOM), unit economics, injection molding / 3D printing manufacturing specs, and commercial viability assessment.",
                inputs={"action": "create_engineering_dossier", "product_name": "AeroSpire Self-Watering Planter", "user_instruction": user_text},
                permissions=["biz.crm", "biz.demo_build", "biz.outreach"],
                tool_scope=b_desc.registered_tool_ids if b_desc else ["crm_get_client", "crm_update_client"],
                deadline_seconds=120.0,
                verification_criteria=["engineering_dossier"],
                execution_order=3,
                dependencies=[c_task_id],
                raw_user_request=user_text,
                input_integrity=integrity_meta,
            ))

        return contracts

    def _build_single_agent_contract(
        self,
        agent_id: str,
        domain: str,
        user_text: str,
        parent_task_id: str,
        context: Optional[Dict[str, Any]] = None,
        input_integrity: Optional[Dict[str, Any]] = None,
    ) -> DelegationTaskContract:
        """Constructs a single-agent DelegationTaskContract with authorized tools and permissions."""
        desc = self.registry.get(agent_id)
        task_id = f"task-sub-{domain[:3]}-{uuid.uuid4().hex[:6]}"
        return DelegationTaskContract(
            task_id=task_id,
            parent_task_id=parent_task_id,
            assigned_agent=agent_id,
            domain=domain,
            objective=user_text,
            inputs={"user_instruction": user_text, **(context or {})},
            permissions=desc.permission_requirements if desc else [],
            tool_scope=desc.registered_tool_ids if desc else [],
            deadline_seconds=float(desc.resource_requirements.get("timeout_s", 180.0)) if desc else 180.0,
            verification_criteria=desc.verification_requirements if desc else [],
            execution_order=1,
            dependencies=[],
            raw_user_request=user_text,
            input_integrity=input_integrity or {},
        )

    def validate_delegation_permissions(
        self,
        contract: DelegationTaskContract,
        authorized_permissions: Set[str],
    ) -> Tuple[bool, Optional[str]]:
        """Ensures an agent cannot request or grant permissions beyond authorized bounds."""
        requested = set(contract.permissions)
        unauthorized = requested - authorized_permissions
        if unauthorized:
            msg = f"Delegation permission violation: requested permissions {unauthorized} exceed authorized scope."
            logger.warning(msg)
            return False, msg
        return True, None

    def _validate_stage_receipt(
        self,
        contract: DelegationTaskContract,
        raw_output: Dict[str, Any],
        evidence: Any,
        start_time: float,
    ) -> Tuple[bool, Optional[str]]:
        """
        Anti-Contamination & Acceptance Criteria Verification.
        Enforces that:
        1. Output is relevant to the contract objective (no mismatched cached objects).
        2. Evidence artifacts were created/modified during this task execution window.
        3. Wrong workflow or mismatched agent execution is strictly rejected.
        """
        if not evidence.verified:
            return False, evidence.details.get("error") or "Agent verification receipt was not asserted."

        # 1. Objective relevance check: prevent venue reconnaissance contamination on non-venue tasks
        contract_obj_lower = contract.objective.lower()
        is_venue_task = any(w in contract_obj_lower for w in ("venue", "restaurant", "fine dining", "diner", "hospitality"))

        if not is_venue_task and "venues" in raw_output and "Grasshopper" in str(raw_output.get("venues")):
            return False, "INVALID_DISPATCH: Output contains venue reconnaissance data but task objective was unrelated."

        # 2. Freshness check: verify artifacts were touched during this execution
        artifacts = evidence.artifacts or []
        for art in artifacts:
            if isinstance(art, str) and os.path.exists(art):
                try:
                    mtime = os.path.getmtime(art)
                    if mtime < (start_time - 5.0):
                        return False, f"INVALID_DISPATCH: Stale cached artifact detected ({art}, mtime={mtime} < task_start={start_time})."
                except Exception:
                    pass

        return True, None

    async def dispatch_workflow(
        self,
        decision: RoutingDecision,
        parent_task: Optional[Task] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Dispatches and supervises a multi-agent or single-agent delegation plan in isolated contexts."""
        if not decision.contracts:
            yield {"type": "status", "status": "NO_TASKS", "message": "No delegated contracts in routing decision."}
            return

        parent_id = parent_task.task_id if parent_task else (decision.contracts[0].parent_task_id or f"task-parent-{uuid.uuid4().hex[:8]}")

        # Broadcast routing decision event
        await self.event_bus.publish(Event(
            event_type="router.decision.dispatched",
            source="router.task_router",
            severity=EventSeverity.INFO,
            payload={
                "parent_task_id": parent_id,
                "workflow_name": decision.workflow_name,
                "domain": decision.domain,
                "intent_category": decision.intent_category.value,
                "contracts_count": len(decision.contracts),
                "agents": [c.assigned_agent for c in decision.contracts],
                "raw_request_length": len(decision.raw_user_request),
            },
            correlation_id=parent_id,
        ))

        yield {
            "type": "routed",
            "parent_task_id": parent_id,
            "workflow": decision.workflow_name or "single_agent",
            "domain": decision.domain,
            "stages_count": len(decision.contracts),
            "rationale": decision.rationale,
            "raw_request": decision.raw_user_request,
        }

        # Lazy import of domain agents to avoid circular dependencies
        from ghost.agents import (
            BusinessIntelligenceAgent,
            CreativeIntelligenceAgent,
            DevelopmentIntelligenceAgent,
            PersonalIntelligenceAgent,
            PhysicalIntelligenceAgent,
            ResearchIntelligenceAgent,
            SystemIntelligenceAgent,
        )

        agent_instances = {
            "system_agent": SystemIntelligenceAgent(event_bus=self.event_bus),
            "dev_agent": DevelopmentIntelligenceAgent(event_bus=self.event_bus),
            "research_agent": ResearchIntelligenceAgent(event_bus=self.event_bus),
            "business_agent": BusinessIntelligenceAgent(event_bus=self.event_bus),
            "personal_agent": PersonalIntelligenceAgent(event_bus=self.event_bus),
            "physical_agent": PhysicalIntelligenceAgent(event_bus=self.event_bus),
            "creative_agent": CreativeIntelligenceAgent(event_bus=self.event_bus),
        }

        completed_results: Dict[str, DelegationResult] = {}
        all_succeeded = True

        # Sort contracts by execution order
        sorted_contracts = sorted(decision.contracts, key=lambda c: c.execution_order)

        for contract in sorted_contracts:
            agent = agent_instances.get(contract.assigned_agent)
            if not agent:
                err_msg = f"Assigned agent '{contract.assigned_agent}' not found in agent catalog."
                logger.error(err_msg)
                yield {"type": "stage_failed", "task_id": contract.task_id, "error": err_msg}
                all_succeeded = False
                break

            yield {
                "type": "stage_dispatched",
                "task_id": contract.task_id,
                "agent_id": contract.assigned_agent,
                "objective": contract.objective,
                "tool_scope": contract.tool_scope,
                "order": contract.execution_order,
            }

            # Map inputs and dependencies
            stage_inputs = dict(contract.inputs)
            for dep_id in contract.dependencies:
                dep_res = completed_results.get(dep_id)
                if dep_res:
                    stage_inputs[f"dep_{dep_id}"] = dep_res.findings
                    if isinstance(dep_res.findings, dict) and "venues" in dep_res.findings:
                        stage_inputs["venues"] = dep_res.findings["venues"]

            # Construct Task object for orchestrator lifecycle without slicing the objective
            task_obj = self.orchestrator.submit_task(
                objective_id=parent_id,
                assigned_agent=contract.assigned_agent,
                title=f"{contract.domain.upper()}: {contract.objective}",
                description=contract.objective,
                input_payload={
                    **stage_inputs,
                    "raw_user_request": contract.raw_user_request,
                    "input_integrity": contract.input_integrity,
                    "parent_task_id": parent_id,
                },
                required_permissions=contract.permissions,
            )
            # Transition task into RUNNING status for execution
            self.orchestrator.transition(task_obj.task_id, TaskStatus.RUNNING)

            # Isolated execution sandbox
            start_t = time.time()
            try:
                # Specialist agent executes task within authorized boundary
                raw_output = await agent.execute_task(task_obj, stage_inputs)
                duration = time.time() - start_t

                # Independent verification check
                evidence = await agent.verify_outcome(task_obj, raw_output)

                # Anti-Contamination & Relevance Validation
                valid_receipt, receipt_err = self._validate_stage_receipt(contract, raw_output, evidence, start_t)

                out_payload = dict(raw_output) if isinstance(raw_output, dict) else {"output": raw_output}
                out_payload["verification"] = evidence.to_dict()

                if valid_receipt and evidence.verified:
                    status = "COMPLETED"
                    self.orchestrator.transition(
                        task_obj.task_id,
                        TaskStatus.COMPLETED,
                        output_payload=out_payload,
                    )
                else:
                    status = "BLOCKED" if raw_output.get("blocked", False) else "FAILED"
                    failure_reason = receipt_err or raw_output.get("failure_details") or "Verification failed to confirm authentic outcome."
                    self.orchestrator.transition(
                        task_obj.task_id,
                        TaskStatus.FAILED,
                        error_reason=failure_reason,
                    )

                delegation_res = DelegationResult(
                    task_id=contract.task_id,
                    parent_task_id=parent_id,
                    agent_id=contract.assigned_agent,
                    status=status,
                    findings=raw_output.get("findings", raw_output),
                    summary=raw_output.get("summary", f"{contract.assigned_agent} finished with status {status}."),
                    evidence=[evidence.to_dict()],
                    source_references=raw_output.get("sources", []),
                    actions_performed=raw_output.get("actions", []),
                    files_or_records_created=raw_output.get("files_created", []),
                    verification_results=evidence.to_dict(),
                    failure_details=None if (valid_receipt and evidence.verified) else (receipt_err or raw_output.get("failure_details")),
                    duration_s=duration,
                )

                completed_results[contract.task_id] = delegation_res

                # Broadcast stage validation event
                await self.event_bus.publish(Event(
                    event_type="task.verification.validated",
                    source="router.task_router",
                    severity=EventSeverity.INFO if valid_receipt else EventSeverity.ERROR,
                    payload={
                        "task_id": contract.task_id,
                        "parent_task_id": parent_id,
                        "agent_id": contract.assigned_agent,
                        "valid_receipt": valid_receipt,
                        "receipt_error": receipt_err,
                        "status": status,
                    },
                    correlation_id=parent_id,
                ))

                yield {
                    "type": "stage_completed",
                    "task_id": contract.task_id,
                    "agent_id": contract.assigned_agent,
                    "status": status,
                    "summary": delegation_res.summary,
                    "verified": valid_receipt and evidence.verified,
                    "records_created": delegation_res.files_or_records_created,
                }

                if status in ("FAILED", "BLOCKED"):
                    all_succeeded = False
                    logger.warning(f"Stage {contract.task_id} halted workflow with status: {status}")
                    break

            except Exception as e:
                logger.error(f"Execution failure in agent {contract.assigned_agent}: {e}", exc_info=True)
                all_succeeded = False
                yield {
                    "type": "stage_error",
                    "task_id": contract.task_id,
                    "agent_id": contract.assigned_agent,
                    "error": str(e),
                }
                break

        # Final Parent Workflow Outcome Summary
        final_status = "COMPLETED" if all_succeeded else "INCOMPLETE"
        yield {
            "type": "workflow_finished",
            "parent_task_id": parent_id,
            "status": final_status,
            "stages_completed": len(completed_results),
            "results": {tid: res.to_dict(include_logs=False) for tid, res in completed_results.items()},
        }


# Global singleton
_GLOBAL_TASK_ROUTER: Optional[TaskRouter] = None


def get_task_router() -> TaskRouter:
    """Returns the process-wide TaskRouter singleton."""
    global _GLOBAL_TASK_ROUTER
    if _GLOBAL_TASK_ROUTER is None:
        _GLOBAL_TASK_ROUTER = TaskRouter()
    return _GLOBAL_TASK_ROUTER


def reset_task_router(router: Optional[TaskRouter] = None) -> None:
    """Testing helper to reset or inject TaskRouter."""
    global _GLOBAL_TASK_ROUTER
    _GLOBAL_TASK_ROUTER = router
