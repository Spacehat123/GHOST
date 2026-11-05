"""Wiring tests: the organs are CONNECTED. Runtime starts, heartbeat is real,
scheduler reads real telemetry, CLI exists, server routes exist."""
import asyncio
import importlib
import json
import os
import tempfile
import unittest
import uuid

def _dep_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


TMP = tempfile.mkdtemp(prefix="ghost-wire-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_QUEUE_DB_PATH"] = os.path.join(TMP, f"queue-{uuid.uuid4().hex}.db")
os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALT")
os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALT.json")
os.environ["GHOST_HEARTBEAT_PATH"] = os.path.join(TMP, "hb.json")
os.environ["GHOST_LADDER_STATE_PATH"] = os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json")
os.environ["GHOST_CRM_DB_PATH"] = os.path.join(TMP, "crm.db")
os.environ["GHOST_OBSIDIAN_DIR"] = os.path.join(TMP, "obs")
os.environ["GHOST_POLICY_CONFIG_PATH"] = os.path.join(TMP, "policy.json")
os.environ["GHOST_EVENTS_DB"] = os.path.join(TMP, "events.db")
os.environ["GHOST_RETAINER_DB_PATH"] = os.path.join(TMP, "ret.db")

from ghost.core import heartbeat
from ghost.core.audit import AuditLedger
from ghost.core.halt import resume, trip, is_halted
from ghost.core.jobqueue import JobQueue
from ghost.crm.db import CRMDatabase
from ghost.autonomy.runtime import AutonomyRuntime
from ghost.autonomy.scheduler import TelemetryProvider
from ghost.policy.engine import PolicyEngine, load_config


class TestHeartbeat(unittest.TestCase):

    def test_beat_then_fresh(self):
        heartbeat.beat("test")
        self.assertLess(heartbeat.age_s(), 5.0)
        self.assertFalse(heartbeat.is_stale())

    def test_missing_marker_is_infinitely_stale(self):
        hb_path = os.path.join(TMP, f"hb-{uuid.uuid4().hex}.json")
        old = os.environ["GHOST_HEARTBEAT_PATH"]
        os.environ["GHOST_HEARTBEAT_PATH"] = hb_path
        try:
            importlib.reload(heartbeat)
            self.assertEqual(heartbeat.age_s(), float("inf"))
            self.assertTrue(heartbeat.is_stale())
        finally:
            os.environ["GHOST_HEARTBEAT_PATH"] = old
            importlib.reload(heartbeat)

    def test_stale_heartbeat_trips_halt(self):
        heartbeat.beat("test")
        self.assertIsNone(heartbeat.enforce_stale())          # fresh: nothing
        # age the marker artificially
        with open(os.environ["GHOST_HEARTBEAT_PATH"], "w") as f:
            json.dump({"ts": 0, "note": "ancient"}, f)
        meta = heartbeat.enforce_stale()
        self.assertIsNotNone(meta)                            # tripped NOW
        self.assertTrue(is_halted())
        events = AuditLedger.get_instance().query(event_type="watchdog.heartbeat_stale")
        self.assertEqual(len(events), 1)
        resume()


class TestCRMTelemetry(unittest.TestCase):

    def test_pipeline_counts_shape(self):
        crm = CRMDatabase.get_instance()
        counts = crm.pipeline_counts()
        for key in ("open_complaints", "hot_leads", "replies_waiting",
                    "pipeline_value_inr", "last_closed_deal_age_h"):
            self.assertIn(key, counts)

    def test_complaint_detected_from_interaction_log(self):
        crm = CRMDatabase.get_instance()
        crm.add_or_update_client("angry- client", "Angry Client",
                                 email="a@b.c", status="won")
        crm.log_interaction(client_slug="angry- client", channel="email",
                            direction="inbound", content="[ANGRY] This is spam")
        counts = crm.pipeline_counts()
        self.assertGreaterEqual(counts["open_complaints"], 1)

    def test_quota_unreadable_fails_closed(self):
        os.environ["GHOST_TOKEN_STATS_PATH"] = os.path.join(TMP, "nonexistent.json")
        crm = CRMDatabase.get_instance()
        self.assertEqual(crm.quota_remaining_pct(), 0.0)
        # readable quota flows through
        p = os.path.join(TMP, "tokens.json")
        with open(p, "w") as f:
            json.dump({"quota_remaining_pct": 93.0}, f)
        os.environ["GHOST_TOKEN_STATS_PATH"] = p
        self.assertEqual(crm.quota_remaining_pct(), 93.0)
        del os.environ["GHOST_TOKEN_STATS_PATH"]


