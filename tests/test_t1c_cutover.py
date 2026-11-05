"""T1c cutover tests: one flag, no lost history, replay determinism, and the
runtime crash exit test on the new store.

Exit tests from ROADMAP_TOTAL_AUTONOMY T1:
- rollback drill: flip GHOST_STORE_MODE legacy<->unified, the app runs on both
- crash the RUNTIME at a point mid-work on the new store -> restart ->
  zero lost jobs, zero double-effects, ledger chain intact (subprocess, kill -9)
- `ghost replay --day D` reconstructs identical state from events alone,
  identically from either store
- parity: any legacy event missing from the unified store alarms
"""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(REPO, "src")

TMP = tempfile.mkdtemp(prefix="ghost-t1c-")

DRIVER = '''
import asyncio, os, sqlite3, sys, time
sys.path.insert(0, os.environ["GHOST_SRC"])
from ghost.core.jobqueue import JobQueue
from ghost.store import outbox

DB = os.environ["GHOST_UNIFIED_DB_PATH"]
N = 12

async def handler(payload):
    key = payload["key"]
    outbox.record("t1c.effect", key, payload)          # record BEFORE the effect
    conn = sqlite3.connect(DB)
    cur = conn.execute("INSERT OR IGNORE INTO effects (key, at) VALUES (?, ?)",
                       (key, time.time()))              # provider idempotency sim
    conn.commit()
    first = cur.rowcount == 1
    conn.close()
    if first:
        time.sleep(0.05)                                # widen the crash window
    outbox.transition(key, "sent")
    outbox.transition(key, "verified")

async def main():
    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS effects (key TEXT PRIMARY KEY, at REAL)")
    conn.commit()
    conn.close()
    q = JobQueue(lease_seconds=1.0)
    for i in range(N):
        q.enqueue("t1c.effect", {"key": f"t1c-key-{i}", "i": i},
                  idempotency_key=f"t1c-key-{i}")
    done = await q.run_worker({"t1c.effect": handler}, poll_interval=0.2,
                              idle_exit_after=6.0)
    print(f"driver-exit completed={done}")

asyncio.run(main())
'''


class EnvScope:
    """Pin every store path this suite touches into a private tmp root."""

    def __init__(self, test: unittest.TestCase, root: str):
        self.test = test
        self.root = root
        self.vars = ("GHOST_STORE_MODE", "GHOST_UNIFIED_DB_PATH",
                     "GHOST_AUDIT_DB_PATH", "GHOST_QUEUE_DB_PATH",
                     "GHOST_PROJECT_ROOT")
        self.old = {k: os.environ.get(k) for k in self.vars}

    def __enter__(self):
        os.environ["GHOST_PROJECT_ROOT"] = self.root
        os.environ["GHOST_UNIFIED_DB_PATH"] = os.path.join(self.root, "data", "ghost.db")
        for k in ("GHOST_AUDIT_DB_PATH", "GHOST_QUEUE_DB_PATH"):
            os.environ.pop(k, None)
        os.makedirs(os.path.join(self.root, "data"), exist_ok=True)
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def set_mode(mode: str) -> None:
    os.environ["GHOST_STORE_MODE"] = mode


class TestFacadeAndRollbackDrill(unittest.TestCase):

    def test_rollback_drill_both_modes_work(self):
        with EnvScope(self, os.path.join(TMP, "drill")):
            from ghost.core.audit import AuditLedger
            from ghost.core.jobqueue import JobQueue
            from ghost.store import facade

            set_mode("legacy")
            self.assertFalse(facade.unified())
            led = AuditLedger()
            self.assertTrue(led.db_path.endswith("audit.db"))
            self.assertEqual(led.table, "audit_events")
            led.record("drill.event", subject="legacy-1")
            q = JobQueue()
            self.assertTrue(q.db_path.endswith("queue.db"))
            q.enqueue("drill.kind", {}, idempotency_key="d1")

            set_mode("unified")
            led2 = AuditLedger()
            self.assertTrue(led2.db_path.endswith("ghost.db"))
            self.assertEqual(led2.table, "events_log")
            led2.record("drill.event", subject="unified-1")
            q2 = JobQueue()
            self.assertTrue(q2.db_path.endswith("ghost.db"))
            q2.enqueue("drill.kind", {}, idempotency_key="d2")

            # flip back — legacy store still has its event (rollback = env flip)
            set_mode("legacy")
            led3 = AuditLedger()
            subjects = [r.get("subject") for r in led3.query(event_type="drill.event")]
            self.assertIn("legacy-1", subjects)
            self.assertNotIn("unified-1", subjects)

    def test_explicit_env_wins_in_unified_mode(self):
        with EnvScope(self, os.path.join(TMP, "explicit")):
            from ghost.core.audit import AuditLedger
            set_mode("unified")
            scoped = os.path.join(TMP, "explicit", "scoped-audit.db")
            os.environ["GHOST_AUDIT_DB_PATH"] = scoped
            led = AuditLedger()
            self.assertEqual(led.db_path, scoped)
            self.assertEqual(led.table, "audit_events")


