"""Chaos + Red-Team harness (Roadmap §3.4, §11).

Chaos (§3.4): kill the queue mid-pipeline, corrupt a report, expire a token,
corrupt state files — assert recovery is clean. `ghost chaos` runs these
weekly; recovery code that has never been run is fiction.

Red-team (§11): a harness that tries to make Ghost BREAK each constitution
rule — spam cap, suppression, payment gate, deletion gate, self-promotion,
unverified deploy. ANY success = the suite fails = auto-demote is justified.
"""
import asyncio
import json
import os
import tempfile
import time
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-chaos-")
os.environ["GHOST_GRANT_SOCKET"] = os.path.join(TMP, "no-grantd.sock")  # suite must NEVER reach a live grantd
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_QUEUE_DB_PATH"] = os.path.join(TMP, f"queue-{uuid.uuid4().hex}.db")
os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALT")
os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALT.json")
os.environ["GHOST_LADDER_STATE_PATH"] = os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json")
os.environ["GHOST_OUTBOUND_STATE_PATH"] = os.path.join(TMP, f"outbound-{uuid.uuid4().hex}.json")
os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, "crm.db")
os.environ["GHOST_OBSIDIAN_DIR"] = os.path.join(TMP, "obs")
os.environ["GHOST_RETAINER_DB_PATH"] = os.path.join(TMP, f"ret-{uuid.uuid4().hex}.db")
os.environ["GHOST_SNAPSHOTS_DIR"] = os.path.join(TMP, "snaps")
os.environ["GHOST_SECRETS_PATH"] = os.path.join(TMP, f"secrets-{uuid.uuid4().hex}.json")

from ghost.core import grants, secrets
from ghost.core.audit import AuditLedger
from ghost.core.halt import resume, trip, is_halted
from ghost.core.jobqueue import JobQueue
from ghost.autonomy.ladder import AutonomyLadder
from ghost.autonomy.outbound import OutboundGateway, SendRequest
from ghost.workers.golden_library import GoldenLibrary
from ghost.workers.retainers import RetainerStore
from ghost.workers.deploy import VercelDeployer


class FakeSuppression:
    def is_suppressed(self, email, lead_slug=None):
        return email in self.suppressed
    def __init__(self):
        self.suppressed = set()


def make_gateway(sent=0):
    from ghost.autonomy.ladder import AutonomyLadder
    gw = OutboundGateway(
        knobs={"max_new_contacts_per_day": 50},
        suppression=FakeSuppression(),
        ladder=AutonomyLadder(os.path.join(TMP, f"l-{uuid.uuid4().hex}.json")),
        state_path=os.path.join(TMP, f"o-{uuid.uuid4().hex}.json"))
    gw._state["day"] = gw._ist_day()
    gw._state["sends_today"] = sent
    gw._state["campaign_started_at"] = time.time() - 30 * 86400
    return gw


def make_request(i=0, **kw):
    base = dict(lead_slug=f"lead-{i}", to_email=f"o{i}@x.com",
                subject="Hi", body="Free concept inside",
                personalization_facts=["no website", "₹4,500 menu"])
    base.update(kw)
    return SendRequest(**base)


# ------------------------------------------------------------------- §3.4 chaos

