"""P1 tests (Operator's 11): collect-only money, chooser wiring, data-dir paths.

- COLLECT-ONLY: zero money-out call sites anywhere in src/ghost — Ghost
  accepts payments, it never sends them (route_transfer was deleted).
- CHOOSER WIRING: the scheduler consumes the deterministic work board; the
  ENGINE stays the veto, and the chooser's stricter service-first gate
  suppresses hunting when they disagree.
- PATHS: the revocation epoch and rhythm markers resolve into the data dir,
  never the source tree (the last src/data residents are gone).
"""
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from dataclasses import asdict

TMP = tempfile.mkdtemp(prefix="ghost-p1-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_RHYTHM_DIR"] = os.path.join(TMP, "rhythm")

from ghost.policy.engine import Decision, Grant, PolicyEngine, StateSnapshot, load_config


class EnvPinned(unittest.TestCase):
    """Suite runs all modules in one process — pin per test, restore after."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_AUDIT_DB_PATH", "GHOST_RHYTHM_DIR",
                        "GHOST_DATA_DIR", "GHOST_HALT_FILE", "GHOST_HALT_META")}
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
        os.environ["GHOST_RHYTHM_DIR"] = os.path.join(TMP, f"rhythm-{uuid.uuid4().hex}")
        os.environ["GHOST_DATA_DIR"] = os.path.join(TMP, f"data-{uuid.uuid4().hex}")
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


REPO = Path(__file__).resolve().parent.parent


class TestCollectOnly(unittest.TestCase):
    """Ghost accepts payments; it never sends money."""

    BANNED = ("payments/transfers", "/payouts", "fund_account",
              "RAZORPAY_ROUTE", "route_transfer")

    def test_no_money_out_call_sites_anywhere(self):
        offenders = []
        for py in (REPO / "src" / "ghost").rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for banned in self.BANNED:
                if banned in text:
                    offenders.append(f"{py.name}: {banned}")
        self.assertEqual(offenders, [],
                         f"collect-only violation — money-out code returned: {offenders}")

    def test_no_fabricated_payment_urls(self):
        for py in (REPO / "src" / "ghost").rglob("*.py"):
            self.assertNotIn("rzp.io/i/", py.read_text(encoding="utf-8"),
                             f"{py.name} fabricates a payment URL — links must come "
                             "from the provider API or be absent (P4 wires the real one)")


def _snapshot(**over) -> StateSnapshot:
    base = dict(halted=False, open_complaints=0, domain_health="green",
                heartbeat_age_s=5.0, incidents_over_slo=0, client_replies_waiting=0,
                hot_leads_waiting=0, pipeline_value_inr=0.0,
                last_closed_deal_age_h=48.0, quota_remaining_pct=100.0,
                contacts_sent_today=0, within_business_hours=True)
    base.update(over)
    return StateSnapshot(**base)


class _FakeTelemetry:
    def __init__(self, snapshot):
        self._s = snapshot

    def snapshot(self):
        return self._s


class _GrowAlwaysEngine:
    """A stand-in for a future engine regression: grants GROW even while
    retainer incidents are open — exactly what the chooser must catch."""

    def __init__(self):
        self.k = {"idle_hours_before_hunt": 24, "hunt_quota_floor_pct": 90,
                  "max_new_contacts_per_day": 50}

    def decide(self, s):
        return Decision(decision_id="dec_fake_grow", intent="GROW",
                        grants=[Grant(intent="GROW", max_new_contacts=10,
                                      max_build_cost_inr=2000.0)],
                        reasons=["fake engine grants grow"], pings=[],
                        snapshot=asdict(s))


class TestChooserWiring(EnvPinned):

    def _run(self, telemetry, engine):
        import asyncio
        from ghost.autonomy.scheduler import run_scheduler
        enqueued, pings = [], []
        cycles = asyncio.run(run_scheduler(
            telemetry=telemetry, engine=engine,
            enqueue=lambda k, p, idempotency_key=None, **kw:
                enqueued.append((k, idempotency_key)),
            ping=lambda c, p: pings.append(c),
            interval_s=0, max_cycles=1))
        return enqueued, cycles

    def test_engine_grow_plus_clean_board_enqueues_hunt(self):
        engine = PolicyEngine(load_config())
        enqueued, cycles = self._run(_FakeTelemetry(_snapshot()), engine)
        self.assertEqual(cycles, 1)
        self.assertEqual(len(enqueued), 1, "exactly one hunt per cycle")
        kind, key = enqueued[0]
        self.assertEqual(kind, "lead_hunt.find_leads")
        self.assertTrue(key.startswith("hunt:"), "idempotency key rides the decision")

    def test_chooser_suppresses_hunt_when_service_work_exists(self):
        # engine (stubbed) grants GROW; the snapshot has 2 retainer incidents
        # over SLO — the chooser's service-first gate must veto the hunt.
        enqueued, _ = self._run(
            _FakeTelemetry(_snapshot(incidents_over_slo=2)), _GrowAlwaysEngine())
        self.assertEqual(enqueued, [], "service-first: no hunt while incidents are open")

    def test_engine_alone_holds_fire_on_incidents(self):
        # the real engine never even grants GROW with incidents open — the
        # suppression branch above is defense in depth, not the primary rule
        decision = PolicyEngine(load_config()).decide(_snapshot(incidents_over_slo=2))
        self.assertEqual(decision.intent, "SERVICE_EXISTING")
        self.assertEqual(decision.grants, [])


class TestDataDirPaths(EnvPinned):

    def test_epoch_and_rhythm_live_in_the_data_dir(self):
        from ghost.core.revocation import epoch_path
        from ghost.core.rhythm import rhythm_dir, rhythm_config
        from ghost.paths import data_dir
        # the suite pins these env vars for isolation — pop them to exercise
        # the DEFAULT resolution (then setUp's cleanup restores them)
        for k in ("GHOST_REVOCATION_EPOCH", "GHOST_RHYTHM_DIR"):
            os.environ.pop(k, None)
        self.assertEqual(epoch_path(), os.path.join(data_dir(), "REVOCATION_EPOCH"))
        self.assertEqual(rhythm_dir(), os.path.join(data_dir(), "rhythm"))
        self.assertIn("scheduler_cycle", rhythm_config()["events"],
                      "default rhythm config intact")
        for p in (epoch_path(), rhythm_dir()):
            self.assertNotIn(os.sep + "src" + os.sep, p)

    def test_rhythm_config_reads_the_real_knob_file(self):
        # the old default pointed at src/configs/rhythm.json (nonexistent) —
        # operator overrides were silently ignored
        import json
        knob = os.path.join(TMP, f"rhythm-knob-{uuid.uuid4().hex}.json")
        json.dump({"events": {"digest": {"by": "08:00", "days": [1], "grace_min": 5}}},
                  open(knob, "w"))
        os.environ["GHOST_RHYTHM_CONFIG"] = knob
        try:
            cfg = __import__("ghost.core.rhythm", fromlist=["rhythm_config"]).rhythm_config()
            self.assertEqual(cfg["events"]["digest"]["by"], "08:00",
                             "operator rhythm override must be honored")
        finally:
            os.environ.pop("GHOST_RHYTHM_CONFIG", None)


if __name__ == "__main__":
    unittest.main()
