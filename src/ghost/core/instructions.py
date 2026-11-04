"""Five-Tier Instruction Hierarchy & Deterministic Instruction Compliance Gate for Ghost Autonomous Engine.
Runs immediately before tool execution to deterministically enforce safety invariants,
firewalls, tactical authorizations, and task scope boundaries.
"""
import os
import re
import logging
from typing import Dict, Any, Optional, Tuple, List
from enum import IntEnum

logger = logging.getLogger("ghost.instructions")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
ALLOWED_WORKSPACES = [
    REPO_DIR,
    os.path.expanduser("~/projects"),
    "/tmp",
    os.environ.get("GHOST_WORKSPACE_OVERRIDE", "")
]
ALLOWED_WORKSPACES = [os.path.abspath(p) for p in ALLOWED_WORKSPACES if p]

class InstructionTier(IntEnum):
    TIER_1_SAFETY_FIREWALL = 1     # Zero auto-send, persona firewall, no external deletions
    TIER_2_USER_EXPLICIT_TASK = 2  # Current user prompt goal and direct task constraints
    TIER_3_CORE_DIRECTIVES = 3     # Persistent memory directives in configs/directives.json
    TIER_4_PROJECT_CONSTRAINTS = 4 # Project rules (no invented facts, relative paths, fail closed)
    TIER_5_INTERNAL_PLANS = 5      # Agent-generated scratch steps and plans

class InstructionComplianceGate:
    """
    Deterministic pre-execution gate.
    Evaluates proposed tool call and arguments before execution.
    If a rule is violated, execution is blocked immediately without calling the bridge.
    """

    FORBIDDEN_COMMAND_PATTERNS = [
        r"rm\s+(-[a-zA-Z]*[rf][a-zA-Z]*)\s+(/|~|\$HOME|/home)(\s|/|$)",
        r"mkfs",
        r"dd\s+if=.*of=/dev/",
        r":\(\)\s*\{\s*:\|:&\s*\};:", # Fork bomb
        r">\s*/dev/sd[a-z]",
        r"chmod\s+-R\s+777\s+/",
    ]

    @classmethod
    def is_path_within_allowed_workspace(cls, path_str: str) -> bool:
        if not path_str:
            return True
        try:
            abs_path = os.path.abspath(os.path.expanduser(path_str))
            for allowed in ALLOWED_WORKSPACES:
                if abs_path == allowed or abs_path.startswith(allowed.rstrip("/") + "/"):
                    return True
            return False
        except Exception:
            return False

    @classmethod
    def check_pre_execution(
        cls,
        tool_name: str,
        arguments: Dict[str, Any],
        task_context: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Evaluates proposed tool call.
        Returns:
            (allowed: bool, violation_dict_or_None: Optional[Dict])
        """
        # Tier 1 Rule: Zero Auto-Send (Ghost Prepares, Pranav Sends)
        # Block direct email pitch dispatch or automated external mail dispatches
        if tool_name in ["email_send_pitch", "email_send", "send_email", "smtp_send"]:
            return False, {
                "ok": False,
                "status": "blocked",
                "tool": tool_name,
                "action": arguments.get("action", "send"),
                "error": "Instruction Compliance Gate Violation: Automated external email dispatch is strictly prohibited. Outbound pitches must be saved as DRAFTS in the review queue for operator approval.",
                "verified": False,
                "compliance_rule": "RULE_ZERO_AUTO_SEND",
                "tier": InstructionTier.TIER_1_SAFETY_FIREWALL.value
            }

        # Check browser action attempting direct external message submission
        if tool_name == "browser_action":
            action = arguments.get("action", "")
            target = str(arguments.get("target", "")).lower()
            val = str(arguments.get("value", "")).lower()
            if action in ["click_and_type", "type", "click"] and any(s in target for s in ["send-button", "submit-outreach", "whatsapp-send"]):
                # If target is explicitly sending external outreach without operator confirmation
                if task_context and not task_context.get("operator_authorized"):
                    return False, {
                        "ok": False,
                        "status": "blocked",
                        "tool": tool_name,
                        "action": action,
                        "error": "Instruction Compliance Gate Violation: Headless external message sending requires explicit operator authorization.",
                        "verified": False,
                        "compliance_rule": "RULE_ZERO_AUTO_SEND",
                        "tier": InstructionTier.TIER_1_SAFETY_FIREWALL.value
                    }

        # Tier 1 Rule: Destructive operations outside workspace
        if tool_name == "desktop_control":
            action = arguments.get("action", "")
            if action == "delete_file":
                target_path = arguments.get("target", "")
                if not cls.is_path_within_allowed_workspace(target_path):
                    return False, {
                        "ok": False,
                        "status": "blocked",
                        "tool": tool_name,
                        "action": action,
                        "error": f"Instruction Compliance Gate Violation: Deleting file outside authorized workspace is blocked: '{target_path}'.",
                        "verified": False,
                        "compliance_rule": "RULE_NO_DESTRUCTIVE_OUTSIDE_WORKSPACE",
                        "tier": InstructionTier.TIER_1_SAFETY_FIREWALL.value
                    }

        # Check system_run commands for dangerous patterns
        if tool_name == "system_run":
            cmd = arguments.get("command", "")
            for pat in cls.FORBIDDEN_COMMAND_PATTERNS:
                if re.search(pat, cmd, re.IGNORECASE):
                    return False, {
                        "ok": False,
                        "status": "blocked",
                        "tool": tool_name,
                        "action": "system_run",
                        "error": f"Instruction Compliance Gate Violation: Destructive command pattern blocked: '{cmd}'.",
                        "verified": False,
                        "compliance_rule": "RULE_DESTRUCTIVE_COMMAND_BLOCKED",
                        "tier": InstructionTier.TIER_1_SAFETY_FIREWALL.value
                    }

        # Tier 2 & Tier 4 Rules: Task context & explicit scope boundaries
        if task_context:
            disallowed_tools = task_context.get("disallowed_tools", [])
            if tool_name in disallowed_tools:
                return False, {
                    "ok": False,
                    "status": "blocked",
                    "tool": tool_name,
                    "action": arguments.get("action", ""),
                    "error": f"Instruction Compliance Gate Violation: Tool '{tool_name}' is disallowed for the current active task scope.",
                    "verified": False,
                    "compliance_rule": "RULE_TASK_SCOPE_VIOLATION",
                    "tier": InstructionTier.TIER_2_USER_EXPLICIT_TASK.value
                }

            if task_context.get("read_only", False) and tool_name in [
                "desktop_control", "system_run", "git_action", "crm_update_client", "deploy_demo_vercel"
            ]:
                # If tool modifies state in read_only mode
                action = arguments.get("action") or arguments.get("command") or ""
                if action in ["move_file", "copy_file", "delete_file", "commit", "push"] or tool_name in ["crm_update_client", "deploy_demo_vercel", "system_run"]:
                    return False, {
                        "ok": False,
                        "status": "blocked",
                        "tool": tool_name,
                        "action": action,
                        "error": f"Instruction Compliance Gate Violation: Task is strictly read-only. Modifying tool '{tool_name}' is blocked.",
                        "verified": False,
                        "compliance_rule": "RULE_READ_ONLY_MODE",
                        "tier": InstructionTier.TIER_2_USER_EXPLICIT_TASK.value
                    }

        return True, None
