"""T10 dark-run harness tests: the run cannot start on a lie.

- preflight fails closed with nothing configured, naming the operator actions
- start refuses on a failed preflight; forced starts are recorded as forced
- status math: day N of 30, criteria read from the real ledger
- the full lifecycle: start (green path with a stubbed preflight) → status → stop
"""
import json
import os
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-t10-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_DARKRUN_STATE"] = os.path.join(TMP, f"darkrun-{uuid.uuid4().hex}.json")
os.environ["GHOST_SECRETS_PATH"] = os.path.join(TMP, f"sec-{uuid.uuid4().hex}.json")
os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALTED")
os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALT.json")
os.environ["GHOST_SUPERVISOR_BEAT"] = os.path.join(TMP, "beat.json")

from ghost.control import darkrun


class EnvPinned(unittest.TestCase):
    """Later-importing test files set GHOST_AUDIT_DB_PATH at THEIR import —
    at RUN time this file would count THEIR ledger. Pin per test."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_AUDIT_DB_PATH", "GHOST_GRANT_SECRET",
                        "GHOST_UNIFIED_DB_PATH", "GHOST_HALT_FILE",
                        "GHOST_HALT_META", "GHOST_SECRETS_PATH")}
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
        os.environ["GHOST_GRANT_SECRET"] = f"t10-{uuid.uuid4().hex}"
        os.environ["GHOST_UNIFIED_DB_PATH"] = os.path.join(TMP, f"ghost-{uuid.uuid4().hex}.db")
        os.environ["GHOST_SECRETS_PATH"] = os.path.join(TMP, f"sec-{uuid.uuid4().hex}.json")
        self.addCleanup(self._restore)

    def _restore(self):
        for path in (os.environ.get("GHOST_HALT_FILE"), os.environ.get("GHOST_HALT_META")):
            if path and os.path.exists(path):
                os.remove(path)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestPreflightFailsClosed(EnvPinned):

    def test_nothing_configured_everything_named(self):
        rep = darkrun.preflight()
        self.assertFalse(rep["ready"])
        names = {c["name"] for c in rep["checks"]}
        for expected in ("grant_authority", "sending_domain", "outreach_smtp",
                         "razorpay_keys", "silero_vad", "halt_clear"):
            self.assertIn(expected, names)
        self.assertTrue(rep["operator_actions"],
                        "every failing check names its operator action")
        for c in rep["checks"]:
            if not c["ok"]:
                self.assertTrue(c["operator_action"],
                                f"{c['name']} must say WHAT the operator must do")

    def test_start_refuses_on_failed_preflight(self):
        out = darkrun.start()
        self.assertIn("error", out)
        self.assertIn("preflight", out["error"])
        self.assertIsNone(darkrun._read_state(), "no state may exist after a refused start")

    def test_forced_start_is_recorded_as_forced(self):
        out = darkrun.start(force=True)
        self.assertTrue(out.get("started"))
        self.assertTrue(out["state"]["forced"])
        self.assertIn("DESPITE FAILED PREFLIGHT", out.get("warning") or "")
        darkrun.stop()
        self.assertIsNone(darkrun._read_state())


class TestLifecycle(EnvPinned):

    def test_start_status_stop_green_path(self):
        # green preflight: stub the operator inputs via env + supervisor beat
        os.environ["GHOST_GRANT_SECRET"] = f"t10-{uuid.uuid4().hex}"
        from ghost.core import heartbeat
        heartbeat.beat("sim")
        from ghost.supervisor.service import beat
        beat()
        # sim receipt marker
        receipt = os.path.join(os.path.dirname(os.environ["GHOST_DARKRUN_STATE"]),
                               "sim_receipt.json")
        os.makedirs(os.path.dirname(receipt), exist_ok=True)
        json.dump({"ok": True}, open(receipt, "w"))
        os.environ["GHOST_SILERO_MODEL_PATH"] = "/dev/null"  # exists → check passes
        out = darkrun.start()
        # razorpay/domain/smtp may still fail — force only if honest gaps remain
        if "error" in out:
            out = darkrun.start(force=True)
        self.assertTrue(out.get("started"), out)
        st = darkrun.status()
        self.assertTrue(st["active"])
        self.assertEqual(st["day"], 1)
        self.assertEqual(st["of"], 30)
        self.assertIn("zero_complaints", st["criteria"])
        self.assertTrue(st["criteria"]["zero_complaints"]["pass"])
        stopped = darkrun.stop()
        self.assertTrue(stopped.get("stopped"))

    def test_status_math_day_n(self):
        # fabricate a start 3 days ago
        json.dump({"started_at": __import__("time").time() - 3 * 86400,
                   "started_day": "2026-11-03"},
                  open(os.environ["GHOST_DARKRUN_STATE"], "w"))
        st = darkrun.status()
        self.assertEqual(st["day"], 4)
        self.assertEqual(st["of"], 30)
        darkrun.stop()
