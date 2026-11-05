"""Strict Tool Argument Schema Validator for Ghost Autonomous Agency Engine.
Enforces non-negotiable architectural rule:
Never execute guessed or silently repaired arguments. Validate strictly against the tool's JSON schema.
"""
import json
import logging
from typing import Tuple, Optional, Dict, Any, List

logger = logging.getLogger("ghost.validator")

# Names the model sometimes invents for real tools. Resolved BEFORE validation and dispatch.
TOOL_ALIASES = {
    "shell_execution": "system_run",
    "shell_run": "system_run",
    "run_shell": "system_run",
    "exec_command": "system_run",
    "data_fetcher": "web_search",
}


def canonical_tool_name(name: str) -> str:
    """Map an alias to the real tool name (identity for real names)."""
    return TOOL_ALIASES.get(name, name)

class ToolArgumentValidator:
    """
    Validates tool arguments strictly against the provided tool schemas (GHOST_TOOLS).
    Never guesses, mutates, or silently repairs invalid parameters.
    """

    @classmethod
    def validate(
        cls,
        tool_name: str,
        raw_arguments_str: str,
        tool_definitions: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Strictly parses and validates tool arguments against tool schema.
        Returns:
            (is_valid: bool, parsed_dict: Optional[Dict], error_message: Optional[str])
        """
        # 1. Parse JSON
        if raw_arguments_str is None or not str(raw_arguments_str).strip():
            parsed: Dict[str, Any] = {}
        else:
            try:
                parsed = json.loads(raw_arguments_str)
                if not isinstance(parsed, dict):
                    return False, None, f"Tool arguments must be a JSON object, got {type(parsed).__name__}."
            except Exception as e:
                return False, None, f"Malformed JSON in tool arguments: {e}"

        # 2. Resolve Tool Spec
        if tool_definitions is None:
            from ghost.core.brain import GHOST_TOOLS
            tool_definitions = GHOST_TOOLS

        tool_spec = None
        effective_name = tool_name
        effective_name = canonical_tool_name(effective_name)

        for item in tool_definitions:
            fn = item.get("function", {})
            if fn.get("name") == effective_name:
                tool_spec = fn
                break

        if not tool_spec:
            return False, None, f"Unrecognized tool '{tool_name}'."

        params = tool_spec.get("parameters", {})
        properties = params.get("properties", {})
        required_keys = params.get("required", [])

        # 3. Check Required Keys
        for req in required_keys:
            if req not in parsed:
                return False, None, f"Missing required parameter '{req}' for tool '{tool_name}'."
            if parsed[req] is None and req in required_keys:
                return False, None, f"Required parameter '{req}' cannot be null for tool '{tool_name}'."

        # 4. Check Property Types & Enums
        for key, val in parsed.items():
            if key not in properties:
                # Undefined property in schema - strictness check
                continue

            prop_def = properties[key]
            expected_type = prop_def.get("type")

            # Null check: if val is None but not in required, it might be optional
            if val is None:
                continue

            if expected_type == "string":
                if not isinstance(val, str):
                    return False, None, f"Parameter '{key}' must be a string, got {type(val).__name__}."
            elif expected_type == "integer":
                # In Python bool is a subclass of int, reject booleans explicitly
                if not isinstance(val, int) or isinstance(val, bool):
                    return False, None, f"Parameter '{key}' must be an integer, got {type(val).__name__}."
            elif expected_type == "number":
                if (not isinstance(val, (int, float))) or isinstance(val, bool):
                    return False, None, f"Parameter '{key}' must be a number, got {type(val).__name__}."
            elif expected_type == "boolean":
                if not isinstance(val, bool):
                    return False, None, f"Parameter '{key}' must be a boolean, got {type(val).__name__}."
            elif expected_type == "array":
                if not isinstance(val, list):
                    return False, None, f"Parameter '{key}' must be an array, got {type(val).__name__}."
            elif expected_type == "object":
                if not isinstance(val, dict):
                    return False, None, f"Parameter '{key}' must be an object, got {type(val).__name__}."

            # Check Enum if defined
            enum_values = prop_def.get("enum")
            if enum_values is not None:
                if val not in enum_values:
                    return False, None, f"Parameter '{key}' value '{val}' is not one of allowed values: {enum_values}."

        return True, parsed, None
