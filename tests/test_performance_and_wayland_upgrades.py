import asyncio
import json
import os
import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ghost.workers.recon_scout import ReconScout
from ghost.workers.audit_engine import PerformanceAuditor
from ghost.mcp.bridge import CDPAutomationEngine, MediaBridge, SystemAppBridge
from ghost.core.agent import GhostAgent


def _dep_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


class TestPerformanceAndWaylandUpgrades(unittest.IsolatedAsyncioTestCase):

    async def test_recon_scout_correlated_cdp(self):
        """Verify ReconScout._send_cdp correlates response by req_id and skips background events."""
        class FakeWS:
            def __init__(self):
                self.sent = []
                self.frames = [
                    json.dumps({"method": "Page.frameNavigated", "params": {}}),
                    json.dumps({"method": "DOM.documentUpdated"}),
                    json.dumps({"id": 42, "result": {"success": True, "data": "recon_ok"}})
                ]

            async def send(self, data):
                self.sent.append(json.loads(data))

            async def recv(self):
                if self.frames:
                    return self.frames.pop(0)
                await asyncio.sleep(10)
                return "{}"

        ws = FakeWS()
        resp = await ReconScout._send_cdp(ws, 42, "Runtime.evaluate", {"expression": "1+1"}, timeout=2.0)
        self.assertEqual(resp["id"], 42)
        self.assertEqual(resp["result"]["data"], "recon_ok")
        self.assertEqual(ws.sent[0]["id"], 42)
        self.assertEqual(ws.sent[0]["method"], "Runtime.evaluate")

    async def test_audit_engine_correlated_cdp(self):
        """Verify PerformanceAuditor._send_cdp correlates response by req_id."""
        class FakeWS:
            def __init__(self):
                self.sent = []
                self.frames = [
                    json.dumps({"method": "Page.loadEventFired", "params": {"timestamp": 123}}),
                    json.dumps({"id": 7, "result": {"metrics": [{"name": "Nodes", "value": 120}]}})
                ]

            async def send(self, data):
                self.sent.append(json.loads(data))

            async def recv(self):
                if self.frames:
                    return self.frames.pop(0)
                await asyncio.sleep(10)
                return "{}"

        ws = FakeWS()
        resp = await PerformanceAuditor._send_cdp(ws, 7, "Performance.getMetrics", timeout=2.0)
        self.assertEqual(resp["id"], 7)
        self.assertEqual(resp["result"]["metrics"][0]["name"], "Nodes")

    async def test_cdp_automation_engine_timeout(self):
        """Verify CDPAutomationEngine.send_command times out if no matching response arrives."""
        class HangingWS:
            async def send(self, data):
                pass

            async def recv(self):
                await asyncio.sleep(0.05)
                return json.dumps({"method": "Network.dataReceived"})

        ws = HangingWS()
        with self.assertRaises(asyncio.TimeoutError):
            await CDPAutomationEngine.send_command(ws, 999, "Page.navigate", {"url": "http://example.com"}, timeout=0.15)

    async def test_media_bridge_resolve_spotify_track(self):
        """Verify Spotify track URI resolution for explicit URIs and web URLs."""
        uri = await MediaBridge.resolve_spotify_track("spotify:track:4cOdK2wGLETKBW3PvgPWqT")
        self.assertEqual(uri, "spotify:track:4cOdK2wGLETKBW3PvgPWqT")

        web_uri = await MediaBridge.resolve_spotify_track("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
        self.assertEqual(web_uri, "spotify:track:4cOdK2wGLETKBW3PvgPWqT")

    async def test_media_bridge_spotify_action_no_xdotool(self):
        """Verify spotify_action uses playerctl and DBus OpenUri without calling xdotool."""
        with patch("ghost.mcp.bridge.ShellExecutor.run", new_callable=AsyncMock) as mock_shell:
            mock_shell.side_effect = lambda cmd: (
                {"returncode": 0, "stdout": "playing"} if "status" in cmd or "pgrep" in cmd else {"returncode": 0, "stdout": "ok"}
            )

            res = await MediaBridge.spotify_action(action="play_query", query="spotify:track:12345")
            self.assertEqual(res.get("status"), "playing_query")
            self.assertTrue(res.get("verified"))

            for call_args in mock_shell.call_args_list:
                cmd = call_args[0][0]
                self.assertNotIn("xdotool", cmd, f"xdotool found in executed command: {cmd}")

    async def test_system_app_bridge_hyprctl_dispatch(self):
        """Verify hyprctl_dispatch uses standard dispatch syntax and handles fallback."""
        with patch("ghost.mcp.bridge.ShellExecutor.run", new_callable=AsyncMock) as mock_shell:
            # Standard CLI success
            mock_shell.return_value = {"returncode": 0, "stdout": "ok"}
            res = await SystemAppBridge.hyprctl_dispatch("movetoworkspacesilent", "2,class:kitty")
            self.assertEqual(res["status"], "dispatched")
            self.assertIn("hyprctl dispatch movetoworkspacesilent 2,class:kitty", res["command"])

            # If standard CLI returns non-zero (Hyprland 0.56 lua mode), fallback to hl.dsp
            mock_shell.side_effect = [
                {"returncode": 7, "stdout": "error"},
                {"returncode": 0, "stdout": "table: 0x123"}
            ]
            res_fb = await SystemAppBridge.hyprctl_dispatch("movetoworkspacesilent", "3,class:code")
            self.assertEqual(res_fb["status"], "dispatched")
            self.assertIn("hl.dsp.window.move", res_fb["command"])
            self.assertIn("workspace = 3", res_fb["command"])
            self.assertIn('window = "class:code"', res_fb["command"])

    async def test_system_app_bridge_input_simulation_no_xdotool(self):
        """Verify input_simulation uses wl-copy and Hyprland primitives, not xdotool."""
        with patch("ghost.mcp.bridge.ShellExecutor.run", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = {"returncode": 0, "stdout": "ok"}

            res = await SystemAppBridge.input_simulation("type_text", keys="hello ghost")
            self.assertEqual(res["status"], "simulated")

            res_key = await SystemAppBridge.input_simulation("shortcut", keys="CTRL + SHIFT + T")
            self.assertEqual(res_key["status"], "simulated")

            for call_args in mock_shell.call_args_list:
                cmd = call_args[0][0]
                self.assertNotIn("xdotool", cmd, f"xdotool found in executed command: {cmd}")

    async def test_agent_circuit_breaker(self):
        """Verify GhostAgent circuit breaker intercepts identical failing tool calls on the 3rd attempt."""
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

        mock_tc = MockToolCall(0, "call_test", "shell_execution", json.dumps({"command": "ls /nonexistent"}))

        chunks_turn1 = [
            MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])
        ]
        chunks_turn2 = [
            MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])
        ]
        chunks_turn3 = [
            MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])
        ]
        chunks_final = [
            MockChunk([MockChoice(MockChoiceDelta(content="I halted the failing command, sir."))])
        ]

        all_turns = [chunks_turn1, chunks_turn2, chunks_turn3, chunks_final]
        turn_idx = 0

        class MockCompletions:
            async def create(self, **kwargs):
                nonlocal turn_idx
                if turn_idx < len(all_turns):
                    current_chunks = all_turns[turn_idx]
                    turn_idx += 1
                else:
                    current_chunks = chunks_final

                async def _gen():
                    for c in current_chunks:
                        yield c
                return _gen()

        mock_client = MagicMock()
        mock_client.chat.completions = MockCompletions()

        execute_calls = []
        async def fake_execute(tool_name, args):
            execute_calls.append((tool_name, args))
            return {"status": "error", "message": "No such file or directory"}

        with patch("ghost.core.brain.OmniRouteBrain.load_config"), \
             patch("ghost.core.brain.OmniRouteBrain.load_history"), \
             patch("ghost.core.brain.OmniRouteBrain._config", {"api_key": "test_key", "model": "test_model"}), \
             patch("ghost.core.brain.OmniRouteBrain.get_client", return_value=mock_client), \
             patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", side_effect=fake_execute), \
             patch("ghost.core.brain.OmniRouteBrain.record_turn"):

            events = []
            async for ev in agent.ask_stream("test input"):
                events.append(ev)

            # execute_tool_call should be called on turn 1 and turn 2, but NOT on turn 3 (intercepted by circuit breaker!)
            self.assertEqual(len(execute_calls), 2, f"Expected 2 actual executions, got {len(execute_calls)}")

            # Check that circuit breaker tripped in events
            cb_events = [ev for ev in events if ev.get("type") == "tool_end" and ev.get("result", {}).get("status") == "circuit_breaker_tripped"]
            self.assertTrue(len(cb_events) >= 1, "Expected circuit_breaker_tripped event in stream")
            self.assertEqual(cb_events[0]["result"]["consecutive_attempts"], 3)

    async def test_agent_circuit_breaker_resets_on_arg_change(self):
        """Verify circuit breaker does NOT trip when arguments change."""
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

        # 3 calls with varying arguments
        chunks = [
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[MockToolCall(0, "c1", "shell_execution", json.dumps({"command": "echo 1"}))]))])],
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[MockToolCall(0, "c2", "shell_execution", json.dumps({"command": "echo 2"}))]))])],
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[MockToolCall(0, "c3", "shell_execution", json.dumps({"command": "echo 3"}))]))])],
            [MockChunk([MockChoice(MockChoiceDelta(content="Done."))])]
        ]
        turn_idx = 0

        class MockCompletions:
            async def create(self, **kwargs):
                nonlocal turn_idx
                cur = chunks[turn_idx] if turn_idx < len(chunks) else chunks[-1]
                turn_idx += 1
                async def _gen():
                    for c in cur:
                        yield c
                return _gen()

        mock_client = MagicMock()
        mock_client.chat.completions = MockCompletions()

        execute_calls = []
        async def fake_execute(tool_name, args):
            execute_calls.append((tool_name, args))
            return {"status": "ok", "stdout": "test"}

        with patch("ghost.core.brain.OmniRouteBrain.load_config"), \
             patch("ghost.core.brain.OmniRouteBrain.load_history"), \
             patch("ghost.core.brain.OmniRouteBrain._config", {"api_key": "test_key", "model": "test_model"}), \
             patch("ghost.core.brain.OmniRouteBrain.get_client", return_value=mock_client), \
             patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", side_effect=fake_execute), \
             patch("ghost.core.brain.OmniRouteBrain.record_turn"):

            events = []
            async for ev in agent.ask_stream("test input"):
                events.append(ev)

            # All 3 distinct calls should execute normally
            self.assertEqual(len(execute_calls), 3)
            cb_events = [ev for ev in events if ev.get("type") == "tool_end" and ev.get("result", {}).get("status") == "circuit_breaker_tripped"]
            self.assertEqual(len(cb_events), 0)

    async def test_agent_circuit_breaker_breaks_loop_on_fourth_attempt(self):
        """Verify that on 4th consecutive attempt, the loop breaks to final synthesis pass."""
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

        mock_tc = MockToolCall(0, "call_test", "shell_execution", json.dumps({"command": "repeated"}))

        # Model keeps emitting same tool call for 4 turns
        chunks = [
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])],
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])],
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])],
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])],
            [MockChunk([MockChoice(MockChoiceDelta(content="I am speaking directly to the operator."))])]
        ]
        turn_idx = 0

        class MockCompletions:
            async def create(self, **kwargs):
                nonlocal turn_idx
                cur = chunks[turn_idx] if turn_idx < len(chunks) else chunks[-1]
                turn_idx += 1
                async def _gen():
                    for c in cur:
                        yield c
                return _gen()

        mock_client = MagicMock()
        mock_client.chat.completions = MockCompletions()

        async def fake_execute(tool_name, args):
            return {"status": "error", "message": "fail"}

        with patch("ghost.core.brain.OmniRouteBrain.load_config"), \
             patch("ghost.core.brain.OmniRouteBrain.load_history"), \
             patch("ghost.core.brain.OmniRouteBrain._config", {"api_key": "test_key", "model": "test_model"}), \
             patch("ghost.core.brain.OmniRouteBrain.get_client", return_value=mock_client), \
             patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", side_effect=fake_execute), \
             patch("ghost.core.brain.OmniRouteBrain.record_turn"):

            events = []
            async for ev in agent.ask_stream("test input"):
                events.append(ev)

            # Check that final synthesis spoke to operator
            spoken_tokens = [ev["text"] for ev in events if ev.get("type") == "token"]
            self.assertTrue(any("speaking directly to the operator" in t for t in spoken_tokens))

    async def test_agent_circuit_breaker_does_not_trip_on_different_successful_results(self):
        """Verify circuit breaker does NOT trip if repeated tool calls yield distinct non-error results."""
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

        mock_tc = MockToolCall(0, "c1", "data_fetcher", json.dumps({"query": "live_stream"}))

        chunks = [
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])],
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])],
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])],
            [MockChunk([MockChoice(MockChoiceDelta(content="Finished stream."))])]
        ]
        turn_idx = 0

        class MockCompletions:
            async def create(self, **kwargs):
                nonlocal turn_idx
                cur = chunks[turn_idx] if turn_idx < len(chunks) else chunks[-1]
                turn_idx += 1
                async def _gen():
                    for c in cur:
                        yield c
                return _gen()

        mock_client = MagicMock()
        mock_client.chat.completions = MockCompletions()

        call_results = [
            {"status": "ok", "count": 10, "data": "batch_1"},
            {"status": "ok", "count": 20, "data": "batch_2"},
            {"status": "ok", "count": 30, "data": "batch_3"}
        ]
        execute_calls = []

        async def fake_execute(tool_name, args):
            idx = len(execute_calls)
            execute_calls.append((tool_name, args))
            return call_results[idx] if idx < len(call_results) else {"status": "ok"}

        with patch("ghost.core.brain.OmniRouteBrain.load_config"), \
             patch("ghost.core.brain.OmniRouteBrain.load_history"), \
             patch("ghost.core.brain.OmniRouteBrain._config", {"api_key": "test_key", "model": "test_model"}), \
             patch("ghost.core.brain.OmniRouteBrain.get_client", return_value=mock_client), \
             patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", side_effect=fake_execute), \
             patch("ghost.core.brain.OmniRouteBrain.record_turn"):

            events = []
            async for ev in agent.ask_stream("test polling"):
                events.append(ev)

            # All 3 calls should execute since each yielded different successful data!
            self.assertEqual(len(execute_calls), 3)
            cb_events = [ev for ev in events if ev.get("type") == "tool_end" and ev.get("result", {}).get("status") == "circuit_breaker_tripped"]
            self.assertEqual(len(cb_events), 0)

    async def test_agent_circuit_breaker_trips_on_shell_returncode_error(self):
        """Verify circuit breaker trips when ShellExecutor returns non-zero returncode."""
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

        mock_tc = MockToolCall(0, "c1", "shell_execution", json.dumps({"command": "cargo build"}))

        chunks = [
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])],
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])],
            [MockChunk([MockChoice(MockChoiceDelta(tc_list=[mock_tc]))])],
            [MockChunk([MockChoice(MockChoiceDelta(content="Halting."))])]
        ]
        turn_idx = 0

        class MockCompletions:
            async def create(self, **kwargs):
                nonlocal turn_idx
                cur = chunks[turn_idx] if turn_idx < len(chunks) else chunks[-1]
                turn_idx += 1
                async def _gen():
                    for c in cur:
                        yield c
                return _gen()

        mock_client = MagicMock()
        mock_client.chat.completions = MockCompletions()

        execute_calls = []
        async def fake_execute(tool_name, args):
            execute_calls.append((tool_name, args))
            # Simulates ShellExecutor returning non-zero returncode with varying stderr (e.g. timestamps)
            return {"returncode": 101, "stdout": "", "stderr": f"error at time {len(execute_calls)}"}

        with patch("ghost.core.brain.OmniRouteBrain.load_config"), \
             patch("ghost.core.brain.OmniRouteBrain.load_history"), \
             patch("ghost.core.brain.OmniRouteBrain._config", {"api_key": "test_key", "model": "test_model"}), \
             patch("ghost.core.brain.OmniRouteBrain.get_client", return_value=mock_client), \
             patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", side_effect=fake_execute), \
             patch("ghost.core.brain.OmniRouteBrain.record_turn"):

            events = []
            async for ev in agent.ask_stream("test shell failure"):
                events.append(ev)

            # Should execute twice, and intercept on 3rd attempt
            self.assertEqual(len(execute_calls), 2)
            cb_events = [ev for ev in events if ev.get("type") == "tool_end" and ev.get("result", {}).get("status") == "circuit_breaker_tripped"]
            self.assertEqual(len(cb_events), 1)

    async def test_hyprctl_dispatch_returns_error_on_failed_lua_fallback(self):
        """Verify hyprctl_dispatch returns status='error' when both CLI and Lua dispatch fail."""
        with patch("ghost.mcp.bridge.ShellExecutor.run", new_callable=AsyncMock) as mock_shell:
            mock_shell.side_effect = [
                {"returncode": 1, "stdout": "error CLI"},
                {"returncode": 1, "stdout": "error REPL"}
            ]
            res = await SystemAppBridge.hyprctl_dispatch("workspace", "5")
            self.assertEqual(res["status"], "error")

    @unittest.skipUnless(_dep_available("aiohttp") and _dep_available("websockets"),
                         "aiohttp/websockets not installed — test patches their APIs")
    async def test_audit_engine_event_buffer_catches_load_event(self):
        """Verify PerformanceAuditor captures Page.loadEventFired in event_buffer during _send_cdp."""
        class FastLoadWS:
            def __init__(self):
                self.messages = [
                    # During enable
                    json.dumps({"id": 1, "result": {}}),
                    json.dumps({"id": 2, "result": {}}),
                    # Page.loadEventFired arrives before navigation response completes!
                    json.dumps({"method": "Page.loadEventFired", "params": {"timestamp": 12345.67}}),
                    json.dumps({"id": 3, "result": {"frameId": "ABC"}}),
                    # Metrics response
                    json.dumps({"id": 4, "result": {"metrics": [{"name": "Nodes", "value": 500}]}})
                ]

            async def send(self, data):
                pass

            async def recv(self):
                if self.messages:
                    return self.messages.pop(0)
                await asyncio.sleep(10)
                return "{}"

        ws = FastLoadWS()
        with patch("aiohttp.ClientSession.put") as mock_put, \
             patch("aiohttp.ClientSession.get"), \
             patch("websockets.connect") as mock_ws_connect:

            mock_resp = AsyncMock()
            mock_resp.json = AsyncMock(return_value={"id": "tab1", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/tab1"})
            mock_put.return_value.__aenter__.return_value = mock_resp

            mock_ws_ctx = AsyncMock()
            mock_ws_ctx.__aenter__.return_value = ws
            mock_ws_connect.return_value = mock_ws_ctx

            audit_res = await PerformanceAuditor.audit_url("https://fast-site.com")
            self.assertIn("load_time_sec", audit_res)
            # Must not have waited for the 4.5s timeout!
            self.assertLess(audit_res["load_time_sec"], 2.0)


if __name__ == "__main__":
    unittest.main()
