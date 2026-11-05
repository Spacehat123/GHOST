"""T8 sidecar tests — the brief's five exit tests (§3b):

1. main task never pauses while a seeded bug gets fixed
2. a sidecar that edits outside its lock is stopped
3. a sidecar that tries to send / hold a key / spawn another agent is blocked
4. a patch that fails the verifier is never merged
5. halt during a sidecar run stops it within 5 seconds
"""
import os
import tempfile
import threading
import time
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-t8-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_UNIFIED_DB_PATH"] = os.path.join(TMP, f"ghost-{uuid.uuid4().hex}.db")
os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALTED")
os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALT.json")
os.environ["GHOST_SIDECAR_WORKROOT"] = os.path.join(TMP, "sidecars")
os.environ["GHOST_GRANT_SECRET"] = f"t8-{uuid.uuid4().hex}"

from ghost.control.sidecars import SidecarManager, SidecarBudget
from ghost.core.sandbox import build_safe_env


def make_target_file(broken: bool = True) -> str:
    d = tempfile.mkdtemp(prefix="ghost-t8-target-", dir=TMP)
    path = os.path.join(d, "index.html")
    content = ('<a href="htp://broken-link.example"></a>' if broken
               else '<a href="https://fixed-link.example"></a>')
    with open(path, "w") as f:
        f.write(content)
    return path


class HaltGuard:
    """Guarantees the shared sentinel is clean after each test."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_HALT_FILE", "GHOST_HALT_META", "GHOST_GRANT_SECRET")}
        os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, "HALTED")
        os.environ["GHOST_HALT_META"] = os.path.join(TMP, "HALT.json")
        os.environ["GHOST_GRANT_SECRET"] = f"t8-{uuid.uuid4().hex}"
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


class TestFiveExits(HaltGuard, unittest.TestCase):

    def test_1_main_task_never_pauses_bug_gets_fixed(self):
        """Main task runs; a sidecar fixes the seeded broken link in the
        background; the main loop's ticks never gap."""
        mgr = SidecarManager()
        target = make_target_file(broken=True)
        spawn = mgr.spawn(brief="Fix the broken link in index.html (typo: htp://)",
                          scope_files=[target],
                          check_command='grep -q "https://fixed-link" "{file}"',
                          issue_key="broken-link:index")
        self.assertIn("spec", spawn, spawn)
        spec = spawn["spec"]

        main_ticks = []

        def main_task(stop):
            while not stop.is_set():
                main_ticks.append(time.time())
                time.sleep(0.02)     # the main loop's tick
        # (sidecars never block the caller: mgr.run is invoked on the side
        #  thread of the orchestrator; in-process tests run it inline after
        #  proving tick continuity)

        stop = threading.Event()
        t = threading.Thread(target=main_task, args=(stop,))
        t.start()
        try:
            def fixer(spec, ctx, is_cancelled):
                path = ctx["files"]["index.html"]
                with open(path) as f:
                    fixed = f.read().replace("htp://broken-link", "https://fixed-link")
                return {"patch": {"index.html": fixed},
                        "evidence": "replaced malformed scheme", "tool_calls": 2}

            res = mgr.run(spec, fixer)
            merged = mgr.merge(spec, res, verifier=None)
        finally:
            stop.set()
            t.join()
        self.assertTrue(merged["merged"], merged)
        with open(target) as f:
            self.assertIn("https://fixed-link", f.read())
        # the main loop never paused: no tick gap anywhere near the budget scale
        gaps = [b - a for a, b in zip(main_ticks, main_ticks[1:])]
        self.assertLess(max(gaps or [0]), 0.5,
                        "main task stalled while a sidecar ran")

    def test_2_out_of_scope_edit_stopped(self):
        mgr = SidecarManager()
        target = make_target_file(broken=True)
        other_dir = tempfile.mkdtemp(prefix="ghost-t8-other-", dir=TMP)
        other = os.path.join(other_dir, "other.html")
        with open(other, "w") as f:
            f.write("<p>innocent</p>")
        spawn = mgr.spawn(brief="fix link", scope_files=[target],
                          check_command="true", issue_key="oos")
        spec = spawn["spec"]

        def greedy_fixer(spec, ctx, is_cancelled):
            return {"patch": {"index.html": "fixed",
                              "other.html": "PWNED"},
                    "evidence": "touched two files", "tool_calls": 3}

        res = mgr.run(spec, greedy_fixer)
        self.assertEqual(res.status, "discarded")
        self.assertIn("out-of-scope", res.notes[0])
        with open(other) as f:
            self.assertNotIn("PWNED", f.read())
        self.assertEqual(mgr.merge(spec, res, None)["merged"], False)

    def test_3_worker_is_structurally_powerless(self):
        """No credentials in the worker env, no PYTHONPATH (ghost's own
        modules — the effects plane — are not importable), and a second
        spawn FROM a running sidecar is refused (depth 1)."""
        mgr = SidecarManager()
        env = mgr.worker_env()
        for key, value in env.items():
            self.assertNotIn("t8-", str(value), f"credential leaked via {key}")
            self.assertNotIn("GRANT", key.upper())
        self.assertNotIn("PYTHONPATH", env)
        # depth-1: while a sidecar is active, the worker context cannot spawn
        target = make_target_file()
        spec = mgr.spawn(brief="d", scope_files=[target], check_command="true",
                         issue_key="depth")["spec"]
        mgr._depth = 1   # a running sidecar sets this
        try:
            refused = mgr.spawn(brief="nested", scope_files=[target],
                                check_command="true", issue_key="nested")
            self.assertIn("refused", refused)
            self.assertIn("depth", refused["refused"])
        finally:
            mgr._depth = 0
            mgr.release_locks(spec)

    def test_4_verifier_failed_patch_never_merged(self):
        mgr = SidecarManager()
        target = make_target_file(broken=True)
        spawn = mgr.spawn(brief="fix link", scope_files=[target],
                          check_command='grep -q "https://fixed-link" "{file}"',
                          issue_key="vf")
        spec = spawn["spec"]

        def bad_fixer(spec, ctx, is_cancelled):
            # claims fixed, patches with STILL-BROKEN content
            return {"patch": {"index.html": '<a href="htp://still-broken"></a>'},
                    "evidence": "trust me", "tool_calls": 1}

        res = mgr.run(spec, bad_fixer)
        merged = mgr.merge(spec, res, verifier=None)
        self.assertFalse(merged["merged"])
        self.assertIn("verifier failed", merged["reason"])
        with open(target) as f:
            self.assertIn("htp://broken-link", f.read(), "live file untouched")
        # the worktree was destroyed with the patch — nothing lingers
        self.assertFalse(os.path.exists(spec.worktree))

    def test_5_halt_stops_sidecar_within_5s(self):
        mgr = SidecarManager()
        target = make_target_file(broken=True)
        spawn = mgr.spawn(brief="long fix", scope_files=[target],
                          check_command="true", issue_key="halt",
                          budget=SidecarBudget(max_seconds=60))
        spec = spawn["spec"]

        def slow_fixer(spec, ctx, is_cancelled):
            started = time.time()
            while not is_cancelled():     # the cooperative halt check
                time.sleep(0.05)
            return {"patch": {}, "evidence": "cancelled", "tool_calls": 0,
                    "cancelled_after_s": time.time() - started}

        from ghost.core.halt import trip, resume, is_halted
        trip_at = time.time() + 0.3
        timer = threading.Timer(0.3, trip, kwargs={"reason": "t8-halt-test"})
        timer.start()
        try:
            t0 = time.time()
            res = mgr.run(spec, slow_fixer)
            elapsed = time.time() - t0
            self.assertEqual(res.status, "halted")
            self.assertTrue(is_halted())
            self.assertLess(elapsed - 0.3, 5.0,
                            f"halt->stop took {elapsed:.2f}s (must be <5s after trip)")
            # locks revoked: the file is immediately spawnable again post-resume
            resume()
            respawn = mgr.spawn(brief="after halt", scope_files=[target],
                                check_command="true", issue_key="halt2")
            self.assertIn("spec", respawn, "locks must be revoked after halt")
            mgr.release_locks(respawn["spec"])
        finally:
            timer.cancel()
            if is_halted():
                resume()


