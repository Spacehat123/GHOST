"""P2 tests: reply classifier, payment-webhook honesty gate, close chain, deploy QA gate."""
import asyncio
import json
import os
import tempfile
import unittest
from decimal import Decimal

TMP = tempfile.mkdtemp(prefix="ghost-p2-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, "audit.db")
os.environ["GHOST_QUEUE_DB_PATH"] = os.path.join(TMP, "queue.db")
os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALTED")
os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALTED.json")
os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, "crm.db")
os.environ["GHOST_EVENTS_DB"] = os.path.join(TMP, "events.db")

from ghost.core.audit import AuditLedger, audit
from ghost.core.halt import trip, resume, HaltActive
from ghost.core.jobqueue import JobQueue
from ghost.core.reply_classifier import (
    classify_reply, ghost_may_reply_alone,
    INTERESTED, QUESTION, OBJECTION, NOT_NOW, ANGRY, AUTO_REPLY, BOUNCE,
)
from ghost.workers.close_chain import (
    ChainError, make_handlers, process_payment_webhook,
)


class FakeProvider:
    """Scriptable PaymentProvider for tests."""
    name = "razorpay"
    def __init__(self, verify=True, duplicate=False):
        self.verify = verify
        self.duplicate = duplicate
    def verify_webhook(self, headers, body):
        return self.verify
    def parse_event(self, body):
        from ghost.workers.payments import PaymentEvent
        if self.duplicate:
            e = PaymentEvent("evt_dup", "payment.captured", Decimal(1), "INR",
                             "INV-1", "lead-1", "duplicate", {})
            return e
        data = json.loads(body)
        return PaymentEvent(
            event_id=data["id"], event_type=data.get("event", "payment.captured"),
            amount=Decimal(str(data["amount"])), currency=data["currency"],
            invoice_number=data["invoice_number"], lead_id=None, status="captured",
            raw_payload=data,
        )


INVOICE = {
    "invoice_number": "INV-7", "amount": "20000", "currency": "INR",
    "lead_slug": "salt-restaurant", "deal_value_inr": 20000,
}


def make_webhook_body(amount=20000, currency="INR"):
    return json.dumps({
        "id": "evt_100", "event": "payment.captured",
        "amount": amount, "currency": currency, "invoice_number": "INV-7",
    }).encode()


class TestReplyClassifier(unittest.TestCase):

    def test_angry_freezes_and_pings(self):
        v = classify_reply("This is spam. Never contact me again. Ridiculous.")
        self.assertEqual(v.category, ANGRY)
        self.assertTrue(v.freeze_sequence)
        self.assertTrue(v.ping_operator)
        self.assertFalse(ghost_may_reply_alone(v))

    def test_unsubscribe_is_suppression(self):
        v = classify_reply("Please unsubscribe me")
        self.assertEqual(v.category, ANGRY)
        self.assertTrue(v.freeze_sequence)

    def test_call_request_flags_human(self):
        v = classify_reply("Sounds good, can we talk on a call tomorrow?", exchange_count=1)
        self.assertTrue(v.needs_call)
        self.assertTrue(v.ping_operator)

    def test_price_pushback_beyond_tolerance_pings_money(self):
        v = classify_reply("That's too expensive for us", discount_requested_pct=40)
        self.assertEqual(v.category, OBJECTION)
        self.assertTrue(v.ping_operator)
        self.assertEqual(v.ping_category, "money")

    def test_exchange_overflow_needs_call(self):
        v = classify_reply("hmm ok", exchange_count=3)
        self.assertTrue(v.needs_call)

    def test_interested(self):
        v = classify_reply("This is impressive, let's proceed")
        self.assertEqual(v.category, INTERESTED)
        self.assertFalse(v.ping_operator)

    def test_unmatched_fails_closed(self):
        v = classify_reply("khjfklsdf")
        self.assertFalse(v.can_ghost_answer)
        self.assertTrue(v.ping_operator)
        self.assertEqual(v.ping_category, "unverifiable")

    def test_auto_reply_detected(self):
        v = classify_reply("Automatic reply: I am out of office until Monday")
        self.assertEqual(v.category, AUTO_REPLY)
        self.assertTrue(v.freeze_sequence)
        self.assertFalse(v.ping_operator)

    def test_deterministic(self):
        a = classify_reply("Can you send details about pricing?", exchange_count=2)
        b = classify_reply("Can you send details about pricing?", exchange_count=2)
        self.assertEqual(a.category, b.category)
        self.assertEqual(a.reasons, b.reasons)
        self.assertEqual(a.confidence, b.confidence)


