"""T4 supervisor tests — an independent process that can kill-switch a dead
main, using FILE WRITES ONLY (it never opens ghost.db).

Exit tests (ROADMAP_TOTAL_AUTONOMY T4):
- kill -9 the main process -> the supervisor freezes outbound, bumps the
  revocation epoch, fires the alert — within the deadline (real subprocesses)
- killing the supervisor -> `ghost deadman` alarms within its threshold
- the supervisor never writes ghost.db (file-hash proof + import allowlist)
- rhythm misses alarm but do NOT freeze (liveness freezes; silence alarms)
- a supervisor freeze kills outstanding grants (T3 tie-in)
"""
import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(REPO, "src")
TMP = tempfile.mkdtemp(prefix="ghost-t4-")
os.environ["GHOST_GRANT_SOCKET"] = os.path.join(TMP, "no-grantd.sock")  # suite must NEVER reach a live grantd

MAIN_DRIVER = '''
import json, os, time
p = os.environ["GHOST_HEARTBEAT_PATH"]
os.makedirs(os.path.dirname(p), exist_ok=True)
end = time.time() + float(os.environ.get("DRIVER_SECS", "30"))
while time.time() < end:
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"ts": time.time(), "note": "main", "pid": os.getpid()}, f)
    os.replace(tmp, p)
    time.sleep(0.1)
'''


class RootEnv:
    """Pin a whole private root: heartbeat/halt/epoch/rhythm/supervisor files."""

    def __init__(self, test: unittest.TestCase):
        self.test = test
        self.root = tempfile.mkdtemp(prefix="ghost-t4-root-")
        os.makedirs(os.path.join(self.root, "data"), exist_ok=True)
        self.keys = ("GHOST_HEARTBEAT_PATH", "GHOST_HALT_FILE", "GHOST_HALT_META",
                     "GHOST_REVOCATION_EPOCH", "GHOST_RHYTHM_DIR",
                     "GHOST_SUPERVISOR_BEAT", "GHOST_SUPERVISOR_ALERTS",
                     "GHOST_PID_DEAD_GRACE_S", "GHOST_UNIFIED_DB_PATH",
                     "GHOST_AUDIT_DB_PATH", "GHOST_GRANT_SECRET")

    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in self.keys}
        d = os.path.join(self.root, "data")
        os.environ["GHOST_HEARTBEAT_PATH"] = os.path.join(d, "heartbeat.json")
        os.environ["GHOST_HALT_FILE"] = os.path.join(d, "HALTED")
        os.environ["GHOST_HALT_META"] = os.path.join(d, "HALTED.json")
        os.environ["GHOST_REVOCATION_EPOCH"] = os.path.join(d, "REVOCATION_EPOCH")
        os.environ["GHOST_RHYTHM_DIR"] = os.path.join(d, "rhythm")
        os.environ["GHOST_SUPERVISOR_BEAT"] = os.path.join(d, "supervisor_beat.json")
        os.environ["GHOST_SUPERVISOR_ALERTS"] = os.path.join(d, "supervisor_alerts.log")
        os.environ["GHOST_UNIFIED_DB_PATH"] = os.path.join(d, "ghost.db")
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(d, "audit.db")
        os.environ["GHOST_GRANT_SECRET"] = f"t4-{uuid.uuid4().hex}"
        os.environ["GHOST_PID_DEAD_GRACE_S"] = "0.3"   # scaled for the kill test
        self.test.addCleanup(self.__exit__)
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def path(self, name: str) -> str:
        return os.environ[name]

    def sha(self, path: str) -> str:
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return "missing"


def _subprocess_env(root_env: RootEnv) -> dict:
    env = dict(os.environ, PYTHONPATH=SRC, DRIVER_SECS="60")
    env.pop("GHOST_HALT_FILE", None) if False else None
    return env