class TestUnifiedChainAndOutbox(unittest.TestCase):

    def test_unified_records_carry_a_live_chain(self):
        with EnvScope(self, os.path.join(TMP, "chain")):
            from ghost.core.audit import AuditLedger
            from ghost.store.chain import verify_hash_chain
            set_mode("unified")
            led = AuditLedger.get_instance()
            for i in range(5):
                self.assertGreater(led.record("chain.test", subject=f"s{i}", n=i), 0)
            conn = sqlite3.connect(os.environ["GHOST_UNIFIED_DB_PATH"])
            ok, detail = verify_hash_chain(conn)
            conn.close()
            self.assertTrue(ok, detail)
            self.assertIn("5 rows", detail)

    def test_outbox_state_machine(self):
        with EnvScope(self, os.path.join(TMP, "outbox")):
            from ghost.store import outbox
            rid = outbox.record("mail.send", "k1", {"to": "a@b.c"})
            self.assertIsNotNone(rid)
            self.assertEqual(outbox.record("mail.send", "k1"), rid)  # dedupe
            self.assertEqual(outbox.state_of("k1"), "pending")
            self.assertTrue(outbox.transition("k1", "sent"))
            self.assertTrue(outbox.transition("k1", "verified"))
            self.assertFalse(outbox.transition("k1", "sent"))      # terminal
            self.assertFalse(outbox.transition("ghost", "sent"))   # unknown key
            rid2 = outbox.record("mail.send", "k2")
            self.assertFalse(outbox.transition("k2", "verified"))  # illegal jump
            self.assertEqual(outbox.state_of("k2"), "pending")
            self.assertEqual(len(outbox.pending()), 1)             # only k2


class TestParityAndReplay(unittest.TestCase):

    def test_parity_subset_invariant_and_drift_alarm(self):
        root = os.path.join(TMP, "parity")
        data, configs = os.path.join(root, "data"), os.path.join(root, "configs")
        os.makedirs(data, exist_ok=True)
        os.makedirs(configs, exist_ok=True)
        from ghost.core.audit import AuditLedger
        from ghost.store import parity
        legacy = os.path.join(data, "audit.db")
        led = AuditLedger(legacy)
        for i in range(3):
            led.record("par.event", subject=f"s{i}")
        day = led.query(limit=1)[0]["day"]
        # migrate into the unified store
        from ghost.store.migrator import migrate_all
        migrate_all(os.path.join(data, "ghost.db"), data, configs)
        rep = parity.compare(legacy_audit=legacy, unified=os.path.join(data, "ghost.db"))
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["events"]["missing_in_unified"], 0)
        # unified-only (post-cutover) events are fine: legacy ⊆ unified
        uni = sqlite3.connect(os.path.join(data, "ghost.db"))
        uni.execute("INSERT INTO events_log (ts, day, event_type, subject, payload, origin) "
                    "VALUES (?, ?, 'par.event', 'post-cutover', '{}', 'main')",
                    (__import__("time").time(), day))
        uni.commit()
        uni.close()
        rep = parity.compare(legacy_audit=legacy, unified=os.path.join(data, "ghost.db"))
        self.assertTrue(rep["ok"], rep)
        # a rogue legacy write post-cutover = lost history = ALARM
        rogue = sqlite3.connect(legacy)
        rogue.execute("INSERT INTO audit_events (ts, day, event_type, subject, payload) "
                      "VALUES (?, ?, 'par.event', 'rogue', '{}')",
                      (__import__("time").time(), day))
        rogue.commit()
        rogue.close()
        rep = parity.compare(legacy_audit=legacy, unified=os.path.join(data, "ghost.db"))
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["events"]["missing_in_unified"], 1)
        self.assertTrue(rep["events"]["drift_days"])

    def test_replay_deterministic_and_store_equivalent(self):
        with EnvScope(self, os.path.join(TMP, "replay")):
            from ghost.core.audit import AuditLedger
            from ghost.store.replay import derive_day
            legacy = os.path.join(os.environ["GHOST_PROJECT_ROOT"], "data", "audit.db")
            set_mode("legacy")
            led_l = AuditLedger(legacy)
            set_mode("unified")
            led_u = AuditLedger()  # ghost.db via facade
            for i in range(4):
                for led in (led_l, led_u):
                    led.record("policy.decision", subject=f"dec_{i}", intent="GROW")
                    led.record("tokens.spent", subject=None, intent="codegen", tokens=100 + i)
            day = led_l.query(limit=1)[0]["day"]
            a1 = derive_day(day, led_l)
            a2 = derive_day(day, led_l)
            b = derive_day(day, led_u)
            self.assertEqual(a1, a2, "replay must be deterministic")
            self.assertEqual(a1, b, "replay must be identical from either store")
            self.assertEqual(a1["derived"]["policy.decision"], 4)
            self.assertEqual(a1["tokens_by_intent"]["codegen"], 4 * 100 + 6)