class TestPaymentWebhookGate(unittest.TestCase):

    def setUp(self):
        self.enqueued = []
        def enqueue(kind, payload, idempotency_key=None, **kw):
            self.enqueued.append((kind, payload, idempotency_key))
        self.enqueue = enqueue

    def test_bad_signature_never_processes(self):
        p = FakeProvider(verify=False)
        r = process_payment_webhook(p, {}, make_webhook_body(), lambda n: INVOICE, self.enqueue)
        self.assertEqual(r["status"], "rejected")
        self.assertEqual(self.enqueued, [])

    def test_no_invoice_is_unverifiable(self):
        r = process_payment_webhook(FakeProvider(), {}, make_webhook_body(), lambda n: None, self.enqueue)
        self.assertEqual(r["status"], "rejected")
        self.assertEqual(r["reason"], "no_matching_invoice")

    def test_underpayment_is_mismatch_not_payment(self):
        r = process_payment_webhook(FakeProvider(), {}, make_webhook_body(amount=500),
                                    lambda n: INVOICE, self.enqueue)
        self.assertEqual(r["status"], "mismatch")
        self.assertIn("money", r["ping"])
        self.assertEqual(self.enqueued, [])

    def test_currency_mismatch_is_mismatch(self):
        r = process_payment_webhook(FakeProvider(), {}, make_webhook_body(currency="USD"),
                                    lambda n: INVOICE, self.enqueue)
        self.assertEqual(r["status"], "mismatch")

    def test_duplicate_never_rechains(self):
        r = process_payment_webhook(FakeProvider(duplicate=True), {}, make_webhook_body(),
                                    lambda n: INVOICE, self.enqueue)
        self.assertEqual(r["status"], "duplicate")
        self.assertEqual(self.enqueued, [])

    def test_verified_payment_chains_contract_and_welcome(self):
        r = process_payment_webhook(FakeProvider(), {}, make_webhook_body(),
                                    lambda n: INVOICE, self.enqueue)
        self.assertEqual(r["status"], "verified")
        kinds = [k for k, _, _ in self.enqueued]
        self.assertEqual(kinds, ["close_chain.send_contract", "close_chain.send_welcome"])
        keys = [x[2] for x in self.enqueued]
        self.assertEqual(keys, ["contract:INV-7", "welcome:INV-7"])


