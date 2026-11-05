"""T5 verifier-plane tests: reconciliation catches reality drift, the promise
checker blocks off-terms promises before SMTP, and the reply classifier no
longer emits typed-in confidence numbers (measured corpus instead).

Exit tests (ROADMAP_TOTAL_AUTONOMY T5):
- a send missing from the provider log is caught (phantom)
- a provider payment missing from the ledger (and the reverse) is caught
- a dead deploy URL is caught
- a pitch containing an unapproved price is BLOCKED before SMTP
- no classifier verdict carries a typed-in confidence; measured agreement on
  a labeled corpus holds (ANGRY recall = 1.0)
"""
import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

TMP = tempfile.mkdtemp(prefix="ghost-t5-")
# Module-level pins keep import-time binds scoped, but other test files POP
# some of these at run time (chaos + GHOST_GRANT_SECRET) — every test class
# below re-pins per test with cleanup (the T2 lesson).
PINNED_KEYS = ("GHOST_AUDIT_DB_PATH", "GHOST_UNIFIED_DB_PATH", "GHOST_GRANT_SECRET",
               "GHOST_HALT_FILE", "GHOST_HALT_META", "GHOST_OUTBOUND_STATE_PATH",
               "GHOST_REVOCATION_EPOCH", "GHOST_TERMS_PATH",
               "GHOST_OUTREACH_SMTP_HOST", "GHOST_OUTREACH_SMTP_PORT",
               "GHOST_OUTREACH_SMTP_USER", "GHOST_OUTREACH_SMTP_PASSWORD",
               "GHOST_OUTREACH_FROM", "GHOST_OUTREACH_REPLY_TO")


def pin_env():
    saved = {k: os.environ.get(k) for k in PINNED_KEYS}

    def apply():
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
        os.environ["GHOST_UNIFIED_DB_PATH"] = os.path.join(TMP, f"ghost-{uuid.uuid4().hex}.db")
        os.environ["GHOST_GRANT_SECRET"] = f"t5-{uuid.uuid4().hex}"
        os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALTED")
        os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALT.json")
        os.environ["GHOST_OUTBOUND_STATE_PATH"] = os.path.join(TMP, f"ob-{uuid.uuid4().hex}.json")
        os.environ["GHOST_REVOCATION_EPOCH"] = os.path.join(TMP, f"epoch-{uuid.uuid4().hex}")
        os.environ["GHOST_TERMS_PATH"] = os.path.join(TMP, f"terms-{uuid.uuid4().hex}.json")
        os.environ["GHOST_OUTREACH_SMTP_HOST"] = "smtp.test"
        os.environ["GHOST_OUTREACH_SMTP_PORT"] = "587"
        os.environ["GHOST_OUTREACH_SMTP_USER"] = "u"
        os.environ["GHOST_OUTREACH_SMTP_PASSWORD"] = "p"
        os.environ["GHOST_OUTREACH_FROM"] = "ghost@test"
        os.environ["GHOST_OUTREACH_REPLY_TO"] = "reply@test"

    def restore():
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return apply, restore


class EnvPinnedTest(unittest.TestCase):
    def setUp(self):
        apply, restore = pin_env()
        apply()
        self.addCleanup(restore)

from ghost.core import grants
from ghost.autonomy.outbound import SendRequest
from ghost.autonomy.sender import ColdOutreachSender, SendRefused
from ghost.store import outbox
from ghost.verify import reconciliation as reco
from ghost.verify import promise_checker as pc


class FakeSup:
    def is_suppressed(self, value, lead_slug=None):
        return False


class FakeSMTP:
    calls = 0

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg):
        FakeSMTP.calls += 1
        return {}


def req(**over):
    base = dict(lead_slug="t5-lead", to_email="o@t.c", subject="A free concept",
                body="We built something for you.",
                personalization_facts=["f1 about the place", "f2 about the menu"])
    base.update(over)
    return SendRequest(**base)