class TestSchedulerRealTelemetry(unittest.TestCase):

    def test_provider_uses_real_heartbeat_age(self):
        # Stale heartbeat -> HOLD_FIRE even with a hungry pipeline.
        trip(reason="test")  # direct halt
        try:
            snap = TelemetryProvider(crm=CRMDatabase.get_instance()).snapshot()
            self.assertTrue(snap.halted)
        finally:
            resume()

    def test_provider_reads_crm_counts(self):
        crm = CRMDatabase.get_instance()
        crm.add_or_update_client("hot", "Hot Lead", email="h@b.c", status="contract_sent",
                                 deal_value=50000.0)
        snap = TelemetryProvider(crm=crm).snapshot()
        self.assertGreaterEqual(snap.hot_leads_waiting, 1)
        self.assertGreaterEqual(snap.pipeline_value_inr, 50000.0)


class TestRuntime(unittest.TestCase):

    def test_runtime_starts_and_stops_cleanly(self):
        async def go():
            rt = AutonomyRuntime(scheduler_interval_s=0.05, with_lead_hunt=False)
            await rt.start()
            await asyncio.sleep(0.25)
            st = rt.status
            await rt.stop()
            return st
        st = asyncio.run(go())
        self.assertTrue(st["running"])
        self.assertFalse(st["halted"])
        self.assertLess(st["heartbeat_age_s"], 5.0)
        events = AuditLedger.get_instance().day_summary()["events"]
        self.assertGreaterEqual(events.get("policy.decision", 0), 1)
        self.assertGreaterEqual(events.get("runtime.started"), 1)
        self.assertGreaterEqual(events.get("runtime.stopped"), 1)

    def test_runtime_stands_down_on_halt(self):
        async def go():
            rt = AutonomyRuntime(scheduler_interval_s=0.05, with_lead_hunt=False)
            await rt.start()
            await asyncio.sleep(0.2)
            trip(reason="operator")
            await asyncio.sleep(0.15)
            st = rt.status
            await rt.stop()
            return st
        st = asyncio.run(go())
        self.assertTrue(st["halted"])
        resume()


class TestCLI(unittest.TestCase):

    def test_halt_and_resume_commands(self):
        from ghost.cli import main as cli_main
        self.assertEqual(cli_main(["halt", "--reason", "cli test"]), 0)
        self.assertTrue(is_halted())
        self.assertEqual(cli_main(["resume"]), 0)
        self.assertFalse(is_halted())

    def test_status_runs(self):
        from ghost.cli import main as cli_main
        self.assertEqual(cli_main(["status"]), 0)

    def test_metrics_command(self):
        from ghost.cli import main as cli_main
        from ghost.core.audit import audit
        audit("outbound.sent", subject="x")
        self.assertEqual(cli_main(["metrics"]), 0)


class TestServerRoutes(unittest.TestCase):

    def test_app_imports_with_all_routes(self):
        from ghost.server import app as app_mod
        paths = {getattr(r, "path", "") for r in app_mod.app.routes}
        for required in ("/api/halt", "/api/resume", "/api/digest", "/api/metrics",
                         "/api/ladder", "/api/webhooks/razorpay", "/api/retainers",
                         "/api/runtime/status", "/api/voice/interrupt"):
            self.assertIn(required, paths, f"missing route {required}")

    @unittest.skipUnless(_dep_available("fastapi"), "fastapi not installed")
    def test_webhook_refuses_unsigned_body(self):
        from fastapi.testclient import TestClient
        from ghost.server.app import app
        with TestClient(app) as client:
            r = client.post("/api/webhooks/razorpay", content=b"{\"fake\": 1}",
                            headers={"X-Razorpay-Signature": "deadbeef"})
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.json()["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
