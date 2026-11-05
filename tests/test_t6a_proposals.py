"""T6a tests — proposal taxonomy, deterministic work chooser, sim fixtures.

Exit items this covers (ROADMAP_TOTAL_AUTONOMY T6a):
- every proposal action is typed with a registry-backed risk class; a brain
  that declares the wrong risk class, an unknown action, or missing required
  args is REFUSED, not executed
- operator-only flags (operator_approved) cannot be smuggled past the schema
- the work chooser is deterministic, respects the priority order, and never
  proposes what the policy gates would refuse
- sim fixtures: same seed -> byte-identical 50-lead pack
"""
import unittest

from ghost.brain.proposals import (
    EXTERNAL_EFFECT, PIPELINE_EFFECT, READ, SANDBOXED_SHELL,
    Proposal, PROPOSAL_REGISTRY, to_proposal,
)
from ghost.control.work_chooser import choose_work
from ghost.policy.engine import StateSnapshot
from ghost.sim.fixtures import fixture_leads, fixture_replies


def snap(**over):
    base = dict(halted=False, open_complaints=0, incidents_over_slo=0,
                client_replies_waiting=0, hot_leads_waiting=0,
                quota_remaining_pct=100.0, last_closed_deal_age_h=None,
                within_business_hours=True)
    base.update(over)
    return StateSnapshot(**base)


class TestProposalSchema(unittest.TestCase):

    def test_revenue_cycle_actions_registered_with_correct_risk(self):
        self.assertEqual(PROPOSAL_REGISTRY["leads.scout"]["risk_class"], PIPELINE_EFFECT)
        self.assertEqual(PROPOSAL_REGISTRY["demo.deploy"]["risk_class"], EXTERNAL_EFFECT)
        self.assertEqual(PROPOSAL_REGISTRY["message.send_email"]["risk_class"], EXTERNAL_EFFECT)
        self.assertEqual(PROPOSAL_REGISTRY["demo.deploy"]["grant_capability"], "deploy")
        self.assertEqual(PROPOSAL_REGISTRY["message.send_email"]["grant_capability"],
                         "outbound_email")

    def test_validated_proposal_passes(self):
        p = Proposal(action="demo.deploy", args={"slug": "grasshopper-concept"},
                     risk_class=EXTERNAL_EFFECT).validated()
        self.assertEqual(p.action, "demo.deploy")

    def test_unknown_action_refused(self):
        with self.assertRaises(ValueError):
            Proposal(action="launch_nukes", risk_class=READ).validated()

    def test_risk_class_mismatch_refused(self):
        # the brain declaring READ for a deploy is a lie the schema catches
        with self.assertRaises(ValueError):
            Proposal(action="demo.deploy", args={"slug": "x"}, risk_class="READ").validated()

    def test_missing_required_args_refused(self):
        with self.assertRaises(ValueError):
            Proposal(action="demo.deploy", args={}, risk_class=EXTERNAL_EFFECT).validated()

    def test_operator_flag_cannot_be_smuggled(self):
        # mechanism test with a temporary registry entry (system.run's control
        # is the sandbox itself; operator flags belong on identity/destructive
        # capabilities like download_glb — which keep theirs)
        PROPOSAL_REGISTRY["test.danger"] = {
            "risk_class": READ, "executor": "x", "verifier": "y",
            "required_args": [], "requires_operator_flag": True}
        try:
            with self.assertRaises(ValueError):
                Proposal(action="test.danger", args={}, risk_class=READ).validated()
            with self.assertRaises(ValueError):
                Proposal(action="test.danger", args={"operator_approved": False},
                         risk_class=READ).validated()
            Proposal(action="test.danger", args={"operator_approved": True},
                     risk_class=READ).validated()   # explicit flag passes
        finally:
            del PROPOSAL_REGISTRY["test.danger"]

    def test_whatsapp_is_drafts_only_by_design(self):
        reg = PROPOSAL_REGISTRY["message.send_whatsapp"]
        self.assertEqual(reg["executor"], "NOT_CONVERTED_drafts_only",
                         "operator identity channel stays manual until T9")

    def test_legacy_tool_mapping(self):
        p = to_proposal("deploy_demo_vercel", {"slug": "grasshopper-concept"})
        self.assertEqual(p.action, "demo.deploy")
        self.assertEqual(p.risk_class, EXTERNAL_EFFECT)
        p.validated()   # well-formed
        with self.assertRaises(ValueError):
            to_proposal("capture_screen", {})   # not mapped -> stays legacy


class TestWorkChooser(unittest.TestCase):

    def test_priority_order_complaints_first(self):
        items = choose_work(snap(open_complaints=2, incidents_over_slo=1,
                                 client_replies_waiting=3))
        self.assertEqual([i.kind for i in items],
                         ["serve_complaints", "serve_retainers", "follow_up"])
        self.assertEqual(items[0].gate, "open_complaints>0")

    def test_hunt_only_when_all_gates_open(self):
        items = choose_work(snap())
        self.assertEqual([i.kind for i in items], ["hunt"])
        # stale deal age -> no hunt
        self.assertEqual(choose_work(snap(last_closed_deal_age_h=2.0)), [])
        # low quota -> no hunt
        self.assertEqual(choose_work(snap(quota_remaining_pct=50.0)), [])
        # outside business hours -> no hunt (cold outreach never at night)
        self.assertEqual(choose_work(snap(within_business_hours=False)), [])

    def test_halt_empties_the_board(self):
        self.assertEqual(choose_work(snap(halted=True, open_complaints=9,
                                          incidents_over_slo=9)), [],
                         "the throat-cut handle empties the board instantly")

    def test_build_demos_ranks_last(self):
        items = choose_work(snap(hot_leads_waiting=4, client_replies_waiting=1))
        self.assertEqual([i.kind for i in items], ["follow_up", "build_demo"])

    def test_deterministic_same_snapshot_same_plan(self):
        s = snap(open_complaints=1, hot_leads_waiting=2)
        a = choose_work(s)
        b = choose_work(snap(open_complaints=1, hot_leads_waiting=2))
        self.assertEqual([(i.kind, i.reason, i.gate) for i in a],
                         [(i.kind, i.reason, i.gate) for i in b])


class TestSimFixtures(unittest.TestCase):

    def test_same_seed_byte_identical(self):
        import json
        a = json.dumps(fixture_leads(50), sort_keys=True)
        b = json.dumps(fixture_leads(50), sort_keys=True)
        self.assertEqual(a, b)
        self.assertEqual(len(fixture_leads(50)), 50)

    def test_fixture_shape_matches_crm_leads(self):
        lead = fixture_leads(5)[0]
        for col in ("id", "slug", "name", "address", "phone", "website", "rating",
                    "review_count", "google_category", "entity_type",
                    "score_revenue", "score_fit", "score_friction", "gaps_json",
                    "status", "needs_confirmation", "demo_url"):
            self.assertIn(col, lead, f"fixture must carry crm_leads column {col}")

    def test_reply_pack_spans_categories(self):
        replies = fixture_replies()
        self.assertGreaterEqual(len(replies), 5)

    def test_fake_clock_never_reads_wall_time(self):
        from ghost.sim.clock import FakeClock, ist_hour
        c = FakeClock()
        t1 = c.time()
        c.advance(3600)
        self.assertEqual(c.time() - t1, 3600.0)
        self.assertEqual(c.strftime("%Y-%m-%d"), "2026-11-06")
        self.assertIsInstance(ist_hour(c), int)


if __name__ == "__main__":
    unittest.main()
