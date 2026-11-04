"""Unit and Contract Tests for Strict Tool Argument Schema Validation.
Enforces non-negotiable architectural rule:
Never execute guessed or silently repaired arguments. Validate parsed JSON strictly against the tool's JSON Schema.
"""
import unittest
from ghost.core.validator import ToolArgumentValidator

class TestToolContracts(unittest.TestCase):

    def test_missing_required_parameter_rejected(self):
        """Missing required parameter must be rejected strictly."""
        # 'desktop_control' requires 'action'
        is_valid, parsed, err = ToolArgumentValidator.validate("desktop_control", "{}")
        self.assertFalse(is_valid)
        self.assertIsNone(parsed)
        self.assertIn("Missing required parameter 'action'", err)

        # 'git_action' requires 'command'
        is_valid, parsed, err = ToolArgumentValidator.validate("git_action", '{"repo_dir": "/tmp"}')
        self.assertFalse(is_valid)
        self.assertIsNone(parsed)
        self.assertIn("Missing required parameter 'command'", err)

    def test_malformed_json_rejected(self):
        """Malformed JSON must return structured error, never silently repaired or guessed."""
        malformed = '{"action": "focus_window", target: broken_unquoted}'
        is_valid, parsed, err = ToolArgumentValidator.validate("desktop_control", malformed)
        self.assertFalse(is_valid)
        self.assertIsNone(parsed)
        self.assertIn("Malformed JSON", err)

    def test_non_object_json_rejected(self):
        """Array or primitive JSON root must be rejected strictly."""
        is_valid, parsed, err = ToolArgumentValidator.validate("desktop_control", '["focus_window"]')
        self.assertFalse(is_valid)
        self.assertIsNone(parsed)
        self.assertIn("must be a JSON object", err)

        is_valid, parsed, err = ToolArgumentValidator.validate("desktop_control", '"focus_window"')
        self.assertFalse(is_valid)
        self.assertIsNone(parsed)
        self.assertIn("must be a JSON object", err)

    def test_type_mismatch_rejected(self):
        """Type mismatches (e.g. int instead of string, bool instead of int) must be rejected."""
        # action must be string, not integer
        is_valid, parsed, err = ToolArgumentValidator.validate("desktop_control", '{"action": 123}')
        self.assertFalse(is_valid)
        self.assertIsNone(parsed)
        self.assertIn("must be a string", err)

        # boolean is subclass of int in Python, must not be accepted for integer fields
        is_valid, parsed, err = ToolArgumentValidator.validate("input_simulation", '{"type": "click", "x": true}')
        self.assertFalse(is_valid)
        self.assertIsNone(parsed)
        self.assertIn("must be an integer", err)

    def test_invalid_enum_rejected(self):
        """Values not present in defined enum must be rejected."""
        is_valid, parsed, err = ToolArgumentValidator.validate("desktop_control", '{"action": "hack_mainframe"}')
        self.assertFalse(is_valid)
        self.assertIsNone(parsed)
        self.assertIn("is not one of allowed values", err)

    def test_valid_arguments_accepted(self):
        """Legitimate schema-compliant parameters pass validation without mutation."""
        valid_json = '{"action": "focus_window", "target": "Brave"}'
        is_valid, parsed, err = ToolArgumentValidator.validate("desktop_control", valid_json)
        self.assertTrue(is_valid)
        self.assertIsNone(err)
        self.assertEqual(parsed, {"action": "focus_window", "target": "Brave"})

    def test_unrecognized_tool_rejected(self):
        """Tool not in schema specification is rejected."""
        is_valid, parsed, err = ToolArgumentValidator.validate("non_existent_tool_xyz", "{}")
        self.assertFalse(is_valid)
        self.assertIsNone(parsed)
        self.assertIn("Unrecognized tool", err)

if __name__ == "__main__":
    unittest.main()