class TestSupervisorKillMain(unittest.TestCase):
    """THE exit test: kill -9 main -> freeze + revoke + alert within deadline."""

    def test_kill9_main_then_supervisor_freezes(self):
        with RootEnv(self) as env:
            # ghost.db exists (the file the supervisor must NEVER touch)
            ghost_db = env.path("GHOST_UNIFIED_DB_PATH")
            open(ghost_db, "wb").write(b"sqlite-placeholder")
            db_sha_before = env.sha(ghost_db)

            main = subprocess.Popen([sys.executable, "-c", MAIN_DRIVER],
                                    env=_subprocess_env(env), cwd=self.root_dir(env),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            sup = subprocess.Popen(
                [sys.executable, "-m", "ghost.supervisor.service", "--interval", "0.2"],
                env=_subprocess_env(env), cwd=self.root_dir(env),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(0.8)                 # both loops alive and fresh
                self.assertFalse(os.path.exists(env.path("GHOST_HALT_FILE")))
                main.kill()                     # kill -9 the main process
                main.wait(timeout=10)
                deadline = time.time() + 30.0   # scaled exit bound (prod: 60s)
                while time.time() < deadline:
                    if os.path.exists(env.path("GHOST_HALT_FILE")):
                        break
                    time.sleep(0.1)
                self.assertTrue(os.path.exists(env.path("GHOST_HALT_FILE")),
                                "supervisor did not freeze a dead main in time")
                elapsed = time.time() - (deadline - 30.0)
                self.assertLess(elapsed, 30.0)
                meta = json.load(open(env.path("GHOST_HALT_META")))
                self.assertEqual(meta["by"], "supervisor")
                self.assertEqual(meta["reason"], "heartbeat_lost")
                self.assertGreaterEqual(meta.get("revocation_epoch", 0), 1,
                                        "freeze must bump the revocation epoch")
                # the epoch FILE moved too (cross-process proof)
                with open(env.path("GHOST_REVOCATION_EPOCH")) as f:
                    self.assertGreaterEqual(int(f.read().strip() or 0), 1)
                # alert fired (durable trail)
                with open(env.path("GHOST_SUPERVISOR_ALERTS")) as f:
                    self.assertIn("freeze", f.read())
                # AND the supervisor never touched the store
                self.assertEqual(db_sha_before, env.sha(ghost_db),
                                 "supervisor wrote ghost.db — single-writer violation")
            finally:
                sup.kill()
                sup.wait(timeout=10)
                if main.poll() is None:
                    main.kill()

    @staticmethod
    def root_dir(env: RootEnv) -> str:
        return env.root


class TestSupervisorBehavior(unittest.TestCase):

    def test_live_main_is_left_alone(self):
        with RootEnv(self) as env:
            from ghost.core import heartbeat
            heartbeat.beat("main")     # fresh beat with pid (this process)
            from ghost.supervisor import service
            res = service.check_cycle()
            self.assertFalse(os.path.exists(env.path("GHOST_HALT_FILE")))
            self.assertNotIn("freeze", " ".join(res["actions"]))

    def test_freeze_kills_outstanding_grants(self):
        with RootEnv(self) as env:
            from ghost.core import grants, heartbeat
            heartbeat.beat("main")     # fresh, then the heartbeat goes stale
            token = grants.issue("outbound_email", "lead-x", {"daily_cap": 5}, ttl_s=600.0)
            self.assertIsNotNone(grants.verify(token, capability="outbound_email",
                                               subject="lead-x"))
            token2 = grants.issue("outbound_email", "lead-y", {"daily_cap": 5}, ttl_s=600.0)
            # main dies: stale heartbeat + dead pid
            stale = {"ts": time.time() - 400.0, "pid": 999999}
            json.dump(stale, open(env.path("GHOST_HEARTBEAT_PATH"), "w"))
            from ghost.supervisor import service
            res = service.check_cycle()
            self.assertTrue(any(a.startswith("freeze") for a in res["actions"]), res)
            # T3 tie-in: the pre-freeze grant is dead (epoch bumped by freeze)
            self.assertIsNone(grants.verify(token2, capability="outbound_email",
                                            subject="lead-y"))
            # and the halt meta carries the supervisor's signature
            meta = json.load(open(env.path("GHOST_HALT_META")))
            self.assertEqual(meta["by"], "supervisor")

    def test_supervisor_never_imports_the_store(self):
        """Structural half of the single-writer guarantee (file-hash is the
        dynamic half, proven in the kill test)."""
        sup_dir = os.path.join(SRC, "ghost", "supervisor")
        banned = ("ghost.core.audit", "ghost.store", "ghost.crm", "ghost.core.jobqueue",
                  "ghost.workers", "ghost.autonomy", "smtplib", "requests")
        for py in os.listdir(sup_dir):
            if not py.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(sup_dir, py)).read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        self.assertFalse(any(a.name == b or a.name.startswith(b + ".")
                                             for b in banned), f"{py}: imports {a.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    self.assertFalse(any(mod == b or mod.startswith(b + ".")
                                         for b in banned), f"{py}: imports {mod}")

    def test_rhythm_miss_alarms_but_does_not_freeze(self):
        with RootEnv(self) as env:
            from ghost.core import heartbeat, rhythm
            heartbeat.beat("main")   # main alive
            cfg = {"events": {"digest": {"by": "00:00", "days": [0, 1, 2, 3, 4, 5, 6],
                                         "grace_min": 0}}}
            res = service_check(cfg)
            self.assertTrue(any(a.startswith("alarm(rhythm:digest)") for a in res["actions"]),
                            res)
            self.assertFalse(os.path.exists(env.path("GHOST_HALT_FILE")),
                             "a rhythm miss must ALARM, never freeze")
            # marked today -> no new alarm
            rhythm.mark("digest")
            res2 = service_check(cfg)
            self.assertFalse(any(a.startswith("alarm(rhythm") for a in res2["actions"]))

    def test_deadman_alarms_on_silent_supervisor(self):
        with RootEnv(self) as env:
            from ghost.supervisor import service
            service.beat()
            self.assertEqual(service.deadman(threshold_s=60.0), 0)
            # backdate the beat beyond the threshold
            with open(env.path("GHOST_SUPERVISOR_BEAT"), "w") as f:
                json.dump({"ts": time.time() - 3600.0}, f)
            self.assertEqual(service.deadman(threshold_s=60.0), 1)
            with open(env.path("GHOST_SUPERVISOR_ALERTS")) as f:
                self.assertIn("DEAD-MAN", f.read())

    def test_cli_wiring(self):
        from ghost import cli
        import ghost.main as main_mod
        self.assertIn("supervise", cli.COMMANDS)
        self.assertIn("deadman", cli.COMMANDS)
        self.assertIn("supervise", main_mod.OPERATOR_COMMANDS)
        self.assertIn("deadman", main_mod.OPERATOR_COMMANDS)


def service_check(cfg):
    from ghost.supervisor import service
    return service.check_cycle(cfg)


if __name__ == "__main__":
    unittest.main()
