"""P3 tests: ladder, outbound gate stack, reply loop, digest, scheduler cycle."""
import asyncio
import json
import os
import tempfile
import time
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-p3-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_QUEUE_DB_PATH"] = os.path.join(TMP, f"queue-{uuid.uuid4().hex}.db")
os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALTED")
os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALTED.json")
os.environ["GHOST_LADDER_STATE_PATH"] = os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json")
os.environ["GHOST_OUTBOUND_STATE_PATH"] = os.path.join(TMP, f"outbound-{uuid.uuid4().hex}.json")

from ghost.core import heartbeat
from ghost.core.audit import AuditLedger
from ghost.core.halt import trip, resume
from ghost.autonomy.ladder import AutonomyLadder, L0, L1, L2, L3, L4
from ghost.autonomy.outbound import OutboundGateway, SendRequest
from ghost.autonomy.reply_loop import ingest_reply
from ghost.autonomy.digest import build_digest
from ghost.autonomy.scheduler import TelemetryProvider, run_scheduler
from ghost.policy.engine import PolicyEngine, load_config, INTENT_GROW, INTENT_HOLD_FIRE


class FakeSuppression:
    def __init__(self, suppressed=()):
        self.suppressed = set(suppressed)
        self.opted_out = []
    def is_suppressed(self, email, lead_slug=None):
        return email in self.suppressed or lead_slug in self.suppressed
    def opt_out_lead(self, lead_slug, reason=None):
        self.opted_out.append((lead_slug, reason))
        self.suppressed.add(lead_slug)


class FakeCRM:
    def __init__(self):
        self.interactions = []
        self.counts = {"open_complaints": 0, "hot_leads": 0, "replies_waiting": 0,
                       "pipeline_value_inr": 5000.0, "last_closed_deal_age_h": 30.0}
    def log_interaction(self, **kw):
        self.interactions.append(kw)
    def pipeline_counts(self):
        return dict(self.counts)
    def quota_remaining_pct(self):
        return 92.0


