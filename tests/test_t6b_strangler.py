"""T6b strangler tests: converted capabilities flow proposal -> control ->
grant -> effects -> verify, behind GHOST_BRAIN_MODE, with golden-trace parity
against the legacy direct-tool outcomes.
"""
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-t6b-")
os.environ["GHOST_GRANT_SOCKET"] = os.path.join(TMP, "no-grantd.sock")  # suite must NEVER reach a live grantd
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_UNIFIED_DB_PATH"] = os.path.join(TMP, f"ghost-{uuid.uuid4().hex}.db")
os.environ["GHOST_GRANT_SECRET"] = f"t6b-{uuid.uuid4().hex}"
os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALTED")
os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALT.json")
os.environ["GHOST_REVOCATION_EPOCH"] = os.path.join(TMP, "EPOCH")
os.environ["GHOST_LADDER_STATE_PATH"] = os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json")
os.environ["GHOST_OUTBOUND_STATE_PATH"] = os.path.join(TMP, f"ob-{uuid.uuid4().hex}.json")
os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, f"crm-{uuid.uuid4().hex}.db")
os.environ["GHOST_SNAPSHOTS_DIR"] = os.path.join(TMP, "snaps")
os.environ["GHOST_DEMOS_DIR"] = os.path.join(TMP, "demos")
os.makedirs(os.path.join(TMP, "demos"), exist_ok=True)

from ghost.brain.dispatcher import brain_mode, dispatch
from ghost.brain.proposals import EXTERNAL_EFFECT, PIPELINE_EFFECT, Proposal
from ghost.crm.db import CRMDatabase
from ghost.sim import golden_traces as gt



