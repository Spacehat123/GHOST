"""T1b migration-harness tests: resumable, checksummed, reversible.

Exit tests from ROADMAP_TOTAL_AUTONOMY T1:
- crash the MIGRATOR mid-copy -> re-run -> completes, zero duplicates, parity
  green (real os-level kill via subprocess + GHOST_MIGRATION_CRASH_AFTER)
- idempotent re-run: a second full run changes nothing
- old stores are NEVER mutated (they are the rollback)
- drafts slug->id rewrite; unresolvable refs -> quarantine, never dropped
- events_log hash chain verifies; tampering is detected
- FTS rebuilt over copied episodes
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

TMP = tempfile.mkdtemp(prefix="ghost-t1b-")
DATA = os.path.join(TMP, "data")
CONFIGS = os.path.join(TMP, "configs")
os.makedirs(DATA, exist_ok=True)
os.makedirs(CONFIGS, exist_ok=True)
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(DATA, "audit.db")

from ghost.crm.db import CRMDatabase
from ghost.core.audit import AuditLedger
from ghost.core.jobqueue import JobQueue
from ghost.store.migrator import migrate_all, verify_hash_chain

# Census-verified source DDL for the raw fixtures (memory/missions/watchdog).
EPISODES_DDL = """CREATE TABLE episodes (id TEXT PRIMARY KEY, task_id TEXT,
    query TEXT, summary TEXT, observation TEXT, outcome TEXT, error TEXT,
    lessons_learned TEXT, importance REAL, created_at REAL, metadata_json TEXT)"""
MISSIONS_DDL = """CREATE TABLE missions (id TEXT PRIMARY KEY, goal TEXT,
    created_at TEXT, status TEXT, skill TEXT, notes TEXT, total_elapsed REAL,
    total_tokens INTEGER, metadata TEXT)"""
TASKS_DDL = """CREATE TABLE tasks (id TEXT NOT NULL, mission_id TEXT NOT NULL,
    idx INTEGER, title TEXT, description TEXT, acceptance TEXT,
    evidence_required TEXT, depends_on TEXT, status TEXT, evidence TEXT,
    result_summary TEXT, attempts INTEGER, budget TEXT, tools_allowed TEXT,
    task_type TEXT, PRIMARY KEY (id, mission_id))"""
TOOL_CALLS_DDL = """CREATE TABLE tool_calls (id TEXT PRIMARY KEY,
    mission_id TEXT, task_id TEXT, tool_name TEXT, command TEXT,
    exit_code INTEGER, output TEXT, created_at TEXT, metadata TEXT)"""
SYSTEM_EVENTS_DDL = """CREATE TABLE system_events (id TEXT PRIMARY KEY,
    signature TEXT, event_type TEXT, source TEXT, summary TEXT,
    payload_json TEXT, urgency TEXT, occurrence_count INTEGER,
    first_seen_at REAL, last_seen_at REAL, occurrences_json TEXT, status TEXT)"""


def _sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _raw_db(path: str, statements):
    conn = sqlite3.connect(path)
    for s in statements:
        conn.execute(s)
    return conn


def build_sources(data_dir: str, configs_dir: str) -> None:
    """Fixture sources mirroring the live shapes (census-verified)."""
    # --- CRM via the real store class ---
    crm = CRMDatabase(os.path.join(data_dir, "crm.db"))
    crm.add_or_update_lead({"slug": "slug-resolvable", "id": "slug-resolvable",
                            "name": "R1", "status": "harvested"})
    crm.add_or_update_lead({"slug": "slug-uuid", "name": "R2", "status": "harvested"})
    lead_id_uuid = crm.get_lead("slug-uuid")["id"]
    crm.add_or_update_draft(lead_id="slug-resolvable", channel="email",
                            body="b1", subject="s1")            # rewrites to id
    crm.add_or_update_draft(lead_id=lead_id_uuid, channel="email",
                            body="b2", subject="s2")            # already an id
    crm.add_or_update_draft(lead_id="ghost-lead", channel="email",
                            body="b3", subject="s3")            # orphan -> quarantine

    # --- audit ledger ---
    led = AuditLedger(os.path.join(data_dir, "audit.db"))
    for i in range(3):
        led.record("test.event", subject=f"e{i}", n=i)

    # --- job queue ---
    q = JobQueue(os.path.join(data_dir, "queue.db"))
    for i in range(5):
        q.enqueue("test.kind", {"i": i}, idempotency_key=f"tk{i}")

    # --- memory (raw) ---
    mem = _raw_db(os.path.join(data_dir, "memory.db"), [EPISODES_DDL])
    for i in range(30):
        mem.execute("INSERT INTO episodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"ep{i}", f"task_{1793779018000 + i}", f"q{i}", "s", "o",
                     "ok", None, "l", 0.5, 1793779018000.0 + i, "{}"))
    mem.commit()
    mem.close()

    # --- missions (raw, composite PK) ---
    mis = _raw_db(os.path.join(data_dir, "missions.db"),
                  [MISSIONS_DDL, TASKS_DDL, TOOL_CALLS_DDL])
    mis.execute("INSERT INTO missions VALUES ('m1', 'g', '2026-11-06', 'open', NULL, NULL, 0, 0, '{}')")
    mis.execute("INSERT INTO tasks VALUES ('t1', 'm1', 0, 'T', NULL, NULL, NULL, 'open', NULL, NULL, 0, NULL, NULL, NULL, NULL)")
    mis.execute("INSERT INTO tool_calls VALUES ('tc1', 'm1', 't1', 'tool', 'cmd', 0, '', '2026', '{}')")
    mis.execute("INSERT INTO tool_calls VALUES ('tc2', 'm1', 'bogus-task', 'tool', 'cmd', 0, '', '2026', '{}')")
    mis.commit()
    mis.close()

    # --- watchdog events ---
    wd = _raw_db(os.path.join(data_dir, "events.db"), [SYSTEM_EVENTS_DDL])
    wd.execute("INSERT INTO system_events VALUES ('se1', 'sig1', 't', 'src', 's', '{}', 'low', 1, 0, 0, '[]', 'open')")
    wd.commit()
    wd.close()

    # --- JSON state (census-verified shapes) ---
    with open(os.path.join(configs_dir, "token_stats.json"), "w") as f:
        json.dump({"last": 1, "last_cost": 0.1, "today": 2, "today_cost": 0.2,
                   "history": [
                       {"task_id": "task_x", "prompt_tokens": 30, "completion_tokens": 20,
                        "total_tokens": 50, "model_calls": 2, "tool_calls": 1,
                        "breakdown": {"memory": 296}, "cost_usd": 1.7e-05,
                        "model": "test-model", "created_at": 1793902880.48},
                       "MALFORMED-NOT-A-DICT"]}, f)
    with open(os.path.join(data_dir, "conversation_history.json"), "w") as f:
        json.dump([{"role": "user", "content": "hi", "timestamp": "t1"},
                   {"role": "assistant", "content": "hello", "timestamp": "t2"}], f)
    with open(os.path.join(data_dir, "ladder_state.json"), "w") as f:
        json.dump({"capabilities": {"contracts": {
            "level": 2, "success_streak": 3, "total_successes": 9,
            "total_incidents": 0, "eligible_for": 2, "updated_at": 1793902880.0}}}, f)
    with open(os.path.join(data_dir, "outbound_state.json"), "w") as f:
        json.dump({"day": "2026-11-06", "sends_today": 4}, f)
    with open(os.path.join(data_dir, "user_profile.json"), "w") as f:
        json.dump({"user_name": "Pranav", "preferences": {"dark_mode": True}}, f)
    with open(os.path.join(configs_dir, "session_state.json"), "w") as f:
        json.dump({"conversation_id": "c1", "turn_count": 3}, f)
    with open(os.path.join(configs_dir, "golden_library.json"), "w") as f:
        json.dump({"restaurant": {"demo_dir": "/tmp/demo", "registered_at": 1793902880.0,
                                  "qa_evidence": {"screenshots": 3}}}, f)


build_sources(DATA, CONFIGS)
SOURCE_FILES = [os.path.join(DATA, f) for f in
                ("crm.db", "audit.db", "queue.db", "memory.db", "missions.db", "events.db")]


class TestT1bMigration(unittest.TestCase):

    def test_01_full_migration_parity_and_resolution(self):
        target = os.path.join(TMP, "ghost.db")
        report = migrate_all(target, DATA, CONFIGS)
        tables = report["tables"]
        self.assertEqual([t for t, r in tables.items() if r["state"] == "error"], [])
        self.assertEqual([t for t, r in tables.items() if r["state"] == "parity_failed"], [])

        tgt = sqlite3.connect(target)
        # drafts: 2 copied (one rewritten slug->id, one already an id), 1 quarantined
        drafts = [r[0] for r in tgt.execute("SELECT lead_id FROM crm_drafts")]
        self.assertEqual(len(drafts), 2)
        lead_ids = {r[0] for r in tgt.execute("SELECT id FROM crm_leads")}
        for lid in drafts:
            self.assertIn(lid, lead_ids, "draft must reference a real lead id after rewrite")
        # orphan + bogus task quarantined with reasons, never silently dropped
        q = " | ".join(r for (r,) in tgt.execute(
            "SELECT reason FROM migration_quarantine"))
        self.assertIn("unresolvable lead ref ghost-lead", q)
        self.assertIn("unresolvable task id bogus-task", q)
        # valid tool_call kept (§6-4)
        self.assertEqual(tgt.execute(
            "SELECT COUNT(*) FROM mission_tool_calls WHERE task_id='t1'").fetchone()[0], 1)
        # episodes 30/30, FTS rebuilt to match
        self.assertEqual(tgt.execute("SELECT COUNT(*) FROM memory_episodes").fetchone()[0], 30)
        self.assertEqual(tgt.execute("SELECT COUNT(*) FROM memory_episodes_fts").fetchone()[0], 30)
        # malformed token history entry quarantined, good one flattened
        self.assertEqual(tgt.execute("SELECT COUNT(*) FROM token_spend").fetchone()[0], 1)
        self.assertIn("malformed token record", q)
        # JSON state flattened
        self.assertEqual(tgt.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0], 2)
        self.assertEqual(tgt.execute("SELECT COUNT(*) FROM ladder_state").fetchone()[0], 1)
        self.assertEqual(tgt.execute("SELECT COUNT(*) FROM golden_demos").fetchone()[0], 1)
        self.assertEqual(tgt.execute("SELECT COUNT(*) FROM operator_profile").fetchone()[0], 2)
        self.assertEqual(tgt.execute("SELECT COUNT(*) FROM agency_session").fetchone()[0], 2)
        # events_log: copied + hash chain verified
        ok, detail = verify_hash_chain(tgt)
        self.assertTrue(ok, detail)
        tgt.close()

    def test_02_idempotent_rerun_no_duplicates(self):
        target = os.path.join(TMP, "ghost.db")
        migrate_all(target, DATA, CONFIGS)
        r2 = migrate_all(target, DATA, CONFIGS)
        for t, rep in r2["tables"].items():
            if rep["state"] == "skipped":
                continue
            self.assertEqual(rep["state"], "done", f"{t}: {rep}")
            if rep.get("copied_this_run") is not None:
                self.assertEqual(rep["copied_this_run"], 0,
                                 f"{t}: second run must copy nothing: {rep}")
        tgt = sqlite3.connect(target)
        self.assertEqual(tgt.execute("SELECT COUNT(*) FROM crm_drafts").fetchone()[0], 2)
        # quarantine NOT doubled by the re-run (idempotency of resolvers)
        self.assertEqual(tgt.execute("SELECT COUNT(*) FROM migration_quarantine").fetchone()[0], 3)
        tgt.close()

    def test_03_sources_never_mutated(self):
        before = {p: _sha(p) for p in SOURCE_FILES}
        migrate_all(os.path.join(TMP, "ghost.db"), DATA, CONFIGS)
        after = {p: _sha(p) for p in SOURCE_FILES}
        self.assertEqual(before, after, "sources are the rollback — they must never change")

    def test_04_crash_and_resume(self):
        """Exit test: crash the migrator mid-copy (os-level), re-run completes
        with zero duplicates and parity green."""
        crash_dir = tempfile.mkdtemp(prefix="ghost-t1b-crash-")
        cdata, cconfigs = os.path.join(crash_dir, "data"), os.path.join(crash_dir, "configs")
        os.makedirs(cdata)
        os.makedirs(cconfigs)
        build_sources(cdata, cconfigs)
        target = os.path.join(crash_dir, "ghost.db")
        env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src"),
                   GHOST_MIGRATION_CRASH_AFTER="45")
        proc = subprocess.run(
            [sys.executable, "-m", "ghost.store.migrator",
             "--target", target, "--data-dir", cdata, "--configs-dir", cconfigs],
            capture_output=True, text=True, env=env, cwd=REPO, timeout=60)
        self.assertNotEqual(proc.returncode, 0, f"crash injection did not fire:\n{proc.stdout}")
        self.assertIn("CRASH INJECTION", proc.stderr)
        partial = sqlite3.connect(target)
        partial_rows = partial.execute("SELECT COUNT(*) FROM memory_episodes").fetchone()[0]
        partial.close()
        self.assertLess(partial_rows, 30, "crash should have landed mid-episodes")
        # resume: completes, parity green, nothing lost, nothing duplicated
        report = migrate_all(target, cdata, cconfigs)
        for t, rep in report["tables"].items():
            if rep["state"] == "skipped":
                continue
            self.assertEqual(rep["state"], "done", f"{t}: {rep}")
        done = sqlite3.connect(target)
        self.assertEqual(done.execute("SELECT COUNT(*) FROM memory_episodes").fetchone()[0], 30)
        self.assertEqual(done.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 5)
        self.assertEqual(done.execute("SELECT COUNT(*) FROM crm_drafts").fetchone()[0], 2)
        done.close()

    def test_05_hash_chain_detects_tampering(self):
        tampered = os.path.join(TMP, "tampered.db")
        src = sqlite3.connect(os.path.join(TMP, "ghost.db"))
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # fold WAL into the main file
        src.close()
        shutil.copy(os.path.join(TMP, "ghost.db"), tampered)
        conn = sqlite3.connect(tampered)
        conn.execute("UPDATE events_log SET payload='{\"tampered\": true}' "
                     "WHERE id=(SELECT MIN(id) FROM events_log)")
        conn.commit()
        ok, detail = verify_hash_chain(conn)
        conn.close()
        self.assertFalse(ok, "tampered chain must fail verification")
        self.assertIn("mismatch", detail)

    def test_06_cli_wiring(self):
        from ghost import cli
        self.assertIn("migrate", cli.COMMANDS)


if __name__ == "__main__":
    unittest.main()
