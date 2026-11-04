"""Unit Tests for Ghost Flow Router."""
import unittest
from ghost.workers.flow_router import FlowRouter


class TestFlowRouter(unittest.TestCase):

    def test_default_flow_is_standard(self):
        res = FlowRouter.choose_flow(
            lead_slug="generic-dry-cleaners",
            lead_data={
                "category": "dry cleaning",
                "lead_score": 45.0
            }
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["flow"], "standard")
        self.assertFalse(res["needs_operator_approval"])

    def test_operator_override_motion_wins(self):
        res = FlowRouter.choose_flow(
            lead_slug="regular-cafe",
            operator_choice="motion",
            lead_data={"category": "cafe"}
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["flow"], "motion")
        self.assertFalse(res["needs_operator_approval"])
        self.assertTrue(any("operator" in r.lower() for r in res["reasons"]))

    def test_operator_override_3d_reports_missing_glb_blocker(self):
        res = FlowRouter.choose_flow(
            lead_slug="mayfair-tailors",
            operator_choice="3d",
            lead_data={"category": "tailor", "has_licensed_glb": False}
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["flow"], "3d")
        self.assertGreater(len(res["blockers"]), 0)
        self.assertTrue(any("glb" in b.lower() for b in res["blockers"]))

    def test_motion_proposed_for_top_visual_candidate(self):
        res = FlowRouter.choose_flow(
            lead_slug="atelier-detailing",
            lead_data={
                "category": "automotive_detailing",
                "business_name": "Atelier Detailing Studio",
                "lead_score": 85.0
            },
            batch_context={"batch_rank": 2, "current_motion_count": 1}
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["flow"], "motion")
        # Rule: Never upgrade automatically without operator approval
        self.assertTrue(res["needs_operator_approval"])
        self.assertTrue(any("visual sector" in r.lower() for r in res["reasons"]))

    def test_batch_quota_respected_for_motion(self):
        res = FlowRouter.choose_flow(
            lead_slug="luxury-spa-ltd",
            lead_data={
                "category": "salon_spa",
                "lead_score": 90.0
            },
            batch_context={"batch_rank": 6, "current_motion_count": 5}
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["flow"], "standard")
        self.assertTrue(any("quota" in r.lower() for r in res["reasons"]))

    def test_emergency_trade_never_gets_motion(self):
        res = FlowRouter.choose_flow(
            lead_slug="emergency-locksmith-247",
            lead_data={
                "category": "locksmith emergency services",
                "lead_score": 88.0
            },
            batch_context={"batch_rank": 1}
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["flow"], "standard")
        self.assertGreater(len(res["blockers"]), 0)