class TestCloseChain(unittest.TestCase):

    def setUp(self):
        # Per-test queue DB: leftover jobs from other tests must never leak in.
        import uuid
        self.q = JobQueue(db_path=os.path.join(TMP, f"q-{uuid.uuid4().hex}.db"))

    def _run(self, deps=None):
        handlers = make_handlers(deps or {})
        async def go():
            return await self.q.run_worker(handlers, idle_exit_after=0.5, poll_interval=0.05)
        return asyncio.run(go())

    def test_welcome_blocked_without_verified_payment_event(self):
        sent = []
        self.q.enqueue("close_chain.send_welcome", {"invoice_number": "INV-X", "lead_slug": "l",
                                                    "event_id": "evt_ghost"},
                       idempotency_key="w1", max_attempts=2)
        self._run({"send_email": sent.append})
        # Blocked welcome is NEVER sent. It retries, then dead-letters (which
        # pings the operator) — a silent skip would be a fabricated success.
        self.assertEqual(self.q.stats().get("done"), 0)
        self.assertEqual(self.q.stats().get("dead"), 1)
        self.assertEqual(self.q.stats().get("pending"), 0)
        self.assertEqual(sent, [])
        blocked = AuditLedger.get_instance().query(event_type="welcome.blocked")
        self.assertGreaterEqual(len(blocked), 1)

    @staticmethod
    def _stats(q):
        return q.stats()

    def test_full_chain_after_real_verification(self):
        sent_log = []
        def fake_send(payload):
            sent_log.append(payload)
        # 1. A real verified webhook writes evidence to the ledger and enqueues the chain.
        r = process_payment_webhook(FakeProvider(), {}, make_webhook_body(),
                                    lambda n: INVOICE, self.q.enqueue)
        self.assertEqual(r["status"], "verified")
        # 2. Chain runs: contract + welcome both complete; welcome sent exactly once.
        n = self._run({"send_email": fake_send})
        self.assertEqual(n, 2)
        self.assertEqual(self.q.stats().get("done"), 2)
        self.assertEqual(len(sent_log), 1)

    def test_chain_respects_halt(self):
        process_payment_webhook(FakeProvider(), {}, make_webhook_body(),
                                lambda n: INVOICE, self.q.enqueue)
        trip(reason="operator")
        try:
            self._run({})
        finally:
            resume()
        # Halt paused the worker at the first outbound step: 0 done, 2 pending,
        # retry budgets intact. A resumed run picks up exactly here.
        self.assertEqual(self.q.stats().get("done"), 0)
        self.assertEqual(self.q.stats().get("pending"), 2)
        self.assertEqual(self.q.stats().get("dead"), 0)
        paused = AuditLedger.get_instance().query(event_type="job.halt_paused")
        self.assertGreaterEqual(len(paused), 1)

    @staticmethod
    def q_state(q):
        return q.stats()


class TestDeployQAGate(unittest.TestCase):

    def _demo_with_qa(self, passed=True, captured=10, write_shots=True):
        d = tempfile.mkdtemp(prefix="qa-gate-", dir=TMP)
        os.makedirs(os.path.join(d, "reports"), exist_ok=True)
        shots = []
        for i in range(1, 3):
            rel = os.path.join("reports", "screenshots", f"s{i}.png")
            shots.append(rel)
        qa = {"passed": passed, "screenshots_captured": captured,
              "human_review": {"screenshots": shots}}
        with open(os.path.join(d, "reports", "qa.json"), "w") as f:
            json.dump(qa, f)
        if write_shots:
            for rel in shots:
                p = os.path.join(d, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(b"\x89PNG fake")
        return d

    def test_no_qa_report_blocks(self):
        from ghost.workers.deploy import VercelDeployer
        d = tempfile.mkdtemp(prefix="no-qa-", dir=TMP)
        block = VercelDeployer._qa_gate("x", d)
        self.assertIsNotNone(block)
        self.assertEqual(block["status"], "blocked_by_qa")

    def test_failed_qa_blocks(self):
        from ghost.workers.deploy import VercelDeployer
        d = self._demo_with_qa(passed=False)
        block = VercelDeployer._qa_gate("x", d)
        self.assertEqual(block["status"], "blocked_by_qa")

    def test_zero_screenshots_blocks_even_if_passed(self):
        from ghost.workers.deploy import VercelDeployer
        d = self._demo_with_qa(captured=0, write_shots=False)
        block = VercelDeployer._qa_gate("x", d)
        self.assertEqual(block["status"], "blocked_by_qa")

    def test_missing_cited_screenshots_block(self):
        from ghost.workers.deploy import VercelDeployer
        d = self._demo_with_qa(write_shots=False)
        block = VercelDeployer._qa_gate("x", d)
        self.assertEqual(block["status"], "blocked_by_qa")

    def test_valid_qa_clears(self):
        from ghost.workers.deploy import VercelDeployer
        d = self._demo_with_qa()
        self.assertIsNone(VercelDeployer._qa_gate("x", d))

    def test_override_is_explicit_and_logged(self):
        from ghost.workers.deploy import VercelDeployer
        d = tempfile.mkdtemp(prefix="no-qa2-", dir=TMP)
        self.assertIsNone(VercelDeployer._qa_gate("x", d, allow_unverified=True))
        events = AuditLedger.get_instance().query(event_type="deploy.unverified_override")
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
