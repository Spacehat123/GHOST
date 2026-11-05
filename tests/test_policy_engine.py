"""P1 Decision Engine tests — determinism, fail-closed, caps-are-law (Roadmap §1)."""
import os
import unittest

# Isolate config + audit paths per-test-run WITHOUT touching GHOST_PROJECT_ROOT:
# discover runs all test modules in one process, and other modules compute
# REPO_DIR from that var at import time — redirecting it would break them.
import tempfile
TMP = tempfile.mkdtemp(prefix="ghost-policy-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, "audit.db")
os.environ["GHOST_POLICY_CONFIG_PATH"] = os.path.join(TMP, "policy.json")  # absent -> defaults

from ghost.policy.engine import (
    PolicyEngine, StateSnapshot, load_config,
    INTENT_HOLD_FIRE, INTENT_SERVICE_EXISTING, INTENT_CLOSE_PIPELINE,
    INTENT_GROW, INTENT_MAINTAIN,
)


def hungry_snapshot(**kw):
    """The user's exact scenario: no deal in 24h+, 90% quota left, low pipeline."""
    base = dict(
        last_closed_deal_age_h=30.0,
        quota_remaining_pct=92.0,
        pipeline_value_inr=5000.0,
        domain_health="green",
        contacts_sent_today=0,
    )
    base.update(kw)
    return StateSnapshot(**base)


class TestIntentSelection(unittest.TestCase):

    def setUp(self):
        self.engine = PolicyEngine(load_config())  # fresh defaults each test

    def test_the_exact_scenario_grows(self):
        d = self.engine.decide(hungry_snapshot())
        self.assertEqual(d.intent, INTENT_GROW)
        self.assertEqual(len(d.grants), 1)
        self.assertEqual(d.grants[0].max_new_contacts, 50)
        self.assertTrue(any("quota" in r for r in d.reasons))

    def test_recent_deal_no_hunt(self):
        d = self.engine.decide(hungry_snapshot(last_closed_deal_age_h=3.0))
        self.assertNotEqual(d.intent, INTENT_GROW)

    def test_low_quota_no_hunt(self):
        d = self.engine.decide(hungry_snapshot(quota_remaining_pct=50.0))
        self.assertNotEqual(d.intent, INTENT_GROW)

    def test_full_pipeline_no_hunt(self):
        d = self.engine.decide(hungry_snapshot(pipeline_value_inr=250000.0))
        self.assertNotEqual(d.intent, INTENT_GROW)

    def test_hot_lead_outranks_hunting(self):
        d = self.engine.decide(hungry_snapshot(hot_leads_waiting=2))
        self.assertEqual(d.intent, INTENT_CLOSE_PIPELINE)

    def test_client_reply_outranks_hot_leads(self):
        d = self.engine.decide(hungry_snapshot(hot_leads_waiting=2, client_replies_waiting=1))
        self.assertEqual(d.intent, INTENT_SERVICE_EXISTING)

    def test_incident_outranks_everything_except_hold(self):
        d = self.engine.decide(hungry_snapshot(incidents_over_slo=1))
        self.assertEqual(d.intent, INTENT_SERVICE_EXISTING)

    def test_complaint_freezes_outbound(self):
        d = self.engine.decide(hungry_snapshot(open_complaints=1))
        self.assertEqual(d.intent, INTENT_HOLD_FIRE)
        self.assertEqual(d.grants, [])

    def test_stale_heartbeat_freezes_outbound(self):
        d = self.engine.decide(hungry_snapshot(heartbeat_age_s=9999))
        self.assertEqual(d.intent, INTENT_HOLD_FIRE)
        self.assertIn("unverifiable", " ".join(d.pings))

    def test_red_domain_freezes_outbound(self):
        d = self.engine.decide(hungry_snapshot(domain_health="red"))
        self.assertEqual(d.intent, INTENT_HOLD_FIRE)

    def test_off_hours_no_cold_outreach(self):
        d = self.engine.decide(hungry_snapshot(within_business_hours=False))
        self.assertNotEqual(d.intent, INTENT_GROW)

    def test_default_is_maintain(self):
        d = self.engine.decide(StateSnapshot(last_closed_deal_age_h=1.0, pipeline_value_inr=999999.0))
        self.assertEqual(d.intent, INTENT_MAINTAIN)


class TestCapsAreLaw(unittest.TestCase):

    def setUp(self):
        self.engine = PolicyEngine(load_config())

    def test_contact_cap_never_exceeded(self):
        d = self.engine.decide(hungry_snapshot(contacts_sent_today=47))
        self.assertEqual(d.intent, INTENT_GROW)
        self.assertEqual(d.grants[0].max_new_contacts, 3)  # 50 - 47

    def test_cap_exhausted_grants_nothing(self):
        d = self.engine.decide(hungry_snapshot(contacts_sent_today=50))
        self.assertEqual(d.grants, [])
        self.assertTrue(any("cap exhausted" in r for r in d.reasons))

    def test_cap_overflow_telemetry_still_clamped(self):
        # lying telemetry: 5000 contacts "sent today" — engine must not blow up
        d = self.engine.decide(hungry_snapshot(contacts_sent_today=5000))
        self.assertEqual(d.grants, [])


class TestDeterminismAndFailClosed(unittest.TestCase):

    def setUp(self):
        self.engine = PolicyEngine(load_config())

    def test_identical_snapshot_identical_decision_id(self):
        s = hungry_snapshot()
        d1 = self.engine.decide(s)
        d2 = self.engine.decide(s)
        self.assertEqual(d1.decision_id, d2.decision_id)
        self.assertEqual(d1.intent, d2.intent)
        self.assertEqual(d1.grants, d2.grants)

    def test_replay_is_identical(self):
        s = hungry_snapshot(contacts_sent_today=10, pipeline_value_inr=12345.0)
        first = self.engine.decide(s).to_dict()
        second = PolicyEngine(load_config()).decide(s).to_dict()  # fresh engine
        self.assertEqual(first["decision_id"], second["decision_id"])
        self.assertEqual(first["grants"], second["grants"])

    def test_garbage_telemetry_fails_closed(self):
        bad = StateSnapshot(
            last_closed_deal_age_h=None, quota_remaining_pct="corrupt",
            domain_health=None, contacts_sent_today="lots",
            heartbeat_age_s=None,
        )
        d = self.engine.decide(bad)
        # invalid domain health coerces to red -> hold fire; corrupt quota -> 0
        self.assertIn(d.intent, (INTENT_HOLD_FIRE,))
        self.assertEqual(d.grants, [])

    def test_unknown_domain_health_is_red_not_green(self):
        d = self.engine.decide(hungry_snapshot(domain_health="awesome"))
        self.assertEqual(d.intent, INTENT_HOLD_FIRE)


class TestKnobOwnership(unittest.TestCase):

    def test_knobs_file_overrides_defaults(self):
        cfg = load_config()
        # configs/policy.json ships with the documented defaults
        self.assertEqual(cfg["knobs"]["max_new_contacts_per_day"], 50)
        # and the operator can tighten it
        cfg["knobs"]["max_new_contacts_per_day"] = 5
        engine = PolicyEngine(cfg)
        d = engine.decide(hungry_snapshot())
        self.assertEqual(d.grants[0].max_new_contacts, 5)


if __name__ == "__main__":
    unittest.main()