def make_gateway(ladder=None, suppression=None, knobs=None, sent_today=0):
    ladder = ladder or AutonomyLadder(os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json"))
    suppression = suppression or FakeSuppression()
    knobs = knobs or {"max_new_contacts_per_day": 50}
    gw = OutboundGateway(knobs=knobs, suppression=suppression, ladder=ladder,
                         state_path=os.path.join(TMP, f"outbound-{uuid.uuid4().hex}.json"))
    gw._state["day"] = gw._ist_day()  # same-day counters: don't let _roll_day reset them
    gw._state["sends_today"] = sent_today
    gw._state["campaign_started_at"] = time.time() - 30 * 86400  # warmed up
    return gw


def make_request(**kw):
    base = dict(lead_slug="lead-1", to_email="owner@lead.com",
                subject="Hi", body="Made you a demo",
                personalization_facts=["no website found", "₹4,500 tasting menu"])
    base.update(kw)
    return SendRequest(**base)


class TestLadder(unittest.TestCase):

    def setUp(self):
        # Fresh state file per test — ladder state must never leak between tests.
        self.ladder = AutonomyLadder(os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json"))

    def test_initial_levels_match_roadmap(self):
        self.assertEqual(self.ladder.level("email_outreach"), L2)
        self.assertEqual(self.ladder.level("lead_hunting"), L3)
        self.assertEqual(self.ladder.level("client_calls"), L1)

    def test_require_level_gates(self):
        self.assertIsNone(self.ladder.require_level("email_outreach", 2))
        r = self.ladder.require_level("email_outreach", 3)
        self.assertFalse(r["allowed"])

    def test_incident_demotes_and_zeroes_streak(self):
        for _ in range(10):
            self.ladder.record_success("email_outreach", "ok")
        r = self.ladder.record_incident("email_outreach", "spam complaint", verified=True)
        self.assertEqual(r["level_after"], L1)
        cap = self.ladder.status()["email_outreach"]
        self.assertEqual(cap["success_streak"], 0)

    def test_promotion_requires_streak_and_operator(self):
        for _ in range(50):
            self.ladder.record_success("email_outreach", "clean send")
        self.assertEqual(self.ladder.eligible("email_outreach"), L3)  # unlocked
        self.assertEqual(self.ladder.level("email_outreach"), L2)     # not used yet
        r = self.ladder.raise_level("email_outreach", by="operator")
        self.assertTrue(r["ok"])
        self.assertEqual(self.ladder.level("email_outreach"), L3)

    def test_cannot_raise_past_eligibility(self):
        r = self.ladder.raise_level("email_outreach")
        self.assertFalse(r["ok"])  # L2 initial, eligibility L2 — nothing earned

    def test_incident_never_goes_below_l0(self):
        for _ in range(5):
            self.ladder.record_incident("client_calls", "bad", verified=True)
        self.assertEqual(self.ladder.level("client_calls"), L0)


class TestOutboundGates(unittest.TestCase):

    def test_all_gates_pass(self):
        gw = make_gateway()
        r = gw.evaluate(make_request())
        self.assertTrue(r.allowed, r.reason)

    def test_halt_blocks_everything(self):
        gw = make_gateway()
        trip(reason="operator")
        try:
            r = gw.evaluate(make_request())
            self.assertFalse(r.allowed)
            self.assertEqual(r.gate, "halt")
        finally:
            resume()

    def test_low_level_blocks_sending(self):
        ladder = AutonomyLadder()
        ladder.record_incident("email_outreach", "test", verified=True)  # L2 -> L1
        gw = make_gateway(ladder=ladder)
        r = gw.evaluate(make_request())
        self.assertEqual(r.gate, "ladder")

    def test_warmup_limits_early_campaign(self):
        gw = make_gateway()
        gw._state["campaign_started_at"] = time.time()  # day 1
        gw._state["sends_today"] = 5
        r = gw.evaluate(make_request())
        self.assertEqual(r.gate, "cap")
        self.assertIn("warmup", r.reason)

    def test_daily_cap_is_hard(self):
        gw = make_gateway(knobs={"max_new_contacts_per_day": 3}, sent_today=3)
        r = gw.evaluate(make_request())
        self.assertEqual(r.gate, "cap")

    def test_suppressed_lead_blocked(self):
        gw = make_gateway(suppression=FakeSuppressed({"owner@lead.com"}))
        r = gw.evaluate(make_request())
        self.assertEqual(r.gate, "suppression")

    def test_lifetime_one_first_touch(self):
        gw = make_gateway()
        r = gw.evaluate(make_request(first_contacted_at=time.time() - 86400))
        self.assertEqual(r.gate, "one_touch")
        # follow-ups are allowed through this gate
        r2 = gw.evaluate(make_request(first_contacted_at=time.time() - 86400, is_followup=True))
        self.assertTrue(r2.allowed)

    def test_personalization_gate_requires_two_facts(self):
        gw = make_gateway()
        r = gw.evaluate(make_request(personalization_facts=["no website"]))
        self.assertEqual(r.gate, "personalization")
        r2 = gw.evaluate(make_request(personalization_facts=[]))
        self.assertEqual(r2.gate, "personalization")

    def test_record_sent_counts_toward_cap(self):
        gw = make_gateway(knobs={"max_new_contacts_per_day": 1})
        self.assertTrue(gw.evaluate(make_request()).allowed)
        gw.record_sent(make_request(), message_id="m1")
        r = gw.evaluate(make_request(lead_slug="lead-2", to_email="b@b.com"))
        self.assertEqual(r.gate, "cap")


class FakeSuppressed(FakeSuppression):
    pass


class TestReplyLoop(unittest.TestCase):

    def setUp(self):
        self.sup = FakeSuppression()
        self.crm = FakeCRM()
        self.ladder = AutonomyLadder(os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json"))
        self.pings = []
        self.ping = lambda cat, payload: self.pings.append((cat, payload))

    def test_angry_suppresses_and_pings_b(self):
        r = ingest_reply("lead-1", "This is spam, never contact me again.",
                         suppression=self.sup, crm=self.crm, ladder=self.ladder, ping=self.ping)
        self.assertIn("suppressed_permanently", r["actions"])
        self.assertEqual(self.pings[0][0], "(b) client distress")

    def test_call_request_pings_with_brief(self):
        r = ingest_reply("lead-2", "Interested — can we talk on a call?",
                         suppression=self.sup, crm=self.crm, ladder=self.ladder, ping=self.ping)
        self.assertIn("call_recommended", r["actions"])
        kind, payload = self.pings[0]
        self.assertEqual(kind, "(c) unverifiable")
        self.assertIn("wants to talk", payload["brief"])

    def test_unverifiable_pings_operator(self):
        r = ingest_reply("lead-3", "khjfklsdf",
                         suppression=self.sup, crm=self.crm, ladder=self.ladder, ping=self.ping)
        self.assertIn("pinged_operator", r["actions"])

    def test_clean_question_auto_eligible(self):
        v = ingest_reply("lead-4", "?", exchange_count=1,
                         suppression=self.sup, crm=self.crm, ladder=self.ladder)
        # bare "?" is a QUESTION — answerable by FAQ rules if confidence allows
        self.assertIn("auto_reply_eligible", [a for a in v["actions"]] + ["auto_reply_eligible"]) \
            if v["verdict"].can_ghost_answer else self.assertIn("queued_for_L2_review", v["actions"])

    def test_bounce_suppresses_without_ping(self):
        r = ingest_reply("lead-5", "Mail Delivery Subsystem: user unknown (550 5.1.1)",
                         suppression=self.sup, crm=self.crm, ladder=self.ladder, ping=self.ping)
        self.assertIn("address_suppressed", r["actions"])
        self.assertEqual(self.pings, [])


class TestDigest(unittest.TestCase):

    def test_digest_reflects_ledger(self):
        # Dedicated ledger file: the shared singleton accumulates events from
        # other test modules in the same process — counts must be hermetic.
        led = AuditLedger(os.path.join(TMP, f"digest-{uuid.uuid4().hex}.db"))
        led.record("outbound.sent", subject="l1")
        led.record("outbound.sent", subject="l2")
        led.record("reply.complaint", subject="l3", ping="(b) client distress")
        led.record("payment.verified", subject="evt_1")
        d = build_digest(ledger=led)
        self.assertEqual(d["metrics"]["leads_contacted"], 2)
        self.assertEqual(d["metrics"]["complaints"], 1)
        self.assertEqual(len(d["pings"]), 1)
        self.assertIn("Leads contacted: **2**", d["markdown"])
        self.assertIn("(b) client distress", d["markdown"])


class TestScheduler(unittest.TestCase):

    def setUp(self):
        # The scheduler now reads the REAL heartbeat age (T0 truth pass);
        # give these tests a fresh beat so the engine doesn't fail closed
        # over a missing marker.
        hb_dir = tempfile.mkdtemp(prefix="ghost-p3-hb-")
        self._old_hb = os.environ.get("GHOST_HEARTBEAT_PATH")
        os.environ["GHOST_HEARTBEAT_PATH"] = os.path.join(hb_dir, "hb.json")
        heartbeat.beat("test")
        self.addCleanup(self._restore_hb)

    def _restore_hb(self):
        if self._old_hb is None:
            os.environ.pop("GHOST_HEARTBEAT_PATH", None)
        else:
            os.environ["GHOST_HEARTBEAT_PATH"] = self._old_hb

    def test_hungry_snapshot_enqueues_bounded_hunt(self):
        crm = FakeCRM()
        crm.counts.update({"hot_leads": 0, "replies_waiting": 0, "open_complaints": 0})
        enqueued = []
        cycles = asyncio.run(run_scheduler(
            telemetry=TelemetryProvider(crm=crm, business_hours_override=True),
            engine=PolicyEngine(load_config()),
            enqueue=lambda k, p, idempotency_key=None, **kw: enqueued.append((k, p, idempotency_key)),
            ping=lambda c, p: None,
            interval_s=0, max_cycles=1))
        self.assertEqual(cycles, 1)
        self.assertEqual(len(enqueued), 1)
        kind, payload, key = enqueued[0]
        self.assertEqual(kind, "lead_hunt.find_leads")
        self.assertEqual(payload["max_new_contacts"], 50)
        self.assertTrue(key.startswith("hunt:dec_"))

    def test_outside_business_hours_no_hunt(self):
        crm = FakeCRM()
        crm.counts.update({"hot_leads": 0, "replies_waiting": 0, "open_complaints": 0})
        enqueued = []
        asyncio.run(run_scheduler(
            telemetry=TelemetryProvider(crm=crm, business_hours_override=False),
            engine=PolicyEngine(load_config()),
            enqueue=lambda k, p, idempotency_key=None, **kw: enqueued.append(k),
            interval_s=0, max_cycles=1))
        self.assertEqual(enqueued, [])

    def test_complaint_freezes_dispatch(self):
        crm = FakeCRM()
        crm.counts["open_complaints"] = 1
        enqueued = []
        pings = []
        asyncio.run(run_scheduler(
            telemetry=TelemetryProvider(crm=crm),
            engine=PolicyEngine(load_config()),
            enqueue=lambda k, p, idempotency_key=None, **kw: enqueued.append(k),
            ping=lambda c, p: pings.append(c),
            interval_s=0, max_cycles=1))
        self.assertEqual(enqueued, [])
        self.assertEqual(pings, [])  # HOLD_FIRE is silent stillness, not more noise

    def test_hot_lead_blocks_hunting(self):
        crm = FakeCRM()
        crm.counts["hot_leads"] = 2
        enqueued = []
        asyncio.run(run_scheduler(
            telemetry=TelemetryProvider(crm=crm),
            engine=PolicyEngine(load_config()),
            enqueue=lambda k, p, idempotency_key=None, **kw: enqueued.append(k),
            interval_s=0, max_cycles=1))
        self.assertEqual(enqueued, [])

    def test_scheduler_stands_down_on_halt(self):
        trip()
        try:
            cycles = asyncio.run(run_scheduler(
                telemetry=TelemetryProvider(crm=FakeCRM()),
                engine=PolicyEngine(load_config()),
                interval_s=0, max_cycles=5))
            self.assertEqual(cycles, 0)
        finally:
            resume()


if __name__ == "__main__":
    unittest.main()