class TestRuntimeCrashOnNewStore(unittest.TestCase):
    """Exit test: kill -9 mid-work on ghost.db; restart finishes everything;
    zero lost jobs, zero double-effects, chain intact."""

    def test_kill9_midwork_then_resume(self):
        root = os.path.join(TMP, "crash")
        os.makedirs(os.path.join(root, "data"), exist_ok=True)
        driver_path = os.path.join(root, "driver.py")
        with open(driver_path, "w") as f:
            f.write(DRIVER)
        env = dict(os.environ,
                   GHOST_PROJECT_ROOT=root,
                   GHOST_UNIFIED_DB_PATH=os.path.join(root, "data", "ghost.db"),
                   GHOST_STORE_MODE="unified",
                   GHOST_SRC=SRC)
        env.pop("GHOST_AUDIT_DB_PATH", None)
        env.pop("GHOST_QUEUE_DB_PATH", None)
        env.pop("GHOST_HALT_FILE", None)
        env.pop("GHOST_HALT_META", None)

        # run 1: killed mid-processing
        p1 = subprocess.Popen([sys.executable, driver_path], env=env, cwd=root,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        import time as _t
        _t.sleep(0.5)
        p1.kill()
        p1.wait(timeout=10)
        for stream in (p1.stdout, p1.stderr):
            if stream:
                stream.close()

        # run 2: resume — finishes everything
        p2 = subprocess.run([sys.executable, driver_path], env=env, cwd=root,
                            capture_output=True, text=True, timeout=90)
        self.assertEqual(p2.returncode, 0, f"resume driver failed:\n{p2.stderr}")

        db = os.path.join(root, "data", "ghost.db")
        conn = sqlite3.connect(db)
        done = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='done'").fetchone()[0]
        effects = conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM outbox WHERE state='verified'").fetchone()[0]
        from ghost.store.chain import verify_hash_chain
        ok, detail = verify_hash_chain(conn)
        conn.close()
        self.assertEqual(done, 12, f"jobs lost: {done}/12 done")
        self.assertEqual(effects, 12, "double-effects detected")
        self.assertEqual(verified, 12, "outbox rows not all verified")
        self.assertTrue(ok, f"chain broken after crash+resume: {detail}")


class TestCliWiring(unittest.TestCase):

    def test_parity_and_replay_wired(self):
        from ghost import cli
        self.assertIn("parity", cli.COMMANDS)
        self.assertIn("replay", cli.COMMANDS)
        import ghost.main as main_mod
        self.assertIn("parity", main_mod.OPERATOR_COMMANDS)
        self.assertIn("replay", main_mod.OPERATOR_COMMANDS)


if __name__ == "__main__":
    unittest.main()
