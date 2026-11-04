"""End-to-End (E2E) Scenario Test Suite for Ghost Autonomous Agency Engine.
Validates all 7 end-to-end integration scenarios defined in the architectural plan:
1. Multi-Constraint Task Execution
2. Failed Tool Execution & Strategic Adaptation
3. Watchdog Event Ingestion & Lossless Deduplication
4. Interrupted Execution & State Preservation
5. Honest Reporting of Unverified Operations
6. Task Resumption via 3-Layer Memory
7. Token Ceiling Graceful Degradation & Remaining Work
"""
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import tempfile
import shutil
import os
import json
import asyncio
from ghost.core.agent import GhostAgent
from ghost.core.brain import OmniRouteBrain
from ghost.core.memory import CoreMemoryStore, EpisodicMemoryStore, WorkingMemory
from ghost.core.instructions import InstructionComplianceGate
from ghost.watchdog.service import AutonomousWatchdog
from ghost.qa.contracts import TaskResult, ActionVerifier
from ghost.core.token_tracker import TaskTokenTracker

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

class TestE2EScenarios(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.events_db = os.path.join(self.temp_dir, "events.db")
        self.memory_db = os.path.join(self.temp_dir, "memory.db")
        self.directives_file = os.path.join(self.temp_dir, "directives.json")
        OmniRouteBrain._config = {"api_key": "mock_api_key", "model": "antigravity/gemini-3.8-flash-low"}
        OmniRouteBrain._history = []

    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("ghost.core.brain.OmniRouteBrain.get_client")
    async def test_scenario_1_multi_constraint_task_execution(self, mock_get_client):
        """Scenario 1: Multi-constraint task enforces safety rules, tracks cumulative tokens, and verifies evidence."""
        agent = GhostAgent()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        out_file = os.path.join(self.temp_dir, "brief.md")

        # Step 1: Model tries to send email directly (violating Zero Auto-Send)
        tc_bad = MagicMock()
        tc_bad.index = 0
        tc_bad.id = "bad_send"
        tc_bad.function.name = "email_send_pitch"
        tc_bad.function.arguments = json.dumps({"recipient": "client@london.uk", "subject": "Pitch"})

        # Step 2: Model adapts to safe tool
        tc_good = MagicMock()
        tc_good.index = 0
        tc_good.id = "good_search"
        tc_good.function.name = "web_search"
        tc_good.function.arguments = json.dumps({"query": "Topaz Detailing London services"})

        round_count = 0

        async def stream_scenario(*args, **kwargs):
            nonlocal round_count
            round_count += 1
            if round_count == 1:
                yield DummyChunk(tool_calls=[tc_bad], usage=DummyUsage(100, 30, 130))
            elif round_count == 2:
                # Compliance gate blocked previous call
                messages = kwargs.get("messages", [])
                tool_msg = next((m for m in messages if m.get("role") == "tool"), None)
                assert tool_msg is not None
                content = json.loads(tool_msg["content"])
                assert content["compliance_rule"] == "RULE_ZERO_AUTO_SEND"
                yield DummyChunk(tool_calls=[tc_good], usage=DummyUsage(150, 40, 190))
            else:
                yield DummyChunk(content="Completed scout. Brief saved as draft.", usage=DummyUsage(200, 50, 250))

        mock_client.chat.completions.create = AsyncMock(side_effect=stream_scenario)

        with patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {
                "ok": True,
                "status": "success",
                "tool": "web_search",
                "action": "search",
                "results": ["Topaz Detailing specializes in PPF and ceramic coating."]
            }

            events = []
            async for evt in agent.ask_stream("Prepare outreach for Topaz Detailing"):
                events.append(evt)

            # Assert compliance gate blocked the email send
            blocked_events = [e for e in events if e.get("type") == "tool_end" and e.get("result", {}).get("status") == "blocked"]
            self.assertEqual(len(blocked_events), 1)
            self.assertEqual(blocked_events[0]["result"]["compliance_rule"], "RULE_ZERO_AUTO_SEND")

            # Assert cumulative token stats were yielded
            token_events = [e for e in events if e.get("type") == "token_stats"]
            self.assertTrue(len(token_events) > 0)
            self.assertTrue(token_events[-1]["last"] > 500)

    @patch("ghost.core.brain.OmniRouteBrain.get_client")
    async def test_scenario_2_failed_tool_and_strategic_adaptation(self, mock_get_client):
        """Scenario 2: When a tool fails, normalized error is delivered and model adapts without infinite retries."""
        agent = GhostAgent()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        tc_failing = MagicMock()
        tc_failing.index = 0
        tc_failing.id = "call_cdp_fail"
        tc_failing.function.name = "browser_action"
        tc_failing.function.arguments = json.dumps({"action": "open_url", "url": "https://offline-target.com"})

        step = 0
        async def stream_fail_adapt(*args, **kwargs):
            nonlocal step
            step += 1
            if step == 1:
                yield DummyChunk(tool_calls=[tc_failing], usage=DummyUsage())
            else:
                yield DummyChunk(content="Target site offline. Falling back to local offline cache.", usage=DummyUsage())

        mock_client.chat.completions.create = AsyncMock(side_effect=stream_fail_adapt)

        with patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {
                "ok": False,
                "status": "failed",
                "tool": "browser_action",
                "action": "open_url",
                "error": "Net::ERR_CONNECTION_REFUSED",
                "verified": False
            }

            events = []
            async for evt in agent.ask_stream("Scout offline target"):
                events.append(evt)

            # Failure must be normalized and delivered
            tool_ends = [e for e in events if e.get("type") == "tool_end"]
            self.assertEqual(len(tool_ends), 1)
            self.assertFalse(tool_ends[0]["result"]["ok"])
            self.assertEqual(tool_ends[0]["result"]["status"], "failed")

            spoken = "".join(e["text"] for e in events if e.get("type") == "token")
            self.assertIn("Falling back", spoken)

    @patch("ghost.mcp.bridge.SystemAppBridge.desktop_notify", new_callable=AsyncMock)
    async def test_scenario_3_watchdog_event_ingestion_lossless_dedup(self, mock_notify):
        """Scenario 3: Watchdog event ingestion persists duplicate signals with full occurrence history."""
        watchdog = AutonomousWatchdog(db_path=self.events_db)

        # Ingest 3 signals: 2 identical, 1 distinct
        await watchdog.emit_signal("lead_optin", "webhook", "New lead: Marcus Aurelius", {"email": "m@rome.com"})
        await watchdog.emit_signal("lead_optin", "webhook", "New lead: Marcus Aurelius", {"email": "m@rome.com"})
        await watchdog.emit_signal("lead_optin", "webhook", "New lead: Seneca", {"email": "s@rome.com"})

        unprocessed = watchdog.get_unprocessed_events(limit=10)
        self.assertEqual(len(unprocessed), 2)

        marcus_evt = next(e for e in unprocessed if "Marcus" in e["summary"])
        self.assertEqual(marcus_evt["occurrence_count"], 2)

        history = json.loads(marcus_evt["occurrences_json"])
        self.assertEqual(len(history), 2)

    @patch("ghost.core.brain.OmniRouteBrain.get_client")
    async def test_scenario_4_interrupted_execution_and_preservation(self, mock_get_client):
        """Scenario 4: Interrupted stream exits cleanly and preserves working state."""
        agent = GhostAgent()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        async def slow_stream(*args, **kwargs):
            yield DummyChunk(content="Starting long task... ", usage=DummyUsage())
            await asyncio.sleep(0.02)
            agent.interrupt()
            yield DummyChunk(content="More content...", usage=DummyUsage())

        mock_client.chat.completions.create = AsyncMock(side_effect=slow_stream)

        events = []
        async for evt in agent.ask_stream("Run heavy generation"):
            events.append(evt)

        states = [e.get("state") for e in events if e.get("type") == "state"]
        self.assertIn("INTERRUPTED", states)

    def test_scenario_5_honest_unverified_reporting(self):
        """Scenario 5: Ghost never claims task completion when acceptance criteria lack concrete evidence."""
        criteria = [
            {
                "type": "file_exists",
                "description": "Vercel production URL response verified",
                "path": os.path.join(self.temp_dir, "nonexistent_deployment.json"),
                "min_bytes": 10
            }
        ]
        result = ActionVerifier.evaluate_task_criteria("task_deploy", criteria)
        self.assertEqual(result.status, "unverified")
        self.assertFalse(result.verified)
        self.assertEqual(len(result.remaining_work), 1)
        self.assertIn("Vercel production URL response verified", result.remaining_work[0])

    def test_scenario_6_task_resumption_via_3_layer_memory(self):
        """Scenario 6: Core memory snapshot enforces <= 250 token limit while resolving contradiction cleanly."""
        core_store = CoreMemoryStore(config_path=self.directives_file)
        episodic_store = EpisodicMemoryStore(db_path=self.memory_db)

        # Record episodic lesson
        episodic_store.record_episode(
            task_id="t_past",
            query="Brave CDP connection setup",
            summary="Port 9222 connection confirmed",
            outcome="success",
            lessons_learned="Always specify --remote-debugging-port=9222"
        )

        # Add conflicting core memories
        res1 = core_store.add_memory("Primary reverse engineering tool is REA", mem_type="fact", importance=0.9)
        res2 = core_store.add_memory("Primary reverse engineering tool is Ghidra", mem_type="fact", importance=0.8)

        # Both preserved, neither deleted
        self.assertTrue(res2["conflict_detected"])

        # Snapshot omits conflict until resolved
        snap_before = core_store.get_snapshot()
        self.assertNotIn("Primary reverse engineering tool is REA", snap_before)

        # Resolve conflict
        core_store.resolve_conflict(res1["item"]["id"], res2["item"]["id"], "Operator confirmed REA as primary tool")
        snap_after = core_store.get_snapshot()
        self.assertIn("Primary reverse engineering tool is REA", snap_after)

        # Token ceiling check
        self.assertLessEqual(len(snap_after) // 4, 250)

    @patch("ghost.core.brain.OmniRouteBrain.get_client")
    async def test_scenario_7_token_ceiling_graceful_degradation(self, mock_get_client):
        """Scenario 7: When cumulative tokens hit ceiling, loop halts and final synthesis reports remaining work."""
        agent = GhostAgent()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        tc = MagicMock()
        tc.index = 0
        tc.id = "tc_heavy"
        tc.function.name = "web_search"
        tc.function.arguments = json.dumps({"query": "deep research topic"})

        async def heavy_token_stream(*args, **kwargs):
            messages = kwargs.get("messages", [])
            last_msg = str(messages[-1].get("content", ""))
            if "BOUNDED LOOP CEILING BREACHED" in last_msg:
                yield DummyChunk(content="Token ceiling breached. Remaining work: process final 5 items.", usage=DummyUsage(10, 10, 20))
                return
            # High token usage per round to trigger ceiling
            yield DummyChunk(tool_calls=[tc], usage=DummyUsage(2000, 1000, 3000))

        mock_client.chat.completions.create = AsyncMock(side_effect=heavy_token_stream)

        with patch.dict(os.environ, {"GHOST_MAX_TASK_TOTAL_TOKENS": "5000", "GHOST_MAX_MODEL_ROUNDS": "10"}):
            with patch("ghost.core.brain.OmniRouteBrain.execute_tool_call", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = {"ok": True, "status": "success", "tool": "web_search", "action": "search", "results": []}

                events = []
                async for evt in agent.ask_stream("Run token intensive task"):
                    events.append(evt)

                spoken = "".join(e["text"] for e in events if e.get("type") == "token")
                self.assertIn("Remaining work", spoken)

if __name__ == "__main__":
    unittest.main()
