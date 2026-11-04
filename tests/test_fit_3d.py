"""Unit Tests for Ghost 3D Fit Assessment Gate."""
import unittest
from ghost.workers.fit_3d import Fit3DAssessor


class TestFit3D(unittest.TestCase):

    def test_blocker_emergency_trade_returns_no(self):
        res = Fit3DAssessor.assess(
            lead_slug="apex-plumbing-solutions",
            lead_data={
                "category": "emergency plumbing and drain service",
                "deal_value": 2500.0,
                "has_licensed_glb": True
            }
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["recommendation"], "no")
        self.assertGreater(len(res["blockers"]), 0)
        self.assertIn("Emergency/local convenience trade", res["blockers"][0])

    def test_blocker_slow_connection_returns_no(self):
        res = Fit3DAssessor.assess(
            lead_slug="topaz-detailing-slow",
            lead_data={
                "category": "automotive detailing",
                "connection_profile": "2g_3g_slow",
                "has_licensed_glb": True
            }
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["recommendation"], "no")
        self.assertTrue(any("slow mobile" in b.lower() for b in res["blockers"]))

    def test_blocker_no_licensed_glb_returns_no(self):
        res = Fit3DAssessor.assess(
            lead_slug="monaco-jewelers",
            lead_data={
                "category": "jewelry",
                "deal_value": 5000.0,
                "has_licensed_glb": False
            }
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["recommendation"], "no")
        self.assertTrue(any("permissive" in b.lower() or "usable glb" in b.lower() for b in res["blockers"]))

    def test_high_ticket_detailing_proposes_3d(self):
        res = Fit3DAssessor.assess(
            lead_slug="topaz-detailing",
            lead_data={
                "category": "automotive_detailing",
                "business_name": "Topaz Detailing London",
                "deal_value": 4500.0,
                "high_ticket": True,
                "review_count": 85,
                "rating": 4.9,
                "has_licensed_glb": True
            }
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["recommendation"], "propose")
        self.assertGreaterEqual(res["score"], 60)
        self.assertEqual(res["hero_object"], "vehicle")
        self.assertEqual(len(res["blockers"]), 0)

    def test_high_ticket_architecture_proposes_3d(self):
        res = Fit3DAssessor.assess(
            lead_slug="kappe-architects",
            lead_data={
                "category": "architecture",
                "business_name": "Kappe Studio Architecture",
                "deal_value": 12000.0,
                "has_licensed_glb": True
            }
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["recommendation"], "propose")
        self.assertGreaterEqual(res["score"], 60)
        self.assertEqual(res["hero_object"], "building")

    def test_operator_requested_overrides_proposal(self):
        res = Fit3DAssessor.assess(
            lead_slug="small-craft-studio",
            lead_data={
                "category": "craft pottery",
                "has_licensed_glb": True
            },
            operator_requested=True
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["recommendation"], "operator_requested")
        self.assertGreaterEqual(res["score"], 75)