class TestPromiseChecker(EnvPinnedTest):

    def setUp(self):
        super().setUp()
        self.terms = {"approved_amounts": ["15000"], "forbidden_phrases": ["guaranteed"],
                      "allow_deadlines": False}

    def test_approved_amount_passes(self):
        ok, scan = pc.check_send_allowed("Proposal", "The build is ₹15,000 total.", self.terms)
        self.assertTrue(ok, scan["violations"])

    def test_unapproved_amount_blocks(self):
        ok, scan = pc.check_send_allowed("Proposal", "We can do it for ₹25,000.", self.terms)
        self.assertFalse(ok)
        self.assertIn("25000", scan["unapproved_amounts"])

    def test_deadline_promise_blocks(self):
        for text in ("We'll deliver by Friday.", "Live within 24 hours!",
                     "I'll call you tomorrow at 5pm.", "Finished in 3 days."):
            ok, scan = pc.check_send_allowed("s", text, self.terms)
            self.assertFalse(ok, text)
            self.assertTrue(scan["deadline_promises"], text)

    def test_forbidden_phrase_blocks(self):
        ok, scan = pc.check_send_allowed("s", "This is a guaranteed win.", self.terms)
        self.assertFalse(ok)
        self.assertIn("guaranteed", scan["forbidden_phrases"])

    def test_missing_terms_fail_closed_for_promises_only(self):
        ok, scan = pc.check_send_allowed("Hello", "We built a free concept for your restaurant.",
                                         pc.load_terms.__wrapped__() if hasattr(pc.load_terms, "__wrapped__") else None)
        # no terms injected -> defaults: empty approvals
        self.assertTrue(ok, "plain conversation with no promises must pass")
        ok2, scan2 = pc.check_send_allowed("Hello", "Only ₹9,999 today!", None)
        self.assertFalse(ok2, "a promise with no approved terms must block")

    def test_sender_blocks_before_smtp(self):
        """THE exit test: off-terms price never reaches the provider."""
        from ghost.core.audit import AuditLedger
        sender = ColdOutreachSender(FakeSup(), crm=None)
        token = grants.issue("outbound_email", "t5-lead", {"daily_cap": 50}, ttl_s=300.0)
        with patch("ghost.autonomy.sender.smtplib.SMTP", FakeSMTP):
            with self.assertRaises(SendRefused) as ctx:
                sender.send(req(body="We can build it for ₹25,000 — deal?"),
                            grant_token=token)
            if ctx.exception.reason != "promise_violation":
                reasons = [(r.get("payload") or {}).get("reason")
                           for r in AuditLedger.get_instance().query(
                               event_type="grant.verify_failed", limit=5)]
                self.fail(f"refused as {ctx.exception.reason}; grant verify said: {reasons}")
            self.assertEqual(ctx.exception.reason, "promise_violation")
        self.assertEqual(FakeSMTP.calls, 0, "blocked promise reached the provider")
        from ghost.store.facade import ghost_db_path
        from ghost.store.schema import connect_target, create_target
        conn = connect_target(ghost_db_path())
        create_target(conn)
        rows = conn.execute(
            "SELECT COUNT(*) FROM outbox WHERE kind='outreach_email'").fetchone()[0]
        conn.close()
        self.assertEqual(rows, 0, "a blocked promise must not become an intent")
        events = AuditLedger.get_instance().query(event_type="outbound.promise_blocked", limit=5)
        self.assertTrue(events)


class TestReconciliation(EnvPinnedTest):

    def _sent_outbox_row(self, key):
        outbox.record("outreach_email", key, {"lead": "l"})
        outbox.transition(key, "sent")

    def test_phantom_send_caught(self):
        key = f"outreach:{uuid.uuid4().hex[:16]}"
        self._sent_outbox_row(key)
        mid = reco.message_id_for_outbox_key(key)
        # provider log has SOME entries but not ours -> phantom
        log = [{"message_id": "<outreach:other@outreach.ghost>"}]
        rep = reco.reconcile_sends(lambda: log)
        self.assertEqual(rep["status"], "mismatch")
        self.assertEqual(len(rep["phantom_sends"]), 1)
        # provider log CONFIRMS ours -> ok
        rep2 = reco.reconcile_sends(lambda: [{"message_id": mid}])
        self.assertEqual(rep2["status"], "ok")
        # no fetcher configured -> honest, not green
        self.assertEqual(reco.reconcile_sends(None)["status"], "not_configured")

    def test_payment_mismatch_both_directions(self):
        from ghost.core.audit import AuditLedger
        led = AuditLedger.get_instance()
        led.record("payment.verified", subject="evt_a", invoice_number="INV-1",
                   amount="20000")
        calls = []

        def fetch():
            calls.append(1)
            return [
                {"id": "pay_1", "amount": 20000, "currency": "INR",
                 "status": "captured", "notes": {"invoice": "INV-1"}},
                {"id": "pay_2", "amount": 50000, "currency": "INR",
                 "status": "captured", "notes": {"invoice": "INV-2"}},  # no ledger event
            ]
        rep = reco.reconcile_payments(fetch, ledger=led)
        self.assertEqual(rep["status"], "mismatch")
        self.assertIn("INV-2", rep["missing_in_ledger"])
        # ledger event with no provider payment -> missing_at_provider
        led.record("payment.verified", subject="evt_b", invoice_number="INV-9",
                   amount="1000")
        rep2 = reco.reconcile_payments(fetch, ledger=led)
        self.assertIn("INV-9", rep2["missing_at_provider"])
        # amount mismatch
        def fetch_bad():
            return [{"id": "pay_1", "amount": 5, "currency": "INR",
                     "status": "captured", "notes": {"invoice": "INV-1"}}]
        rep3 = reco.reconcile_payments(fetch_bad, ledger=led)
        self.assertIn("INV-1", rep3["amount_mismatch"])
        self.assertEqual(reco.reconcile_payments(None, ledger=led)["status"],
                         "not_configured")

    def test_dead_deploy_caught(self):
        from ghost.store.facade import ghost_db_path
        from ghost.store.schema import create_target
        conn = sqlite3.connect(ghost_db_path())
        create_target(conn)
        conn.execute("INSERT OR REPLACE INTO crm_leads (id, slug, demo_url) VALUES (?, ?, ?)",
                     ("lead_dead", "dead-slug", "https://dead.example.com"))
        conn.commit()
        conn.close()
        key = f"deploy:dead-slug:{uuid.uuid4().hex[:6]}"
        outbox.record("deploy", key, {"slug": "dead-slug"})
        outbox.transition(key, "sent")
        rep = reco.reconcile_deploys(fetch_url=lambda url: 500 if "dead" in url else 200)
        self.assertEqual(rep["status"], "mismatch")
        self.assertTrue(any(d["slug"] == "dead-slug" for d in rep["dead_deploys"]))
        rep2 = reco.reconcile_deploys(fetch_url=lambda url: 200)
        self.assertEqual(rep2["status"], "ok")

    def test_run_all_honest_when_unconfigured(self):
        from ghost.core.audit import AuditLedger
        led = AuditLedger.get_instance()
        rep = reco.run_all(payment_fetcher=None, mail_log_fetcher=None,
                           deploy_fetcher=lambda url: 200, ledger=led)
        self.assertEqual(rep["payments"]["status"], "not_configured")
        self.assertEqual(rep["sends"]["status"], "not_configured")
        self.assertEqual(rep["deploys"]["status"], "ok")
        self.assertEqual(rep["mismatch_sections"], [])
        events = AuditLedger.get_instance().query(event_type="reconcile.run", limit=3)
        self.assertTrue(events)


