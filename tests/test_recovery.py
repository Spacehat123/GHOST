"""Unit and Integration Tests for Agent Error Recovery & Circuit Breaker.
Enforces non-negotiable architectural rule:
Graceful error recovery from failed tools without uncontrolled retries, preserving pending work.
"""
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import os
import json
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
    def __init__(self, prompt_tokens=50, completion_tokens=25, total_tokens=75):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens

class TestRecovery(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        OmniRouteBrain._config = {"api_key": "test_mock_key", "model": "test-model"}
        OmniRouteBrain._history = []

    @patch("ghost.core.brain.OmniRouteBrain.get_client")
    async def test_tool_failure_feedback_and_adaptation(self, mock_get_client):
        """When a tool fails with ok=False, Ghost feeds back normalized error so the model adapts."""
        agent = GhostAgent()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        tc_fail = MagicMock()
        tc_fail.index = 0
        tc_fail.id = "call_fail_1"
        tc_fail.function.name = "browser_action"
        tc_fail.function.arguments = json.dumps({"action": "click_and_type", "target": "#bad-button"})

        round_num = 0

        async def stream_rounds(*args, **kwargs):
            nonlocal round_num
            round_num += 1
            if round_num == 1:
                # Turn 1: model requests failing tool
                yield DummyChunk(tool_calls=[tc_fail], usage=DummyUsage())
            elif round_num == 2:
                # Turn 2: verify previous tool message contained failure
                messages = kwargs.get("messages", [])
                tool_msg = next((m for m in messages if m.get("role") == "tool"), None)
                assert tool_msg is not None
                content = json.loads(tool_msg["content"])
                assert content["ok"] is False
                assert content["status"] == "failed"
                # Model adapts and completes
                yield DummyChunk(content="Element was not found. Switched strategy and resolved.", usage=DummyUsage())

        mock_client.chat.completions.create = AsyncMock(side_effect=stream_rounds)

        with patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", new_callable=AsyncMock) as mock_exec:
            # Underlying tool fails
            mock_exec.return_value = {
                "ok": False,
                "status": "failed",
                "tool": "browser_action",
                "action": "click_and_type",
                "error": "Element '#bad-button' not found",
                "verified": False
            }

            events = []
            async for event in agent.ask_stream("Click submit"):
                events.append(event)

            self.assertEqual(mock_exec.call_count, 1)
            spoken = "".join(e["text"] for e in events if e.get("type") == "token")
            self.assertIn("Switched strategy", spoken)

    @patch("ghost.core.brain.OmniRouteBrain.get_client")
    async def test_consecutive_failure_circuit_breaker(self, mock_get_client):
        """Repeated identical calls yielding failure trip the circuit breaker and halt looping."""
        agent = GhostAgent()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        tc = MagicMock()
        tc.index = 0
        tc.id = "call_loop"
        tc.function.name = "system_run"
        tc.function.arguments = json.dumps({"command": "curl http://offline-host.local"})

        async def loop_stream(*args, **kwargs):
            messages = kwargs.get("messages", [])
            last_msg = str(messages[-1].get("content", ""))
            if "TASK HALTED" in last_msg or "BOUNDED LOOP" in last_msg:
                yield DummyChunk(content="Loop intercepted by breaker.", usage=DummyUsage())
                return
            yield DummyChunk(tool_calls=[tc], usage=DummyUsage())

        mock_client.chat.completions.create = AsyncMock(side_effect=loop_stream)

        with patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {
                "ok": False,
                "status": "failed",
                "tool": "system_run",
                "action": "system_run",
                "error": "Connection refused",
                "verified": False
            }

            events = []
            async for event in agent.ask_stream("Check offline host"):
                events.append(event)

            # Circuit breaker should intercept and trip
            breaker_events = [e for e in events if e.get("type") == "tool_end" and e.get("result", {}).get("status") == "circuit_breaker_tripped"]
            self.assertTrue(len(breaker_events) > 0)

    @patch("ghost.core.brain.OmniRouteBrain.get_client")
    async def test_barge_in_interruption(self, mock_get_client):
        """Calling agent.interrupt() immediately breaks stream with INTERRUPTED state."""
        agent = GhostAgent()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        async def slow_stream(*args, **kwargs):
            yield DummyChunk(content="Word1 ", usage=DummyUsage())
            await asyncio.sleep(0.05)
            # Interrupt triggered during stream
            agent.interrupt()
            yield DummyChunk(content="Word2 ", usage=DummyUsage())

        mock_client.chat.completions.create = AsyncMock(side_effect=slow_stream)

        events = []
        async for event in agent.ask_stream("Speak slowly"):
            events.append(event)

        state_events = [e.get("state") for e in events if e.get("type") == "state"]
        self.assertIn("INTERRUPTED", state_events)

if __name__ == "__main__":
    unittest.main()
