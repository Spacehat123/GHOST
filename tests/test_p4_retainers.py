"""P4 tests: snapshots + restore drills, site checks, auto-fix tiers, dunning, monthly report."""
import json
import os
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-p4-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALTED")
os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALTED.json")

from ghost.core.audit import AuditLedger
from ghost.core.halt import trip, resume
from ghost.autonomy.ladder import AutonomyLadder
from ghost.workers.snapshots import SnapshotStore
from ghost.workers.site_monitor import check_uptime, check_seo_drift, check_broken_links
from ghost.workers.retainers import (
    AutoFixer, RetainerStore, monthly_report,
)


def uniq(prefix):
    return os.path.join(TMP, f"{prefix}-{uuid.uuid4().hex}")


class TestSnapshots(unittest.TestCase):

    def setUp(self):
        self.store = SnapshotStore(base_dir=uniq("snaps"))
        self.src = tempfile.mkdtemp(dir=TMP)
        with open(os.path.join(self.src, "index.html"), "w") as f:
            f.write("<html>site v1</html>")
        os.makedirs(os.path.join(self.src, "assets"))
        with open(os.path.join(self.src, "assets", "a.css"), "w") as f:
            f.write("body{}")

    def test_snapshot_restore_roundtrip_verifies(self):
        m = self.store.snapshot("acme", self.src, label="pre-touch")
        self.assertEqual(len(m["files"]), 2)
        # mutate source, then restore
        with open(os.path.join(self.src, "index.html"), "w") as f:
            f.write("<html>BROKEN</html>")
        r = self.store.restore("acme", m["snapshot_id"], self.src)
        self.assertTrue(r["verified"])
        with open(os.path.join(self.src, "index.html")) as f:
            self.assertEqual(f.read(), "<html>site v1</html>")

    def test_corrupt_snapshot_aborts_restore(self):
        m = self.store.snapshot("acme", self.src)
        # corrupt the stored copy
        snap_file = None
        for root, _d, files in os.walk(os.path.join(self.store.base_dir, "acme", m["snapshot_id"])):
            for fn in files:
                if fn == "index.html":
                    snap_file = os.path.join(root, fn)
        with open(snap_file, "w") as f:
            f.write("tampered")
        with self.assertRaises(RuntimeError) as ctx:
            self.store.restore("acme", m["snapshot_id"], tempfile.mkdtemp(dir=TMP))
        self.assertIn("integrity failed", str(ctx.exception))

    def test_drill_marks_verified_and_drill_due_logic(self):
        m = self.store.snapshot("acme", self.src)
        self.assertTrue(self.store.drill_due("acme"))  # never drilled
        r = self.store.restore_drill("acme")
        self.assertTrue(r["ok"])
        self.assertFalse(self.store.drill_due("acme"))  # drilled now

    def test_drill_fails_on_corruption(self):
        m = self.store.snapshot("acme", self.src)
        snap_dir = os.path.join(self.store.base_dir, "acme", m["snapshot_id"])
        with open(os.path.join(snap_dir, "index.html"), "w") as f:
            f.write("corrupted")
        r = self.store.restore_drill("acme")
        self.assertFalse(r["ok"])


