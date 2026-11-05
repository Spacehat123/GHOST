"""P4.5 tests: narrator honesty (no narration without an event), milestone
budgets/throttling, barge-in accounting, and the reference-parity demo tape."""
import asyncio
import os
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-p45-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")

from ghost.core.audit import AuditLedger, audit
from ghost.voice.narrator import Narrator, proof_of_honesty
from ghost.voice.task_runner import TaskRunner, find_and_build_demo_flow


class RecordingSink:
    def __init__(self):
        self.lines = []
    def __call__(self, text):
        self.lines.append(text)


class TestNarratorHonesty(unittest.TestCase):

    def setUp(self):
        self.sink = RecordingSink()
        self.n = Narrator(speak=self.sink)

    def test_speaks_only_payload_proven_numbers(self):
        u = self.n.on_event("lead_hunt.candidates", {"count": 6}, subject="t1")
        self.assertEqual(u, "Found 6 businesses without websites.")
        self.assertEqual(self.sink.lines, ["Found 6 businesses without websites."])

    def test_missing_payload_field_stays_silent(self):
        u = self.n.on_event("lead_hunt.candidates", {}, subject="t1")
        self.assertIsNone(u)          # never guess a number
        self.assertEqual(self.sink.lines, [])

    def test_short_class_budget_is_two_milestones(self):
        clock = {"t": 100.0}
        n = Narrator(speak=self.sink, now=lambda: clock["t"])
        for i in range(5):
            clock["t"] += 10.0  # throttle never binds; only the budget caps
            n.on_event("demo.build_started", {"business": f"b{i}", "task_class": "short"},
                       subject="t1")
        self.assertEqual(len(self.sink.lines), 2)  # budget cap (§14.2.7)

    def test_long_class_throttle_gap(self):
        clock = {"t": 100.0}
        n = Narrator(speak=self.sink, now=lambda: clock["t"])
        spoken = 0
        for i in range(20):
            clock["t"] += 1.0  # 1s steps < 8s gap
            if n.on_event("lead_hunt.candidates", {"count": i, "task_class": "long"},
                          subject="t1"):
                spoken += 1
        self.assertEqual(spoken, 3)  # t=100, 108, 116 over 20 seconds

    def test_terminal_events_always_speak(self):
        self.n.on_event("task.done", {"summary": "demo live", "task_class": "short"},
                        subject="t1")
        self.n.on_event("task.done", {"summary": "again", "task_class": "short"},
                        subject="t1")
        self.assertEqual(len(self.sink.lines), 2)

    def test_silent_events_never_spoken(self):
        u = self.n.on_event("policy.decision", {"intent": "GROW"}, subject="t1")
        self.assertIsNone(u)
        self.assertEqual(self.sink.lines, [])

    def test_proof_of_honesty_passes_on_real_ledger(self):
        ledger = AuditLedger.get_instance()
        audit("lead_hunt.candidates", subject="t9", count=14)
        audit("qa.passed", subject="t9", screenshots=10, fps=60.0)
        n = Narrator(speak=self.sink, ledger=ledger)
        n.on_event("lead_hunt.candidates", {"count": 14}, subject="t9")
        n.on_event("qa.passed", {"screenshots": 10, "fps": 60.0}, subject="t9")
        self.assertTrue(proof_of_honesty(n, ledger))

    def test_proof_of_honesty_catches_unbacked_speech(self):
        ledger = AuditLedger.get_instance()
        n = Narrator(speak=self.sink, ledger=ledger)
        # speak about an event that was NEVER logged
        n.spoken.append({"ts": __import__("time").time(), "task": "ghost-task",
                         "event": "lead_hunt.candidates", "utterance": "Found 999..."})
        self.assertFalse(proof_of_honesty(n, ledger))


class TestTaskRunner(unittest.TestCase):

    def setUp(self):
        self.sink = RecordingSink()
        self.n = Narrator(speak=self.sink)
        self.runner = TaskRunner(narrator=self.n)

    def test_reference_parity_flow(self):
        # Narrator on a stepped clock so the long-class 8s throttle doesn't
        # hide milestones within a microsecond test run (real events, real
        # budget rules — just deterministic time).
        clock = {"t": 100.0}
        n = Narrator(speak=self.sink, now=lambda: clock["t"])
        base_on_event = n.on_event
        def timed_on_event(*args, **kwargs):
            clock["t"] += 10.0
            return base_on_event(*args, **kwargs)
        n.on_event = timed_on_event
        self.runner = TaskRunner(narrator=n)
        async def go():
            return await self.runner.run(
                "demo-seek", "long", "finding restaurants without websites",
                find_and_build_demo_flow(
                    {"count": 6, "top_business": "Salt"},
                    {"screenshots": 10, "fps": 60.0},
                    "https://salt-concept.vercel.app"))
        events = asyncio.run(go())
        kinds = [e.kind for e in events]
        self.assertEqual(kinds[0], "acknowledged")
        self.assertEqual(kinds[-1], "done")
        joined = " ".join(self.sink.lines)
        self.assertIn("I'm doing it now", joined)               # long-class ack
        self.assertIn("Found 6 businesses", joined)             # milestone
        self.assertIn("QA passed — 10 screenshots clean, 60.0 frames per second", joined)
        self.assertIn("Demo's live at https://salt-concept.vercel.app", joined)
        self.assertIn("Done — demo live, draft email ready", joined)
        # §14.3: the tape is provably event-backed
        self.assertTrue(proof_of_honesty(self.n, AuditLedger.get_instance()))

    def test_barge_in_reports_completed_vs_suspended(self):
        async def work():
            yield {"event": "lead_hunt.candidates", "count": 3}
            await asyncio.sleep(0.05)  # gap where the barge-in lands
            yield {"event": "demo.build_started", "business": "Salt"}
            await asyncio.sleep(0.05)
            yield {"event": "qa.passed", "screenshots": 10, "fps": 60.0}
            yield {"event": "task.done", "summary": "never"}
        async def go():
            task = asyncio.create_task(self.runner.run(
                "t", "short", "demo run", work()))
            await asyncio.sleep(0.02)   # first step processed, barge-in mid-stream
            self.runner.interrupt()
            return await task
        events = asyncio.run(go())
        self.assertEqual(events[-1].kind, "interrupted")
        self.assertEqual(events[-1].payload["completed"], 1)
        self.assertGreaterEqual(events[-1].payload["suspended"], 1)
        joined = " ".join(self.sink.lines)
        self.assertIn("Stopped. 1 step(s) finished", joined)
        self.assertIn("awaiting orders", joined)
        self.assertNotIn("QA passed", joined)   # interrupted work is NOT narrated as done

    def test_qa_failure_refuses_to_ship(self):
        async def work():
            yield {"event": "qa.failed", "task_class": "short"}
        async def go():
            return await self.runner.run("t", "short", "build", work())
        asyncio_run(go())
        self.assertIn("I'm not shipping this", " ".join(self.sink.lines))


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