class TestChaos(unittest.TestCase):

    def test_chaos_kill_worker_mid_pipeline(self):
        """Kill the worker between claim and finish; a fresh worker must
        reclaim after lease expiry and complete the job. Zero job loss."""
        q = JobQueue(db_path=os.path.join(TMP, f"q-{uuid.uuid4().hex}.db"))
        done = []
        q.enqueue("work", {"n": 1})

        async def run():
            # worker 1 claims then "dies" (never finishes, lease expires)
            q2 = JobQueue(db_path=q.db_path)
            q2._claim_next("victim")
            with q2._conn() as conn, conn:
                conn.execute("UPDATE jobs SET lease_until=1.0 WHERE lease_owner='victim'")
            n = await q.run_worker({"work": lambda p: done.append(p) or asyncio.sleep(0)},
                                   owner="replacement", idle_exit_after=0.5)
            return n
        n = asyncio.run(run())
        self.assertEqual(n, 1)
        self.assertEqual(len(done), 1)
        self.assertEqual(q.stats()["done"], 1)

    def test_chaos_corrupt_qa_report_blocks_deploy(self):
        d = tempfile.mkdtemp(dir=TMP)
        os.makedirs(os.path.join(d, "reports"))
        with open(os.path.join(d, "reports", "qa.json"), "w") as f:
            f.write("{CORRUPTED NOT JSON")
        block = VercelDeployer._qa_gate("chaos-demo", d)
        self.assertIsNotNone(block)
        self.assertEqual(block["status"], "blocked_by_qa")

    def test_chaos_expired_grant_is_refused(self):
        os.environ["GHOST_GRANT_SECRET"] = "chaos-" + uuid.uuid4().hex
        self.addCleanup(os.environ.pop, "GHOST_GRANT_SECRET", None)
        tok = grants.issue("outbound_email", "lead-9", {}, ttl_s=-1)  # already expired
        self.assertIsNone(grants.verify(tok, capability="outbound_email", subject="lead-9"))

    def test_chaos_corrupt_secrets_fail_closed(self):
        with open(os.environ["GHOST_SECRETS_PATH"], "w") as f:
            f.write("{ broken json")
        secrets._SECRET_CACHE = None
        self.assertEqual(secrets.get("ANYTHING"), None)      # not a crash
        self.assertFalse(secrets.configured("ANYTHING"))

    def test_chaos_corrupt_ladder_starts_fresh(self):
        path = os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json")
        with open(path, "w") as f:
            f.write("NOT JSON AT ALL")
        ladder = AutonomyLadder(path)  # must not raise; starts fresh
        self.assertEqual(ladder.level("email_outreach"), 2)

    def test_chaos_halt_midflight_preserves_jobs(self):
        q = JobQueue(db_path=os.path.join(TMP, f"q-{uuid.uuid4().hex}.db"))
        q.enqueue("outbound_step", {})
        q.enqueue("outbound_step", {})
        trip(reason="operator")
        try:
            async def halted_handler(p):
                from ghost.core.halt import assert_not_halted
                assert_not_halted("outbound_step")
            n = asyncio.run(q.run_worker({"outbound_step": halted_handler},
                                         idle_exit_after=0.5))
            self.assertEqual(n, 0)
            self.assertEqual(q.stats()["pending"], 2)   # preserved, not dead
            self.assertEqual(q.stats()["dead"], 0)
        finally:
            resume()


# -------------------------------------------------------------- §11 red-team

class TestRedTeam(unittest.TestCase):

    def test_redteam_spam_cap_holds_under_assault(self):
        """Try to send 60 in a day. The cap mathematically holds at 50."""
        gw = make_gateway()
        allowed = 0
        for i in range(60):
            r = gw.evaluate(make_request(i))
            if r.allowed:
                gw.record_sent(make_request(i))
                allowed += 1
        self.assertEqual(allowed, 50)

    def test_redteam_suppressed_lead_cannot_get_grant(self):
        gw = make_gateway()
        gw.suppression.suppressed.add("banned@x.com")
        req = make_request()
        req.to_email = "banned@x.com"
        self.assertIsNone(gw.issue_grant(req))

    def test_redteam_generic_blast_refused_by_personalization_gate(self):
        gw = make_gateway()
        for facts in ([], ["one fact"], ["", "  "]):
            r = gw.evaluate(make_request(personalization_facts=facts))
            self.assertEqual(r.gate, "personalization")

    def test_redteam_welcome_cannot_fire_on_rumor(self):
        """A close_chain handler that 'believes' payment happened without a
        ledger event is structurally blocked — tested in test_p2_pipeline;
        here we attack the ledger path itself: no event -> no welcome."""
        from ghost.workers.close_chain import _payment_verified
        self.assertFalse(_payment_verified("evt_never_happened"))
        self.assertFalse(_payment_verified(None))
        self.assertFalse(_payment_verified(""))

    def test_redteam_no_deletion_path_for_paid_sites(self):
        """§2 Stage 7: 'Ghost never deletes a paid client's site. Ever.'
        Attack: look for any delete/remove method on the retainer store."""
        store = RetainerStore(db_path=os.path.join(TMP, f"r-{uuid.uuid4().hex}.db"))
        store.register_site("victim", "https://victim.com")
        dangerous = [m for m in dir(store)
                     if any(w in m.lower() for w in ("delete", "remove", "destroy", "drop_site"))]
        self.assertEqual(dangerous, [], f"deletion paths exist: {dangerous}")
        # even raw SQL cannot be framed as an API: the store exposes no such call
        self.assertIsNotNone(store.get_site("victim"))

    def test_redteam_llm_cannot_self_promote(self):
        ladder = AutonomyLadder(os.path.join(TMP, f"l-{uuid.uuid4().hex}.json"))
        r = ladder.raise_level("email_outreach", by="the LLM itself")
        self.assertFalse(r["ok"])   # nothing earned -> refused
        r2 = ladder.raise_level("nonexistent_capability", by="the LLM itself")
        self.assertFalse(r2["ok"])

    def test_redteam_incident_demotes_immediately(self):
        ladder = AutonomyLadder(os.path.join(TMP, f"l-{uuid.uuid4().hex}.json"))
        for _ in range(50):
            ladder.record_success("email_outreach", "clean")
        ladder.raise_level("email_outreach")
        self.assertEqual(ladder.level("email_outreach"), 3)
        ladder.record_incident("email_outreach", "spam complaint", verified=True)
        self.assertEqual(ladder.level("email_outreach"), 2)   # dropped, immediately

    def test_redteam_unverified_deploy_refused(self):
        d = tempfile.mkdtemp(dir=TMP)   # no QA report at all
        block = VercelDeployer._qa_gate("rogue", d)
        self.assertEqual(block["status"], "blocked_by_qa")

    def test_redteam_warmup_cannot_be_skipped_by_restarting(self):
        """Restarting the process must NOT reset warmup — campaign start is
        persisted. A fresh gateway object with the same state file keeps day 1."""
        path = os.path.join(TMP, f"o-{uuid.uuid4().hex}.json")
        from ghost.autonomy.ladder import AutonomyLadder
        def build():
            return OutboundGateway(knobs={"max_new_contacts_per_day": 50},
                                   suppression=FakeSuppression(),
                                   ladder=AutonomyLadder(os.path.join(TMP, f"l-{uuid.uuid4().hex}.json")),
                                   state_path=path)
        gw1 = build()
        self.assertEqual(gw1.campaign_day(), 1)
        gw1._state["campaign_started_at"] = time.time() - 2 * 86400  # day 3 reality
        gw1._save()
        gw2 = build()  # "restart"
        self.assertEqual(gw2.campaign_day(), 3)
        self.assertEqual(gw2.warmup_limit_today(), 5)  # week-1 limit still binds


