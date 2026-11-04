"""Unit and Contract Tests for 5-Tier Instruction Hierarchy & Instruction Compliance Gate.
Enforces non-negotiable architectural rule:
The compliance gate must deterministically block unauthorized external actions, destructive operations,
and actions outside the approved task scope. It must run immediately before tool execution—not merely advise the model.
"""
import unittest
from ghost.core.instructions import InstructionComplianceGate, InstructionTier

class TestInstructions(unittest.TestCase):

    def test_block_unauthorized_external_email_send(self):
        """Automated external email dispatch must be deterministically blocked (RULE_ZERO_AUTO_SEND)."""
        allowed, violation = InstructionComplianceGate.check_pre_execution(
            tool_name="email_send_pitch",
            arguments={"recipient": "ceo@luxuryhotel.com", "subject": "Exclusive Demo Pitch"}
        )
        self.assertFalse(allowed)
        self.assertIsNotNone(violation)
        self.assertEqual(violation["compliance_rule"], "RULE_ZERO_AUTO_SEND")
        self.assertEqual(violation["tier"], InstructionTier.TIER_1_SAFETY_FIREWALL.value)
        self.assertEqual(violation["status"], "blocked")
        self.assertIn("Automated external email dispatch is strictly prohibited", violation["error"])

    def test_block_destructive_file_deletion_outside_workspace(self):
        """Deleting files outside authorized workspaces must be deterministically blocked."""
        # Deleting critical system file
        allowed, violation = InstructionComplianceGate.check_pre_execution(
            tool_name="desktop_control",
            arguments={"action": "delete_file", "target": "/etc/shadow"}
        )
        self.assertFalse(allowed)
        self.assertIsNotNone(violation)
        self.assertEqual(violation["compliance_rule"], "RULE_NO_DESTRUCTIVE_OUTSIDE_WORKSPACE")
        self.assertIn("Deleting file outside authorized workspace is blocked", violation["error"])

        # Deleting root
        allowed, violation = InstructionComplianceGate.check_pre_execution(
            tool_name="desktop_control",
            arguments={"action": "delete_file", "target": "/var/log/syslog"}
        )
        self.assertFalse(allowed)
        self.assertEqual(violation["compliance_rule"], "RULE_NO_DESTRUCTIVE_OUTSIDE_WORKSPACE")

    def test_block_destructive_system_commands(self):
        """Dangerous shell commands (e.g. rm -rf /, fork bomb) must be intercepted immediately."""
        dangerous_cmds = [
            "rm -rf /",
            "rm -rf ~",
            "rm -fr /home/pranc",
            ":(){ :|:& };:",
            "mkfs.ext4 /dev/sda1"
        ]
        for cmd in dangerous_cmds:
            allowed, violation = InstructionComplianceGate.check_pre_execution(
                tool_name="system_run",
                arguments={"command": cmd}
            )
            self.assertFalse(allowed, f"Command should have been blocked: {cmd}")
            self.assertEqual(violation["compliance_rule"], "RULE_DESTRUCTIVE_COMMAND_BLOCKED")

    def test_block_out_of_scope_tools(self):
        """Tool calls explicitly disallowed in task context must be blocked under Tier 2."""
        task_context = {"disallowed_tools": ["git_action", "deploy_demo_vercel"]}

        allowed, violation = InstructionComplianceGate.check_pre_execution(
            tool_name="deploy_demo_vercel",
            arguments={"slug": "apex-detailing"},
            task_context=task_context
        )
        self.assertFalse(allowed)
        self.assertEqual(violation["compliance_rule"], "RULE_TASK_SCOPE_VIOLATION")
        self.assertEqual(violation["tier"], InstructionTier.TIER_2_USER_EXPLICIT_TASK.value)

    def test_block_mutating_actions_in_read_only_mode(self):
        """Read-only task context deterministically blocks write, delete, commit, or deploy actions."""
        task_context = {"read_only": True}

        # Git commit blocked in read-only mode
        allowed, violation = InstructionComplianceGate.check_pre_execution(
            tool_name="git_action",
            arguments={"command": "commit", "message": "auto commit"},
            task_context=task_context
        )
        self.assertFalse(allowed)
        self.assertEqual(violation["compliance_rule"], "RULE_READ_ONLY_MODE")

        # Desktop delete blocked in read-only mode
        allowed, violation = InstructionComplianceGate.check_pre_execution(
            tool_name="desktop_control",
            arguments={"action": "delete_file", "target": "/tmp/test.txt"},
            task_context=task_context
        )
        self.assertFalse(allowed)
        self.assertEqual(violation["compliance_rule"], "RULE_READ_ONLY_MODE")

    def test_allow_legitimate_authorized_operations(self):
        """Safe operations within workspace and permitted tools pass compliance gate without hindrance."""
        # Safe window focus
        allowed, violation = InstructionComplianceGate.check_pre_execution(
            tool_name="desktop_control",
            arguments={"action": "focus_window", "target": "Brave"}
        )
        self.assertTrue(allowed)
        self.assertIsNone(violation)

        # Safe read-only file/system inspection
        allowed, violation = InstructionComplianceGate.check_pre_execution(
            tool_name="system_run",
            arguments={"command": "ls -la /tmp"}
        )
        self.assertTrue(allowed)
        self.assertIsNone(violation)

if __name__ == "__main__":
    unittest.main()
