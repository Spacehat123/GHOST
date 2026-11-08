"""Unit and Integration Tests for 4-Dimensional Bounded Agent Loops in Ghost.
Enforces non-negotiable architectural rule:
Enforce hard limits on model rounds, tool executions, elapsed time, and cumulative task tokens.
When any limit is reached, stop execution and report what remains unfinished (remaining_work).
"""
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import os
import json
import tempfile

# The brain-loop fixtures ("build a site for Test Place", "Asset Pipeline",
# limit tests) once leaked into the operator's LIVE transcript (2026-11-08
# incident). Pin the history file BEFORE ghost imports.
os.environ["GHOST_CONVERSATION_HISTORY"] = os.path.join(
    tempfile.mkdtemp(prefix="ghost-loop-hist-"), "conversation_history.json")

from ghost.core.agent import GhostAgent
from ghost.core.brain import OmniRouteBrain

class DummyChunk:
    def __init__(self, content=None, tool_calls=None, usage=None):
        self.choices = [MagicMock()]
        self.choices[0].delta = MagicMock()
        self.choices[0].delta.content = content
        self.choices[0].delta.tool_calls = tool_calls
        self.usage = usage

class DummyUsage:
    def __init__(self, prompt_tokens=100, completion_tokens=50, total_tokens=150):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens

class TestAgentLoop(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Configure mock API key
        OmniRouteBrain._config = {"api_key": "test_mock_key", "model": "test-model"}
        OmniRouteBrain._history = []

    @patch("ghost.core.brain.OmniRouteBrain.get_client")
    async def test_bounded_model_rounds_ceiling(self, mock_get_client):
        """When model rounds reach max ceiling, execution stops and halts to final synthesis reporting remaining work."""
        agent = GhostAgent()

        # Mock client that repeatedly requests a tool call to simulate an endless loop
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        tc_mock = MagicMock()
        tc_mock.index = 0
        tc_mock.id = "call_round_test"
        tc_mock.function.name = "web_search"
        tc_mock.function.arguments = json.dumps({"query": "ghost agency architecture"})

        async def infinite_tool_stream(*args, **kwargs):
            messages = kwargs.get("messages", [])
            # Check if this is the final synthesis pass (contains halt notice)
            last_msg = str(messages[-1].get("content", ""))
            if "BOUNDED LOOP CEILING BREACHED" in last_msg:
                # Final synthesis pass
                yield DummyChunk(content="Task halted due to limit. Remaining work: complete deployment.", usage=DummyUsage(10, 10, 20))
                return

            yield DummyChunk(tool_calls=[tc_mock], usage=DummyUsage(20, 10, 30))

        mock_client.chat.completions.create = AsyncMock(side_effect=infinite_tool_stream)

        with patch.dict(os.environ, {"GHOST_MAX_MODEL_ROUNDS": "3", "GHOST_MAX_TOOL_EXECUTIONS": "10"}):
            with patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = {"ok": True, "status": "success", "tool": "web_search", "action": "search", "results": []}

                events = []
                async for event in agent.ask_stream("Run loop test"):
                    events.append(event)

                # Total model calls should equal GHOST_MAX_MODEL_ROUNDS (3) + 1 final synthesis pass = 4 calls
                self.assertEqual(mock_client.chat.completions.create.call_count, 4)

                # Confirm final synthesis tokens received and mention remaining work
                spoken_tokens = [e["text"] for e in events if e.get("type") == "token"]
                full_speech = "".join(spoken_tokens)
                self.assertIn("Remaining work", full_speech)

    @patch("ghost.core.brain.OmniRouteBrain.get_client")
    async def test_bounded_tool_executions_ceiling(self, mock_get_client):
        """When total tool executions reach ceiling, execution halts to final synthesis."""
        agent = GhostAgent()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        tc_mock = MagicMock()
        tc_mock.index = 0
        tc_mock.id = "call_tool_test"
        tc_mock.function.name = "web_search"
        tc_mock.function.arguments = json.dumps({"query": "audit pipeline"})

        async def tool_stream(*args, **kwargs):
            messages = kwargs.get("messages", [])
            last_msg = str(messages[-1].get("content", ""))
            if "BOUNDED LOOP CEILING BREACHED" in last_msg:
                yield DummyChunk(content="Execution ceiling reached. Remaining work: verify accounts.", usage=DummyUsage(10, 10, 20))
                return
            yield DummyChunk(tool_calls=[tc_mock], usage=DummyUsage(15, 10, 25))

        mock_client.chat.completions.create = AsyncMock(side_effect=tool_stream)

        with patch.dict(os.environ, {"GHOST_MAX_TOOL_EXECUTIONS": "2", "GHOST_MAX_MODEL_ROUNDS": "10"}):
            with patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = {"ok": True, "status": "success", "tool": "web_search", "action": "search", "results": []}

                events = []
                async for event in agent.ask_stream("Run tool limit test"):
                    events.append(event)

                # Tool executions must not exceed 2
                self.assertEqual(mock_exec.call_count, 2)
                spoken_tokens = [e["text"] for e in events if e.get("type") == "token"]
                self.assertIn("Remaining work", "".join(spoken_tokens))

    @patch("ghost.core.brain.OmniRouteBrain.get_client")
    async def test_malformed_arguments_schema_error_feedback(self, mock_get_client):
        """Malformed arguments must trigger schema error without invoking tool execution."""
        agent = GhostAgent()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Emits invalid tool call: 'desktop_control' with missing 'action'
        invalid_tc = MagicMock()
        invalid_tc.index = 0
        invalid_tc.id = "bad_call_1"
        invalid_tc.function.name = "desktop_control"
        invalid_tc.function.arguments = "{}"

        call_step = 0

        async def stream_turn(*args, **kwargs):
            nonlocal call_step
            call_step += 1
            if call_step == 1:
                yield DummyChunk(tool_calls=[invalid_tc], usage=DummyUsage(20, 10, 30))
            else:
                yield DummyChunk(content="Understood schema error, aborting.", usage=DummyUsage(20, 10, 30))

        mock_client.chat.completions.create = AsyncMock(side_effect=stream_turn)

        with patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", new_callable=AsyncMock) as mock_exec:
            events = []
            async for event in agent.ask_stream("Trigger bad tool"):
                events.append(event)

            # execute_tool_call MUST NOT be called because schema validation failed
            mock_exec.assert_not_called()

            # Confirm tool_end event contained schema violation
            tool_ends = [e for e in events if e.get("type") == "tool_end"]
            self.assertEqual(len(tool_ends), 1)
            self.assertTrue(tool_ends[0]["result"].get("schema_violation"))
            self.assertIn("Missing required parameter 'action'", tool_ends[0]["result"].get("error"))

if __name__ == "__main__":
    unittest.main()
