"""P0 tests: halt switch, durable job queue, audit ledger (Roadmap §0.5, §3.1, §3.6)."""
import asyncio
import os
import tempfile
import unittest

TMP = tempfile.mkdtemp(prefix="ghost-p0-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, "audit.db")
os.environ["GHOST_QUEUE_DB_PATH"] = os.path.join(TMP, "queue.db")
os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALTED")
os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALTED.json")

from ghost.core.halt import (assert_not_halted, halt_state, is_halted,
                             HaltActive, resume, trip)
from ghost.core.jobqueue import JobQueue
from ghost.core.audit import AuditLedger


class TestHaltSwitch(unittest.TestCase):

    def tearDown(self):
        resume()

    def test_halt_blocks_outbound(self):
        self.assertFalse(is_halted())
        trip(reason="operator", by="test")
        self.assertTrue(is_halted())
        with self.assertRaises(HaltActive):
            assert_not_halted("send_email")
        state = halt_state()
        self.assertEqual(state["reason"], "operator")
        self.assertEqual(state["by"], "test")

    def test_resume_clears(self):
        trip()
        resume()
        self.assertFalse(is_halted())
        assert_not_halted("send_email")  # must not raise

    def test_trip_is_idempotent_with_trail(self):
        first = trip(reason="heartbeat_lost")
        second = trip(reason="operator")
        self.assertTrue(is_halted())
        self.assertNotEqual(first["halt_id"], second["halt_id"])


class TestJobQueue(unittest.TestCase):

    def setUp(self):
        path = os.path.join(TMP, f"queue-{os.urandom(4).hex()}.db")
        self.q = JobQueue(db_path=path)

    def test_idempotency_key_prevents_double_run(self):
        j1 = self.q.enqueue("send_email", {"to": "a@b.c"}, idempotency_key="lead-42-email")
        j2 = self.q.enqueue("send_email", {"to": "a@b.c"}, idempotency_key="lead-42-email")
        self.assertEqual(j1, j2)  # same job returned, not a duplicate
        self.assertEqual(self.q.stats().get("pending"), 1)

    def test_successful_run(self):
        self.q.enqueue("noop", {"x": 1})
        ran = []
        n = asyncio.run(self.q.run_worker({"noop": lambda p: ran.append(p) or asyncio.sleep(0)},
                                          idle_exit_after=0.5))
        self.assertEqual(n, 1)
        self.assertEqual(ran, [{"x": 1}])
        self.assertEqual(self.q.stats().get("done"), 1)

    def test_retry_then_success(self):
        self.q.enqueue("flaky", {}, max_attempts=3)
        calls = {"n": 0}

        async def flaky(p):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            return "ok"

        # Shrink backoff so the test is fast.
        import ghost.core.jobqueue as jq
        orig = jq.random.uniform
        jq.random.uniform = lambda a, b: 0.0
        try:
            n = asyncio.run(self.q.run_worker({"flaky": flaky}, idle_exit_after=0.5,
                                              poll_interval=0.05))
        finally:
            jq.random.uniform = orig
        self.assertEqual(n, 1)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(self.q.stats().get("done"), 1)

    def test_poison_job_dead_letters_and_pings(self):
        self.q.enqueue("always_fails", {}, max_attempts=2)
        pings = []

        async def bad(p):
            raise ValueError("permanently broken")

        import ghost.core.jobqueue as jq
        orig = jq.random.uniform
        jq.random.uniform = lambda a, b: 0.0
        try:
            asyncio.run(self.q.run_worker(
                {"always_fails": bad}, idle_exit_after=0.5, poll_interval=0.05,
                dead_letter_ping=pings.append))
        finally:
            jq.random.uniform = orig
        self.assertEqual(self.q.stats().get("dead"), 1)
        self.assertEqual(len(pings), 1)
        self.assertIn("permanently broken", pings[0]["error"])
        self.assertEqual(self.q.dead_letters()[0]["kind"], "always_fails")

    def test_no_handler_dead_letters_fast(self):
        self.q.enqueue("unknown_kind", {})
        asyncio.run(self.q.run_worker({}, idle_exit_after=0.5))
        self.assertEqual(self.q.stats().get("dead"), 1)

    def test_halt_check_stops_at_safe_point(self):
        self.q.enqueue("noop", {})
        self.q.enqueue("noop", {})
        stop = {"hit": False}

        def halt_now():
            # halt after first job completes: worker must stop, second stays pending
            if stop["hit"]:
                return True
            stop["hit"] = True
            return False

        async def noop(p):
            await asyncio.sleep(0)

        n = asyncio.run(self.q.run_worker({"noop": noop}, idle_exit_after=None,
                                          halt_check=halt_now))
        self.assertEqual(n, 1)
        self.assertEqual(self.q.stats().get("pending"), 1)  # not lost, paused

    def test_crashed_lease_is_reclaimed(self):
        """Simulate a worker dying mid-job: lease expires, next worker picks it up."""
        self.q.enqueue("slow", {})
        self.q._claim_next("dead-worker")
        # The job is now claimed with a live lease held by the dead worker.
        self.assertEqual(self.q.stats().get("claimed"), 1)
        # Force the lease into the past (worker died, never renewed).
        with self.q._conn() as conn:
            conn.execute("UPDATE jobs SET lease_until = 1.0 WHERE lease_owner = 'dead-worker'")
        n = asyncio.run(self.q.run_worker(
            {"slow": lambda p: asyncio.sleep(0)}, owner="fresh-worker", idle_exit_after=0.5))
        self.assertEqual(n, 1)
        self.assertEqual(self.q.stats().get("done"), 1)


class TestAuditLedger(unittest.TestCase):

    def test_record_query_summary(self):
        ledger = AuditLedger(os.path.join(TMP, f"audit-{os.urandom(4).hex()}.db"))
        ledger.record("job.done", subject="j1", kind="build")
        ledger.record("job.done", subject="j2", kind="qa")
        ledger.record("policy.decision", intent="GROW")
        rows = ledger.query(event_type="job.done")
        self.assertEqual(len(rows), 2)
        summary = ledger.day_summary()
        self.assertEqual(summary["events"]["job.done"], 2)
        self.assertEqual(summary["events"]["policy.decision"], 1)


if __name__ == "__main__":
    unittest.main()