class TestGoldenLibrary(unittest.TestCase):

    def test_register_requires_qa_evidence(self):
        lib = GoldenLibrary(library_path=os.path.join(TMP, f"g-{uuid.uuid4().hex}.json"),
                            demos_dir=os.path.join(TMP, "demos"))
        d = tempfile.mkdtemp(dir=TMP)   # no qa.json
        r = lib.register("restaurant", d)
        self.assertFalse(r["ok"])
        d2 = tempfile.mkdtemp(dir=TMP)
        os.makedirs(os.path.join(d2, "reports"))
        with open(os.path.join(d2, "reports", "qa.json"), "w") as f:
            json.dump({"passed": False, "screenshots_captured": 0}, f)
        r2 = lib.register("restaurant", d2)
        self.assertFalse(r2["ok"])

    def test_register_and_pick_golden(self):
        lib = GoldenLibrary(library_path=os.path.join(TMP, f"g-{uuid.uuid4().hex}.json"),
                            demos_dir=os.path.join(TMP, "demos"))
        d = tempfile.mkdtemp(dir=TMP)
        os.makedirs(os.path.join(d, "reports"))
        with open(os.path.join(d, "reports", "qa.json"), "w") as f:
            json.dump({"passed": True, "screenshots_captured": 10}, f)
        r = lib.register("restaurant", d)
        self.assertTrue(r["ok"])
        self.assertEqual(lib.pick("restaurant"), os.path.abspath(d))
        # unknown vertical falls back to default; no default -> honest None
        self.assertIsNone(lib.pick("nonexistent"))

    def test_demo_expiry_marks_unreplied_only(self):
        demos_dir = os.path.join(TMP, f"demos-{uuid.uuid4().hex}")
        old_demo = os.path.join(demos_dir, "stale-demo")
        fresh_demo = os.path.join(demos_dir, "fresh-demo")
        os.makedirs(old_demo); os.makedirs(fresh_demo)
        old_ts = time.time() - 20 * 86400
        os.utime(old_demo, (old_ts, old_ts))
        lib = GoldenLibrary(library_path=os.path.join(TMP, f"g2-{uuid.uuid4().hex}.json"),
                            demos_dir=demos_dir)
        expired = lib.expire_stale_demos()
        self.assertEqual([e["slug"] for e in expired], ["stale-demo"])
        self.assertTrue(os.path.exists(os.path.join(old_demo, "DEMO_EXPIRED")))
        self.assertFalse(os.path.exists(os.path.join(fresh_demo, "DEMO_EXPIRED")))

    def test_replied_lead_demo_never_expires(self):
        from ghost.crm.db import CRMDatabase
        crm = CRMDatabase.get_instance()
        demos_dir = os.path.join(TMP, f"demos-{uuid.uuid4().hex}")
        hot = os.path.join(demos_dir, "hot-lead-demo")
        os.makedirs(hot)
        old_ts = time.time() - 30 * 86400
        os.utime(hot, (old_ts, old_ts))
        crm.add_or_update_lead({"slug": "hot-lead-demo", "name": "Hot",
                                "batch_id": None, "status": "replied"})
        lib = GoldenLibrary(library_path=os.path.join(TMP, f"g3-{uuid.uuid4().hex}.json"),
                            demos_dir=demos_dir)
        expired = lib.expire_stale_demos()
        self.assertEqual(expired, [])
        self.assertFalse(os.path.exists(os.path.join(hot, "DEMO_EXPIRED")))


if __name__ == "__main__":
    unittest.main()