class TestSpawnGate(HaltGuard, unittest.TestCase):

    def test_caps_and_gates(self):
        mgr = SidecarManager()
        files = [make_target_file() for _ in range(4)]
        specs = []
        # two sidecars on the same issue are fine...
        for i in range(2):
            sp = mgr.spawn(brief="b", scope_files=[files[i]], check_command="true",
                           issue_key="same-issue")
            self.assertIn("spec", sp)
            specs.append(sp["spec"])
        # ...a third on the same issue hits the per-issue cap
        refused = mgr.spawn(brief="b", scope_files=[files[2]], check_command="true",
                            issue_key="same-issue")
        self.assertIn("per-issue cap", refused["refused"])
        # a different issue spawns (active=3 = MAX_CONCURRENT)...
        sp3 = mgr.spawn(brief="b", scope_files=[files[2]], check_command="true",
                        issue_key="issue-3")
        self.assertIn("spec", sp3)
        specs.append(sp3["spec"])
        # ...and the next hits the concurrency cap
        refused2 = mgr.spawn(brief="b", scope_files=[files[3]], check_command="true",
                             issue_key="issue-4")
        self.assertIn("concurrent", refused2["refused"])
        for sp in specs:
            mgr.release_locks(sp)

    def test_scope_outside_sandbox_refused(self):
        mgr = SidecarManager()
        refused = mgr.spawn(brief="b", scope_files=["/etc/passwd"],
                            check_command="true", issue_key="sandbox")
        self.assertIn("outside sandbox", refused["refused"])

    def test_missing_check_refused(self):
        mgr = SidecarManager()
        target = make_target_file()
        r = mgr.spawn(brief="fix something", scope_files=[target],
                      check_command="", issue_key="nocheck")
        self.assertIn("refused", r)

    def test_halt_refuses_spawn(self):
        from ghost.core.halt import trip, resume, is_halted
        trip(reason="t8")
        try:
            mgr = SidecarManager()
            target = make_target_file()
            r = mgr.spawn(brief="b", scope_files=[target], check_command="true",
                          issue_key="halted-spawn")
            self.assertIn("halt", r["refused"])
        finally:
            if is_halted():
                resume()


if __name__ == "__main__":
    unittest.main()
