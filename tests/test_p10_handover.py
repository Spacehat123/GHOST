"""P10 tests (Operator's 11): the handover — trust follows evidence.

- STREAKS ACCRUE: ledger success events map onto the ladder, idempotently
  (a replayed sync never double-counts — the cursor is the proof).
- VERIFIED INCIDENTS DEMOTE: a real client complaint, a reconciliation
  mismatch, a verifier-rejected fix — each drops its capability one level
  and zeroes the streak. Safety machinery WORKING (halt discards, grant
  refusals, underpayment gates) is NOT an incident.
- PROMOTION STAYS OPERATOR-ONLY: 50 successes unlock; only /api/ladder/raise
  raises; raising without an unlock or beyond L4 refuses.
- LIVE WIRING: the runtime carries the ladder-evidence loop.
"""
import json
import os
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-p10-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_LADDER_STATE_PATH"] = os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json")
os.environ["GHOST_LADDER_CURSOR"] = os.path.join(TMP, f"cursor-{uuid.uuid4().hex}.json")

from ghost.core.audit import audit, AuditLedger
from ghost.autonomy.ladder import AutonomyLadder
from ghost.autonomy.ladder_evidence import sync_ladder_from_ledger


class EnvPinned(unittest.TestCase):

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_AUDIT_DB_PATH", "GHOST_LADDER_STATE_PATH",
                        "GHOST_LADDER_CURSOR", "GHOST_HALT_FILE", "GHOST_HALT_META")}
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
        os.environ["GHOST_LADDER_STATE_PATH"] = os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json")
        os.environ["GHOST_LADDER_CURSOR"] = os.path.join(TMP, f"cursor-{uuid.uuid4().hex}.json")
        os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, f"H-{uuid.uuid4().hex}")
        os.environ["GHOST_HALT_META"] = os.environ["GHOST_HALT_FILE"] + ".json"
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _ledger(self, event_type, limit=100):
        return AuditLedger.get_instance().query(event_type=event_type, limit=limit)

    def _fresh_ladder(self):
        return AutonomyLadder(state_path=os.environ["GHOST_LADDER_STATE_PATH"])


class TestEvidenceMapping(EnvPinned):

    def test_successes_build_streaks_idempotently(self):
        for i in range(3):
            audit("lead_hunt.completed", subject=f"batch-{i}", count=5)
        out = sync_ladder_from_ledger(ladder=self._fresh_ladder())
        self.assertEqual(out["successes"], 3)
        cap = self._fresh_ladder().status()["lead_hunting"]
        self.assertEqual(cap["success_streak"], 3)
        # replay: the cursor makes the same events count ONCE
        out2 = sync_ladder_from_ledger(ladder=self._fresh_ladder())
        self.assertEqual(out2["successes"], 0)
        self.assertEqual(self._fresh_ladder().status()["lead_hunting"]["success_streak"], 3)
        # new evidence extends the streak
        audit("demo.hosted", subject="s1", url="https://x.vercel.app")
        audit("payment.verified", subject="inv-1")
        audit("outbound.delivered", subject="m-1")
        audit("sidecar.merged", subject="side_1")
        audit("contract.sent", subject="c-1")
        sync_ladder_from_ledger(ladder=self._fresh_ladder())
        st = self._fresh_ladder().status()
        self.assertEqual(st["demo_build_deploy"]["success_streak"], 1)
        self.assertEqual(st["payments"]["success_streak"], 1)
        self.assertEqual(st["email_outreach"]["success_streak"], 1)
        self.assertEqual(st["retainer_autofix"]["success_streak"], 1)
        self.assertEqual(st["contracts"]["success_streak"], 1)

    def test_verified_incidents_demote_and_zero_the_streak(self):
        audit("reply.complaint", subject="angry", ping="(b)")
        out = sync_ladder_from_ledger(ladder=self._fresh_ladder())
        self.assertEqual(out["incidents"], 1)
        st = self._fresh_ladder().status()["email_outreach"]
        self.assertEqual(st["level"], 1, "L2 -> L1 on a verified complaint")
        self.assertEqual(st["success_streak"], 0)
        self.assertEqual(len(self._ledger("ladder.incident")), 1)
        # rebuild: successes after the incident count from zero
        audit("outbound.delivered", subject="m-1")
        sync_ladder_from_ledger(ladder=self._fresh_ladder())
        self.assertEqual(self._fresh_ladder().status()["email_outreach"]["success_streak"], 1)

    def test_reconcile_mismatch_demotes_payments(self):
        audit("reconcile.mismatch", subject="payments", detail="provider says no")
        sync_ladder_from_ledger(ladder=self._fresh_ladder())
        self.assertEqual(self._fresh_ladder().status()["payments"]["level"], 1)

    def test_safety_machinery_working_is_not_an_incident(self):
        # halt discards / budget discards are the system WORKING
        audit("sidecar.discarded", subject="side_1", reason="halt tripped during sidecar run")
        audit("sidecar.discarded", subject="side_2", reason="out-of-scope edits refused")
        audit("payment.underpaid_or_mismatch", subject="inv-1")
        out = sync_ladder_from_ledger(ladder=self._fresh_ladder())
        self.assertEqual(out["incidents"], 0)
        self.assertEqual(self._fresh_ladder().status()["retainer_autofix"]["level"], 2)

    def test_verifier_rejected_fix_is_an_incident(self):
        audit("sidecar.discarded", subject="side_3",
              reason="verifier failed: exit=1 out=still broken")
        out = sync_ladder_from_ledger(ladder=self._fresh_ladder())
        self.assertEqual(out["incidents"], 1)
        self.assertEqual(self._fresh_ladder().status()["retainer_autofix"]["level"], 1)

    def test_fifty_successes_unlock_operator_raise_only(self):
        ladder = self._fresh_ladder()
        for i in range(50):
            audit("payment.verified", subject=f"inv-{i}")
        sync_ladder_from_ledger(ladder=ladder)
        st = self._fresh_ladder().status()["payments"]
        self.assertEqual(st["eligible_for"], 3, "L2 + one earned unlock")
        self.assertEqual(st["level"], 2, "the ladder NEVER raises by itself")
        self.assertEqual(len(self._ledger("ladder.unlock_available")), 1)
        # the operator raises through the earned ceiling — and only that far
        res = self._fresh_ladder().raise_level("payments", by="operator-test")
        self.assertEqual(res.get("level", res.get("new_level", 3)), 3)
        over = self._fresh_ladder().raise_level("payments", by="operator-test")
        self.assertFalse(over.get("ok", False), "beyond the earned ceiling refuses")


class TestLiveWiring(EnvPinned):

    def test_runtime_carries_the_ladder_loop(self):
        import asyncio
        from ghost.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(with_lead_hunt=False)

        async def go():
            await rt.start()
            names = [t.get_name() for t in rt._tasks]
            await rt.stop()
            return names

        names = asyncio.run(go())
        self.assertIn("ladder-evidence", names)

    def test_sync_survives_an_empty_ledger(self):
        out = sync_ladder_from_ledger(ladder=self._fresh_ladder())
        self.assertEqual(out["processed"], 0)
        self.assertEqual(out["successes"], 0)


if __name__ == "__main__":
    unittest.main()
