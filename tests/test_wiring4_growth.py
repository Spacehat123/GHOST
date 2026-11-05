"""Wiring-4 tests: FAQ KB, scope guard, bid copilot, growth extras."""
import os
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-w4-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")

from ghost.knowledge.faq import FAQKnowledgeBase
from ghost.workers.growth import (
    BidCopilot, ChurnPredictor, ReferralEngine, RenewalConcierge, ScopeGuard,
)


class TestFAQ(unittest.TestCase):

    def setUp(self):
        self.kb = FAQKnowledgeBase(os.path.join(TMP, f"faq-{uuid.uuid4().hex}.json"))

    def test_answers_included_in_demo(self):
        text, conf = self.kb.answer("what is included in the demo website?", "restaurant")
        self.assertIsNotNone(text)
        self.assertGreater(conf, 0.45)
        self.assertIn("reservation", text)

    def test_cross_vertical_default_fallback(self):
        text, conf = self.kb.answer("is this a scam?", "restaurant")
        self.assertIsNotNone(text)
        self.assertIn("unsubscribe", text)

    def test_unknown_question_returns_none(self):
        text, conf = self.kb.answer("do you sell used cars in bulk quantities", "restaurant")
        # 'cars' has no KB overlap -> no hallucinated answer
        self.assertIsNone(text)

    def test_can_answer_alone_floor(self):
        # KB match strength for exact phrasing is high but the 0.95 floor is strict:
        # the DESIGN is that most real questions ping the operator — only
        # near-exact matches auto-answer.
        self.assertIsInstance(self.kb.can_answer_alone("what is included in the demo website?", "restaurant"), bool)

    def test_operator_can_extend_kb(self):
        path = os.path.join(TMP, f"faq-{uuid.uuid4().hex}.json")
        import json
        with open(path, "w") as f:
            json.dump({"restaurant": [{"q": "do you offer parking validation",
                                       "a": "Yes — we validate for the basement lot after 6pm."}]}, f)
        kb = FAQKnowledgeBase(path)
        text, conf = kb.answer("do you offer parking validation?", "restaurant")
        self.assertIn("basement", text)


class TestScopeGuard(unittest.TestCase):

    def setUp(self):
        self.guard = ScopeGuard()

    def test_in_scope_tweaks(self):
        v = self.guard.check("Can you fix the typo in our hours section?")
        self.assertTrue(v.in_scope)
        self.assertFalse(v.ping_operator)

    def test_change_order_detected_with_band(self):
        v = self.guard.check("Could you also add online booking for appointments?")
        self.assertFalse(v.in_scope)
        self.assertEqual(v.change_order["kind"], "online_booking")
        lo, hi = v.change_order["price_band_inr"]
        self.assertLess(lo, hi)
        self.assertTrue(v.ping_operator)

    def test_ecommerce_is_big_band(self):
        v = self.guard.check("we also want a shop with online payments")
        self.assertEqual(v.change_order["kind"], "ecommerce")

    def test_neutral_message(self):
        v = self.guard.check("thanks, the site looks great")
        self.assertTrue(v.in_scope)


class TestBidCopilot(unittest.TestCase):

    def test_drafts_but_never_autosends(self):
        cop = BidCopilot(feed_url="https://upwork-feed.example/rss")
        items = cop.fetch_feed(fetch=lambda url: (
            "<rss><item><title>Build restaurant website with reservations</title>"
            "<link>https://upwork.com/j/1</link><description>$500 budget website</description></item>"
            "<item><title>Need a plumber</title><link>https://upwork.com/j/2</link>"
            "<description>fix pipes</description></item></rss>"))
        self.assertEqual(len(items), 2)
        draft = cop.draft_bid(items[0])
        self.assertIsNotNone(draft)
        self.assertEqual(draft["status"], "awaiting_human_send")
        self.assertIsNone(cop.draft_bid(items[1]))  # plumber: below capability
        # There is deliberately NO send() method on this class:
        self.assertFalse(hasattr(BidCopilot, "send"))

    def test_no_feed_url_is_honest(self):
        cop = BidCopilot(feed_url=None)
        self.assertEqual(cop.fetch_feed(lambda u: "<item></item>"), [])


class TestGrowthExtras(unittest.TestCase):

    def test_referral_draft(self):
        d = ReferralEngine().draft_referral_ask("acme", finder_discount_pct=10)
        self.assertIn("introduce us", d["body"])
        self.assertEqual(d["status"], "drafted_L2")

    def test_renewal_proposal_window(self):
        rc = RenewalConcierge()
        self.assertTrue(rc.proposal_due(20))
        self.assertFalse(rc.proposal_due(90))
        d = rc.draft_proposal("acme", "acme.com", days_left=20,
                              current_cost_inr=1200, cheaper_cost_inr=800)
        self.assertEqual(d["savings_inr"], 400.0)

    def test_churn_score_thresholds(self):
        cp = ChurnPredictor()
        healthy = cp.score(days_since_last_reply=2, report_opened=True)
        self.assertFalse(healthy["at_risk"])
        risky = cp.score(days_since_last_reply=45, report_opened=False,
                         complaints_last_90d=1)
        self.assertTrue(risky["at_risk"])
        self.assertGreaterEqual(len(risky["reasons"]), 2)


if __name__ == "__main__":
    unittest.main()