LABELED_CORPUS = [
    # (text, expected_category) — the measured evaluation set
    ("Never contact me again, you spammers.", "ANGRY"),
    ("This is harassment. Remove me from your list.", "ANGRY"),
    ("Absolutely furious about this ridiculous email.", "ANGRY"),
    ("550 5.1.1 user unknown", "BOUNCE"),
    ("Mail Delivery Subsystem: delivery has failed", "BOUNCE"),
    ("The recipient address was rejected, no such mailbox", "BOUNCE"),
    ("Automatic reply: out of office until Monday", "AUTO_REPLY"),
    ("I am away from the office on vacation.", "AUTO_REPLY"),
    ("This looks great, send me the details.", "INTERESTED"),
    ("We are interested — let's proceed.", "INTERESTED"),
    ("I love it! What are the next steps?", "INTERESTED"),
    ("Yes, let's move forward with this.", "INTERESTED"),
    ("How much does it cost?", "QUESTION"),
    ("Can you send your portfolio?", "QUESTION"),
    ("What's included in the package?", "QUESTION"),
    ("Do you have examples of your work?", "QUESTION"),
    ("That is too expensive for us.", "OBJECTION"),
    ("We already worked with a competitor on this.", "OBJECTION"),
    ("The price is too high, any discount?", "OBJECTION"),
    ("Not right now, maybe next quarter.", "NOT_NOW"),
    ("We're good for now, revisit next season.", "NOT_NOW"),
    ("We're not looking at the moment.", "NOT_NOW"),
]


class TestClassifierMeasured(unittest.TestCase):  # no env deps

    def test_no_typed_in_confidence_anywhere(self):
        from ghost.core.reply_classifier import classify_reply
        for text, _ in LABELED_CORPUS:
            v = classify_reply(text)
            self.assertIsNone(v.confidence,
                              f"runtime verdicts carry no typed-in numbers: {text!r}")

    def test_measured_agreement_on_labeled_corpus(self):
        from ghost.core.reply_classifier import classify_reply, ANGRY
        correct = sum(1 for text, expected in LABELED_CORPUS
                      if classify_reply(text).category == expected)
        agreement = correct / len(LABELED_CORPUS)
        self.assertGreaterEqual(agreement, 0.85,
                                f"measured agreement {agreement:.2f} below floor")
        # safety class: EVERY distress marker must freeze + ping
        for text, expected in LABELED_CORPUS:
            if expected == ANGRY:
                v = classify_reply(text)
                self.assertEqual(v.category, ANGRY, text)
                self.assertTrue(v.freeze_sequence and v.ping_operator, text)

    def test_solo_reply_gate_closed_without_measured_backing(self):
        from ghost.core.reply_classifier import classify_reply, ghost_may_reply_alone
        for text, _ in LABELED_CORPUS:
            v = classify_reply(text)
            self.assertFalse(ghost_may_reply_alone(v),
                             f"no solo reply without MEASURED confidence: {text!r}")

    def test_unmatched_fails_closed(self):
        from ghost.core.reply_classifier import classify_reply
        v = classify_reply("zzz qqq xyzzy plugh")
        self.assertFalse(v.can_ghost_answer)
        self.assertTrue(v.ping_operator)


if __name__ == "__main__":
    unittest.main()
