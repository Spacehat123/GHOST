"""Integration and unit tests for tool result normalization and vision handoff contract.

Verifies:
1. Every tool returns a consistent result contract:
   {"ok": bool, "status": "success"|"failed", "tool": str, "action": str, "error": str|None, "verified": bool}
2. Ghost never reports success when the underlying tool or bridge reports failure.
3. Screen capture handoff ensures data_uri is accessible at the top level and the actual
   image payload successfully reaches the model's chat completions messages.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from ghost.core.agent import GhostAgent
from ghost.core.brain import OmniRouteBrain


class TestToolNormalization(unittest.IsolatedAsyncioTestCase):
    """Verifies that all tool results conform strictly to the normalized contract."""

    async def test_normalize_browser_action_failure(self):
        """Verify that browser action failure (e.g. element not found) reports ok=False, status='failed'."""
        with patch("ghost.mcp.bridge.BraveCDPBridge.click_and_type", new_callable=AsyncMock) as mock_action:
            mock_action.return_value = {"success": False, "error": "Element not found on page"}

            res = await OmniRouteBrain.execute_tool_call(
                "browser_action",
                {"action": "click_and_type", "target": "#submit_btn"}
            )

            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "failed")
            self.assertEqual(res["tool"], "browser_action")
            self.assertEqual(res["action"], "click_and_type")
            self.assertEqual(res["error"], "Element not found on page")
            self.assertFalse(res["verified"])
            self.assertIn("result", res)

    async def test_normalize_browser_action_success(self):
        """Verify that browser action success reports ok=True, status='success', verified=True."""
        with patch("ghost.mcp.bridge.BraveCDPBridge.click_and_type", new_callable=AsyncMock) as mock_action:
            mock_action.return_value = {"success": True, "clicked": "#submit_btn", "typed": "hello"}

            res = await OmniRouteBrain.execute_tool_call(
                "browser_action",
                {"action": "click_and_type", "target": "#submit_btn", "value": "hello"}
            )

            self.assertTrue(res["ok"])
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["tool"], "browser_action")
            self.assertEqual(res["action"], "click_and_type")
            self.assertIsNone(res["error"])
            self.assertTrue(res["verified"])
            self.assertEqual(res.get("clicked"), "#submit_btn")

    async def test_normalize_desktop_action_failure(self):
        """Verify that desktop action failure reports ok=False, status='failed'."""
        with patch("ghost.mcp.bridge.AgentDesktopBridge.action", new_callable=AsyncMock) as mock_action:
            mock_action.return_value = {"ok": False, "error": {"message": "Accessibility node @btn_save not found"}}

            res = await OmniRouteBrain.execute_tool_call(
                "agent_desktop_command",
                {"action": "click", "target_ref": "@btn_save"}
            )

            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "failed")
            self.assertEqual(res["tool"], "agent_desktop_command")
            self.assertEqual(res["action"], "click")
            self.assertEqual(res["error"], "Accessibility node @btn_save not found")
            self.assertFalse(res["verified"])

    async def test_normalize_input_simulation_failure(self):
        """Verify that input simulation non-zero returncode reports ok=False, status='failed'."""
        with patch("ghost.mcp.bridge.ShellExecutor.run", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = {"success": False, "returncode": 1, "stdout": "", "stderr": "hyprctl repl socket closed"}

            res = await OmniRouteBrain.execute_tool_call(
                "input_simulation",
                {"type": "key", "keys": "Return"}
            )

            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "failed")
            self.assertEqual(res["tool"], "input_simulation")
            self.assertEqual(res["action"], "key")
            self.assertFalse(res["verified"])
            self.assertIn("hyprctl repl", res["error"])

    async def test_normalize_screen_capture_failure(self):
        """Verify that screen capture failure reports ok=False, status='failed', verified=False."""
        with patch("ghost.mcp.bridge.SystemAppBridge.capture_screen", new_callable=AsyncMock) as mock_cap:
            mock_cap.return_value = {"status": "error", "message": "Failed to capture screen with grim", "details": {}}

            res = await OmniRouteBrain.execute_tool_call(
                "capture_screen",
                {"region": "full"}
            )

            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "failed")
            self.assertEqual(res["tool"], "capture_screen")
            self.assertEqual(res["action"], "capture")
            self.assertEqual(res["error"], "Failed to capture screen with grim")
            self.assertFalse(res["verified"])
            self.assertIsNone(res.get("data_uri"))

    async def test_normalize_screen_capture_success(self):
        """Verify that screen capture success has data_uri directly at top level and verified=True."""
        sample_uri = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP=="
        with patch("ghost.mcp.bridge.SystemAppBridge.capture_screen", new_callable=AsyncMock) as mock_cap:
            mock_cap.return_value = {
                "status": "success",
                "path": "/tmp/ghost_screen.jpg",
                "data_uri": sample_uri,
                "region": "full",
                "size_bytes": 1024
            }

            res = await OmniRouteBrain.execute_tool_call(
                "capture_screen",
                {"region": "full"}
            )

            self.assertTrue(res["ok"])
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["tool"], "capture_screen")
            self.assertEqual(res["action"], "capture")
            self.assertIsNone(res["error"])
            self.assertTrue(res["verified"])
            self.assertEqual(res.get("data_uri"), sample_uri)
            self.assertEqual(res.get("path"), "/tmp/ghost_screen.jpg")

    async def test_normalize_nested_data_uri_lifted_to_top_level(self):
        """Verify that if a bridge returns data_uri nested under result, it is lifted to top level."""
        sample_uri = "data:image/jpeg;base64,/9j/NESTED_URI=="
        normalized = OmniRouteBrain.normalize_tool_result(
            tool_name="capture_screen",
            action="capture",
            raw_res={"status": "success", "result": {"data_uri": sample_uri, "path": "/tmp/nested.jpg"}}
        )

        self.assertTrue(normalized["ok"])
        self.assertEqual(normalized["status"], "success")
        self.assertEqual(normalized["data_uri"], sample_uri)

    async def test_execute_tool_call_handles_exception_truthfully(self):
        """Verify that an unhandled bridge exception returns ok=False, status='failed', not success."""
        with patch.object(OmniRouteBrain, "_dispatch_tool_call", side_effect=RuntimeError("CDP websocket broken")):
            res = await OmniRouteBrain.execute_tool_call("browser_action", {"action": "click_and_type"})

            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "failed")
            self.assertEqual(res["error"], "CDP websocket broken")
            self.assertFalse(res["verified"])

    async def test_git_passphrase_resolved_from_protected_config_never_model_args(self):
        """Verify that Git credentials come strictly from protected config/env, not model args."""
        import os
        from ghost.mcp.bridge import GitBridge

        # 1. Environment variable resolution
        with patch.dict(os.environ, {"GHOST_GIT_PASSPHRASE": "secure_env_pass_123"}, clear=False):
            passphrase = GitBridge.get_passphrase()
            self.assertEqual(passphrase, "secure_env_pass_123")

        # 2. Protected configuration file resolution (configs/git.json)
        with patch.dict(os.environ, {}, clear=True), \
             patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps({"ssh_passphrase": "file_secret_pass"}))), \
             patch("os.path.exists", return_value=True):
            passphrase = GitBridge.get_passphrase()
            self.assertEqual(passphrase, "file_secret_pass")

        # 3. Verify OmniRouteBrain.execute_tool_call does not pass model argument to run_git
        with patch("ghost.mcp.bridge.GitBridge.run_git", new_callable=AsyncMock) as mock_run_git:
            mock_run_git.return_value = {"success": True, "returncode": 0, "stdout": "On branch main"}

            # Model attempts to pass a malicious or hallucinated passphrase
            res = await OmniRouteBrain.execute_tool_call(
                "git_action",
                {"command": "status", "passphrase": "malicious_untrusted_input"}
            )

            self.assertTrue(res["ok"])
            # Verify run_git was called WITHOUT passphrase parameter from model arguments!
            mock_run_git.assert_called_once_with(args=["status"], repo_dir=unittest.mock.ANY)


class TestScreenCaptureVisionHandoffIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration test verifying screen capture image successfully reaches the model."""

    async def test_actual_image_reaches_model_in_chat_completions(self):
        """Verify that when capture_screen succeeds, the base64 image data URI reaches the completions create call."""
        agent = GhostAgent()
        sample_data_uri = "data:image/jpeg;base64,/9j/REAL_JPEG_PAYLOAD_FOR_VISION_TEST=="

        # Mock Completion Chunks
        class MockChoiceDelta:
            def __init__(self, tc_list=None, content=None):
                self.tool_calls = tc_list
                self.content = content

        class MockChoice:
            def __init__(self, delta):
                self.delta = delta

        class MockChunk:
            def __init__(self, choices=None, usage=None):
                self.choices = choices or []
                self.usage = usage

        class MockToolCall:
            def __init__(self, idx, call_id, name, args):
                self.index = idx
                self.id = call_id
                self.function = MagicMock()
                self.function.name = name
                self.function.arguments = args

        # Turn 1: Model emits tool_call for capture_screen
        mock_tc = MockToolCall(0, "call_cap_1", "capture_screen", json.dumps({"region": "full"}))
        turn1_chunks = [
            MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])
        ]

        # Turn 2: Model synthesizes answer after receiving vision image
        turn2_chunks = [
            MockChunk([MockChoice(MockChoiceDelta(content="I see the active workspace with Kitty terminal open, boss."))])
        ]

        captured_completions_calls = []

        class MockCompletions:
            async def create(self, **kwargs):
                captured_completions_calls.append(kwargs)
                call_index = len(captured_completions_calls)
                chunks = turn1_chunks if call_index == 1 else turn2_chunks
                async def _gen():
                    for c in chunks:
                        yield c
                return _gen()

        mock_client = MagicMock()
        mock_client.chat.completions = MockCompletions()

        # Mock SystemAppBridge.capture_screen to succeed with real sample data_uri
        with patch("ghost.core.brain.OmniRouteBrain.load_config"), \
             patch("ghost.core.brain.OmniRouteBrain.load_history"), \
             patch("ghost.core.brain.OmniRouteBrain._config", {"api_key": "test_key", "model": "test_model"}), \
             patch("ghost.core.brain.OmniRouteBrain.get_client", return_value=mock_client), \
             patch("ghost.core.brain.OmniRouteBrain.record_turn"), \
             patch("ghost.mcp.bridge.SystemAppBridge.capture_screen", new_callable=AsyncMock) as mock_screen:

            mock_screen.return_value = {
                "status": "success",
                "path": "/tmp/ghost_screen.jpg",
                "data_uri": sample_data_uri,
                "region": "full",
                "size_bytes": 4096
            }

            events = []
            async for ev in agent.ask_stream("Ghost, take a screenshot and tell me what you see."):
                events.append(ev)

            # Verification 1: Completions were called twice (turn 1 tool call, turn 2 response)
            self.assertEqual(len(captured_completions_calls), 2, "Expected 2 chat completions calls")

            # Verification 2: Check call 2 messages contain the user vision block with the exact data_uri
            call2_messages = captured_completions_calls[1]["messages"]

            # Locate the vision user message appended after the tool response
            vision_messages = [
                m for m in call2_messages
                if m.get("role") == "user" and isinstance(m.get("content"), list)
            ]
            self.assertTrue(len(vision_messages) >= 1, "Expected user vision message block in call 2 messages")

            vision_content = vision_messages[0]["content"]
            image_blocks = [
                b for b in vision_content
                if isinstance(b, dict) and b.get("type") == "image_url"
            ]
            self.assertEqual(len(image_blocks), 1, "Expected exactly 1 image_url block in vision content")

            # Verification 3: The actual image data URI reached the model!
            passed_image_url = image_blocks[0]["image_url"]["url"]
            self.assertEqual(
                passed_image_url,
                sample_data_uri,
                f"Expected image URI {sample_data_uri} to reach model, got {passed_image_url}"
            )

    async def test_screen_capture_failure_does_not_attach_vision_payload(self):
        """Verify that if capture_screen fails, no invalid image_url block is sent to the model."""
        agent = GhostAgent()

        class MockChoiceDelta:
            def __init__(self, tc_list=None, content=None):
                self.tool_calls = tc_list
                self.content = content

        class MockChoice:
            def __init__(self, delta):
                self.delta = delta

        class MockChunk:
            def __init__(self, choices=None, usage=None):
                self.choices = choices or []
                self.usage = usage

        class MockToolCall:
            def __init__(self, idx, call_id, name, args):
                self.index = idx
                self.id = call_id
                self.function = MagicMock()
                self.function.name = name
                self.function.arguments = args

        mock_tc = MockToolCall(0, "call_cap_fail", "capture_screen", json.dumps({"region": "full"}))
        turn1_chunks = [
            MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])
        ]
        turn2_chunks = [
            MockChunk([MockChoice(MockChoiceDelta(content="I was unable to capture the screen, boss."))])
        ]

        captured_completions_calls = []

        class MockCompletions:
            async def create(self, **kwargs):
                captured_completions_calls.append(kwargs)
                call_index = len(captured_completions_calls)
                chunks = turn1_chunks if call_index == 1 else turn2_chunks
                async def _gen():
                    for c in chunks:
                        yield c
                return _gen()

        mock_client = MagicMock()
        mock_client.chat.completions = MockCompletions()

        with patch("ghost.core.brain.OmniRouteBrain.load_config"), \
             patch("ghost.core.brain.OmniRouteBrain.load_history"), \
             patch("ghost.core.brain.OmniRouteBrain._config", {"api_key": "test_key", "model": "test_model"}), \
             patch("ghost.core.brain.OmniRouteBrain.get_client", return_value=mock_client), \
             patch("ghost.core.brain.OmniRouteBrain.record_turn"), \
             patch("ghost.mcp.bridge.SystemAppBridge.capture_screen", new_callable=AsyncMock) as mock_screen:

            mock_screen.return_value = {
                "status": "error",
                "message": "Failed to capture screen with grim"
            }

            events = []
            async for ev in agent.ask_stream("Take a screenshot."):
                events.append(ev)

            self.assertEqual(len(captured_completions_calls), 2)
            call2_messages = captured_completions_calls[1]["messages"]

            # Ensure NO image_url block was added
            for m in call2_messages:
                if isinstance(m.get("content"), list):
                    for b in m["content"]:
                        if isinstance(b, dict) and b.get("type") == "image_url":
                            self.fail("image_url block must NOT be sent to model when screen capture fails!")

            # Verify the tool result returned to model reported failed
            tool_messages = [m for m in call2_messages if m.get("role") == "tool"]
            self.assertEqual(len(tool_messages), 1)
            tool_content = json.loads(tool_messages[0]["content"])
            self.assertEqual(tool_content.get("status"), "failed")
            self.assertFalse(tool_content.get("ok"))
            self.assertEqual(tool_content.get("error"), "Failed to capture screen with grim")


if __name__ == "__main__":
    unittest.main()