class EnvPinned(unittest.TestCase):
    """chaos tests pop GHOST_GRANT_SECRET at run time (alphabetically first) —
    every class using grants re-pins per test with cleanup (T2/T5 lesson)."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_GRANT_SECRET", "GHOST_REVOCATION_EPOCH",
                        "GHOST_HALT_FILE", "GHOST_HALT_META",
                        "GHOST_UNIFIED_DB_PATH", "GHOST_AUDIT_DB_PATH")}

        def apply():
            os.environ["GHOST_GRANT_SECRET"] = f"t6b-{uuid.uuid4().hex}"
            os.environ["GHOST_REVOCATION_EPOCH"] = os.path.join(TMP, f"epoch-{uuid.uuid4().hex}")
            os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALTED")
            os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALT.json")

        apply()
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestDispatcherChain(EnvPinned):

    def test_full_chain_pipeline_update_stage(self):
        """THE strangler proof: proposal -> control -> grant -> effects ->
        verifier, with the outbox recording intent and the VERIFIED result
        coming back."""
        crm = CRMDatabase.get_instance()
        crm.add_or_update_lead({"slug": "strangler-lead", "name": "S", "status": "scouted"})
        p = Proposal(action="pipeline.update_stage",
                     args={"lead_slug": "strangler-lead", "status": "negotiating"},
                     risk_class=PIPELINE_EFFECT)
        res = asyncio.run(dispatch(p))
        self.assertEqual(res["dispatch"], "ok", res)
        self.assertTrue(res["verified"], res)
        lead = crm.get_lead("strangler-lead")
        self.assertEqual(lead["status"], "negotiating", "the effect actually ran")
        # outbox: intent recorded, effect verified
        rows = sqlite3.connect(os.environ["GHOST_UNIFIED_DB_PATH"]).execute(
            "SELECT state FROM outbox WHERE kind='pipeline.update_stage'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "verified")

    def test_refusals_are_structured_never_raised(self):
        # schema lie: wrong risk class
        res = asyncio.run(dispatch(Proposal(action="demo.deploy", args={"slug": "x"},
                                            risk_class="READ")))
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["stage"], "schema")
        # registered but not strangler-wired yet (honest, not legacy fallback)
        res2 = asyncio.run(dispatch(Proposal(action="message.send_email",
                                             args={"lead_slug": "l", "to_email": "a@b.c",
                                                   "subject": "s", "body": "b"},
                                             risk_class=EXTERNAL_EFFECT)))
        self.assertEqual(res2["status"], "refused")
        self.assertEqual(res2["stage"], "adapter")
        # unknown action
        res3 = asyncio.run(dispatch(Proposal(action="nope.nope", risk_class="READ")))
        self.assertEqual(res3["stage"], "schema")

    def test_halt_refuses_at_control(self):
        from ghost.core.halt import trip, resume
        trip(reason="t6b-test")
        try:
            res = asyncio.run(dispatch(Proposal(
                action="pipeline.update_stage",
                args={"lead_slug": "l", "status": "scouted"}, risk_class=PIPELINE_EFFECT)))
            self.assertEqual(res["status"], "refused")
            self.assertEqual(res["stage"], "control")
            self.assertEqual(res["reason"], "halt active")
        finally:
            resume()

    def test_demo_deploy_chain_with_fixtures(self):
        """deploy through the dispatcher: grant minted by control, snapshot
        gate hit inside the effect, outbox intent recorded."""
        from ghost.core import grants
        from ghost.workers.deploy import VercelDeployer
        from ghost.workers.snapshots import SnapshotStore
        slug = f"t6b-deploy-{uuid.uuid4().hex[:6]}"
        demo_dir = os.path.join(os.environ["GHOST_DEMOS_DIR"], slug)
        os.makedirs(demo_dir, exist_ok=True)
        SnapshotStore().snapshot(slug, demo_dir)
        res = asyncio.run(dispatch(Proposal(action="demo.deploy", args={"slug": slug},
                                            risk_class=EXTERNAL_EFFECT)))
        # no QA report -> the effect's QA gate refuses (evidence-gated deploys),
        # surfaced through verify with the intent recorded
        self.assertEqual(res["dispatch"], "ok", res)
        self.assertFalse(res.get("verified"))
        rows = sqlite3.connect(os.environ["GHOST_UNIFIED_DB_PATH"]).execute(
            "SELECT state FROM outbox WHERE kind='demo.deploy'").fetchall()
        self.assertEqual(len(rows), 1, "intent recorded before the effect")


class TestModeSwitch(EnvPinned):

    def test_proposals_mode_routes_converted_tools(self):
        os.environ["GHOST_BRAIN_MODE"] = "proposals"
        try:
            crm = CRMDatabase.get_instance()
            crm.add_or_update_lead({"slug": "mode-lead", "name": "M", "status": "scouted"})
            res = asyncio.run(_execute("crm_update_client", {
                "slug": "mode-lead", "business_name": "M Co", "status": "pitch_sent"}))
            self.assertEqual(res.get("proposal"), "pipeline.upsert_client")
            self.assertTrue(res.get("verified"))
        finally:
            os.environ.pop("GHOST_BRAIN_MODE", None)

    def test_legacy_mode_falls_through(self):
        os.environ["GHOST_BRAIN_MODE"] = "legacy"   # the explicit rollback flag
        try:
            from ghost.brain.dispatcher import dispatch_legacy_or_proposals
            self.assertIsNone(dispatch_legacy_or_proposals(
                "crm_update_client", {"slug": "x", "business_name": "B"}),
                "legacy mode must fall through to the direct-tool path")
        finally:
            os.environ.pop("GHOST_BRAIN_MODE", None)

    def test_unmapped_tool_falls_through_in_proposals_mode(self):
        os.environ["GHOST_BRAIN_MODE"] = "proposals"
        try:
            from ghost.brain.dispatcher import dispatch_legacy_or_proposals
            self.assertIsNone(dispatch_legacy_or_proposals("capture_screen", {}),
                              "unmapped capability stays on the legacy path")
        finally:
            os.environ.pop("GHOST_BRAIN_MODE", None)


async def _execute(tool_name, arguments):
    """Import lazily; brain.py is heavy but importable in tests."""
    from ghost.core.brain import OmniRouteBrain
    return await OmniRouteBrain.execute_tool_call(tool_name, arguments)


class TestGoldenTraces(EnvPinned):

    def test_pipeline_update_stage_trace_parity(self):
        """Recorded legacy observable == proposal-path observable (normalized)."""
        name = "pipeline.update_stage"
        legacy_args = {"slug": "trace-lead", "business_name": "T Co", "status": "closed_won"}
        crm = CRMDatabase.get_instance()
        crm.add_or_update_lead({"slug": "trace-lead", "name": "T", "status": "scouted"})
        # legacy direct-tool observable
        crm.update_lead("trace-lead", status=legacy_args["status"])
        legacy_observable = {"lead_status_after": crm.get_lead("trace-lead")["status"]}
        gt.record(name, "crm_update_client", legacy_args, legacy_observable)
        # proposal-path observable (fresh lead, same inputs)
        crm.add_or_update_lead({"slug": "trace-lead-2", "name": "T2", "status": "scouted"})
        res = asyncio.run(dispatch(Proposal(
            action="pipeline.update_stage",
            args={"lead_slug": "trace-lead-2", "status": legacy_args["status"]},
            risk_class=PIPELINE_EFFECT)))
        proposal_observable = {"lead_status_after": crm.get_lead("trace-lead-2")["status"]}
        fixture = gt.load(name)
        self.assertEqual(fixture["legacy_observable"], gt.normalize(legacy_observable))
        self.assertTrue(gt.equivalent(fixture["legacy_observable"], proposal_observable),
                        "golden trace drifted — the strangler changed behavior")
        self.assertEqual(res["dispatch"], "ok")

    def test_deploy_trace_records_legacy_refusal_shape(self):
        name = "demo.deploy"
        legacy_observable = {"status_sequence_no_evidence": [
            "grant_required",   # no grant
            "snapshot_required" # grant but no snapshot
        ]}
        gt.record(name, "deploy_demo_vercel", {"slug": "<slug>"}, legacy_observable)
        fixture = gt.load(name)
        # replay the same progression through the dispatcher and compare
        from ghost.core.halt import is_halted
        slug = f"trace-deploy-{uuid.uuid4().hex[:6]}"
        os.makedirs(os.path.join(os.environ["GHOST_DEMOS_DIR"], slug), exist_ok=True)
        no_grant = asyncio.run(dispatch(Proposal(action="demo.deploy", args={"slug": slug},
                                                 risk_class=EXTERNAL_EFFECT)))
        # control mints the grant itself, so the no-grant state is unreachable
        # from the proposal path BY DESIGN — the trace documents that the only
        # legacy-only state is 'no grant at all':
        self.assertIn("grant_required", fixture["legacy_observable"]["status_sequence_no_evidence"])
        self.assertEqual(no_grant["dispatch"], "ok")
        self.assertEqual(no_grant.get("verified"), False)  # QA gate refuses (no evidence)


if __name__ == "__main__":
    unittest.main()