class TestSiteChecks(unittest.TestCase):

    def test_uptime_ok(self):
        r = check_uptime("https://x.com", fetch=lambda u, timeout=8: (200, "ok"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["evidence"]["status"], 200)

    def test_uptime_down(self):
        r = check_uptime("https://x.com", fetch=lambda u, timeout=8: (_ for _ in ()).throw(OSError("refused")))
        self.assertFalse(r["ok"])

    def test_seo_drift_detects_missing_pieces(self):
        html = "<html><body>no title, no meta</body></html>"
        def fetch(u, timeout=8):
            if u.endswith("robots.txt") or u.endswith("sitemap.xml"):
                return 404, ""
            return 200, html
        r = check_seo_drift("https://x.com", fetch=fetch)
        self.assertFalse(r["ok"])
        self.assertIn("missing <title>", r["detail"])
        self.assertIn("robots.txt", r["detail"])

    def test_seo_ok(self):
        html = "<html><head><title>T</title><meta name='description' content='d'></head></html>"
        def fetch(u, timeout=8):
            if u.endswith("robots.txt") or u.endswith("sitemap.xml"):
                return 200, "ok"
            return 200, html
        r = check_seo_drift("https://x.com", fetch=fetch)
        self.assertTrue(r["ok"], r["detail"])

    def test_broken_link_detection(self):
        def fetch(u, timeout=8):
            if u.endswith("/menu"):
                return 404, ""
            return 200, "<a href='https://x.com/menu'>Menu</a>"
        r = check_broken_links("https://x.com", fetch=fetch)
        self.assertFalse(r["ok"])
        self.assertEqual(r["evidence"]["broken"][0]["status"], 404)


class TestAutoFixTiers(unittest.TestCase):

    def setUp(self):
        self.store = RetainerStore(db_path=uniq("ret"))
        self.snaps = SnapshotStore(base_dir=uniq("snaps"))
        self.ladder = AutonomyLadder(os.path.join(TMP, f"ladder-{uuid.uuid4().hex}.json"))
        self.pings = []
        self.fixer = AutoFixer(self.store, self.snaps, self.ladder,
                               t1_fixers={"t1:ssl": lambda slug, inc: "renewed via ACME"},
                               ping=lambda c, p: self.pings.append((c, p)))
        self.store.register_site("acme", "https://acme.com", amount_inr=8000)

    def _incident(self, kind="ssl", severity="t1"):
        iid = self.store.open_incident("acme", kind, severity, "cert expiring", {})
        for inc in self.store.open_incidents("acme"):
            if inc["id"] == iid:
                return inc

    def test_no_snapshot_escalates_without_touching(self):
        r = self.fixer.dispatch(self._incident())
        self.assertEqual(r["status"], "escalated")
        self.assertEqual(r["reason"], "no_snapshot")

    def test_t1_needs_l4(self):
        self.snaps.snapshot("acme", tempfile.mkdtemp(dir=TMP))
        r = self.fixer.dispatch(self._incident(severity="t1"))
        # retainer_autofix starts at L2 -> T1 requires L4 -> escalate
        self.assertEqual(r["status"], "escalated")
        self.assertEqual(r["reason"], "ladder")

    def test_t2_needs_l3_and_drilled_snapshot(self):
        # Authority first: earn L3 so the test reaches the snapshot check.
        for _ in range(50):
            self.ladder.record_success("retainer_autofix", "ok")
        self.ladder.raise_level("retainer_autofix")  # L2 -> L3
        self.snaps.snapshot("acme", tempfile.mkdtemp(dir=TMP))  # NOT drilled
        r = self.fixer.dispatch(self._incident(kind="broken_links", severity="t2"))
        self.assertEqual(r["status"], "escalated")
        self.assertEqual(r["reason"], "snapshot_not_drilled")

    def test_t2_restores_after_drill(self):
        src = tempfile.mkdtemp(dir=TMP)
        with open(os.path.join(src, "index.html"), "w") as f:
            f.write("clean")
        m = self.snaps.snapshot("acme", src)
        self.snaps.restore_drill("acme", m["snapshot_id"])
        # Earn L3: 50 successes at L2 unlock L3, operator raises, T2 allowed.
        for _ in range(50):
            self.ladder.record_success("retainer_autofix", "ok")
        self.ladder.raise_level("retainer_autofix")  # L2 -> L3
        r = self.fixer.dispatch(self._incident(kind="broken_links", severity="t2"))
        self.assertEqual(r["status"], "fixed", r)
        self.assertEqual(r["tier"], "t2")
        self.assertEqual(len(self.store.open_incidents("acme")), 0)

    def test_t3_is_staged_with_token_even_at_high_level(self):
        self.snaps.snapshot("acme", tempfile.mkdtemp(dir=TMP))
        for _ in range(50):
            self.ladder.record_success("retainer_autofix", "ok")
        for _ in range(3):
            self.ladder.raise_level("retainer_autofix")  # to L4
        r = self.fixer.dispatch(self._incident(kind="seo_drift", severity="t3"))
        self.assertEqual(r["status"], "staged")
        self.assertTrue(r["approval_token"].startswith("approve-"))
        # incident is staged, not fixed
        inc = self.store.open_incidents("acme")
        # status moved to 'staged' so open list should be empty
        self.assertEqual(inc, [])

    def test_halt_blocks_t1(self):
        self.snaps.snapshot("acme", tempfile.mkdtemp(dir=TMP))
        # Full earn-path to L4: 50 successes -> unlock L3 -> raise -> 50 at L3
        # -> unlock L4 -> raise. Ghost cannot shortcut this (§1.2).
        for _ in range(50):
            self.ladder.record_success("retainer_autofix", "ok")
        self.ladder.raise_level("retainer_autofix")  # L3
        for _ in range(50):
            self.ladder.record_success("retainer_autofix", "ok")
        self.ladder.raise_level("retainer_autofix")  # L4
        self.assertEqual(self.ladder.level("retainer_autofix"), 4)
        trip(reason="operator")
        try:
            r = self.fixer.dispatch(self._incident(severity="t1"))
            self.assertEqual(r["status"], "halted")
        finally:
            resume()


class TestDunningNeverTouchesSites(unittest.TestCase):

    def setUp(self):
        self.store = RetainerStore(db_path=uniq("ret"))

    def test_failure_ladder_to_suspension(self):
        self.store.register_site("acme", "https://acme.com", amount_inr=8000)
        self.store.record_charge_failed("acme")
        self.assertEqual(self.store.get_site("acme")["billing_state"], "dunning")
        self.store.record_charge_failed("acme")
        self.assertEqual(self.store.get_site("acme")["billing_state"], "dunning")
        self.store.record_charge_failed("acme")
        site = self.store.get_site("acme")
        self.assertEqual(site["billing_state"], "grace")
        self.assertGreater(site["grace_until"], 0)
        # more failures past grace -> suspended, but record exists, site kept
        for _ in range(3):
            self.store.record_charge_failed("acme")
        site = self.store.get_site("acme")
        self.assertEqual(site["billing_state"], "suspended")
        # the site row STILL exists — no deletion happened
        self.assertIsNotNone(self.store.get_site("acme"))

    def test_recovery_on_successful_charge(self):
        self.store.register_site("acme", "https://acme.com")
        for _ in range(5):
            self.store.record_charge_failed("acme")
        self.store.record_charge_succeeded("acme", event_id="evt_9")
        site = self.store.get_site("acme")
        self.assertEqual(site["billing_state"], "active")
        self.assertEqual(site["charge_failures"], 0)

    def test_grace_expiry_detected(self):
        self.store.register_site("acme", "https://acme.com")
        for _ in range(3):
            self.store.record_charge_failed("acme")
        self.assertFalse(self.store.is_in_grace_expired("acme"))
        # force grace into the past
        with self.store._conn() as conn, conn:
            import time as _t
            conn.execute("UPDATE sites SET grace_until=? WHERE slug='acme'", (_t.time() - 10,))
        self.assertTrue(self.store.is_in_grace_expired("acme"))


class TestMonthlyReport(unittest.TestCase):

    def test_report_lists_fixes_and_uptime(self):
        store = RetainerStore(db_path=uniq("ret"))
        store.register_site("acme", "https://acme.com", amount_inr=8000)
        iid = store.open_incident("acme", "ssl", "t1", "cert", {})
        store.record_fix("acme", iid, "t1", "t1:ssl", result="renewed via ACME")
        hist = {"checks": 4300, "uptime_pct": 99.98, "incidents": 1}
        r = monthly_report(store, lambda s: hist, "acme")
        self.assertIn("99.98%", r["markdown"])
        self.assertIn("renewed via ACME", r["markdown"])
        self.assertTrue(os.path.exists(r["path"]))


if __name__ == "__main__":
    unittest.main()
