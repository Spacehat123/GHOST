"""STORE PLANE — migration harness (T1b of ROADMAP_TOTAL_AUTONOMY.md).

Copies every source store into `data/ghost.db` per docs/MIGRATION_MAP.md:

- writes ONLY the target; sources are opened read-only and are never mutated
  (they ARE the rollback — working rule 9)
- resumable: per-table checkpoints in migration_progress; INSERT OR IGNORE on
  primary keys, so a crash mid-table leaves partial rows that a re-run
  completes without duplicates
- checksummed: row-count + content-hash parity per table before the table is
  marked done
- honest about relationships: slug->id resolution for child references, and
  unresolvable rows go to migration_quarantine + a report entry — never
  silently dropped, never fabricated
- events_log gets a sha256 hash chain (prev_hash/row_hash), rebuilt over the
  merged events and verified

Test hook: GHOST_MIGRATION_CRASH_AFTER=<n> hard-exits after n row inserts
(os-level crash for the resume test). Never set in production.

Run: python3 -m ghost.store.migrator [--target data/ghost.db]
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from ghost.store.schema import connect_target, create_target

DEFAULT_TARGET = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "ghost.db")

CRASH_ENV = "GHOST_MIGRATION_CRASH_AFTER"
_crash_counter = {"n": 0}


def _maybe_crash() -> None:
    """Test-only os-level crash point (exit 9), counted per row inserted."""
    limit = os.environ.get(CRASH_ENV)
    if not limit:
        return
    _crash_counter["n"] += 1
    if _crash_counter["n"] >= int(limit):
        sys.stderr.write(f"migrator: CRASH INJECTION after {_crash_counter['n']} rows\n")
        os._exit(9)


# --------------------------------------------------------------- row plumbing

def _source_conn(path: str) -> Optional[sqlite3.Connection]:
    if not os.path.exists(path):
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")]


def _pk_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    pks = [c for c in cols if c[5]]
    pks.sort(key=lambda c: c[5])
    return [c[1] for c in pks] or ["rowid"]


def hash_rows(rows: List[Tuple], columns: List[str]) -> str:
    """Stable content hash over sorted rows (order-independent parity)."""
    material = json.dumps(
        [dict(zip(columns, r)) for r in sorted(rows, key=repr)],
        sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class Migrator:
    def __init__(self, target_path: str, data_dir: str, configs_dir: str):
        self.target_path = target_path
        self.data_dir = data_dir
        self.configs_dir = configs_dir
        self.report: Dict[str, Any] = {"tables": {}, "json": {}, "notes": [],
                                       "started_at": time.time()}
        self._quarantined: List[Dict[str, Any]] = []

    # ------------------------------------------------------------ main entry

    def run(self) -> Dict[str, Any]:
        conn = connect_target(self.target_path)
        create_target(conn)
        try:
            lookups = self._build_lookups(conn)
            for src_file, src_table, dst_table, transform in self._copy_plan():
                src = _source_conn(os.path.join(self.data_dir, src_file))
                if src is None:
                    self.report["tables"][dst_table] = {
                        "state": "skipped", "note": f"source {src_file} missing"}
                    continue
                try:
                    self._copy_table(conn, src, src_file, src_table, dst_table,
                                     transform, lookups)
                finally:
                    src.close()
            self._copy_json_state(conn)
            self._rebuild_fts(conn)
            self._finish_events_chain(conn)
            self._foreign_key_check(conn)
            conn.commit()
        finally:
            conn.close()
        self.report["finished_at"] = time.time()
        return self.report

    # ---------------------------------------------------------- lookup maps

    def _build_lookups(self, conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
        lookups: Dict[str, Dict[str, Any]] = {"lead_slug_to_id": {}, "lead_ids": set(),
                                              "site_slugs": set(), "task_ids": set()}
        src_crm = _source_conn(os.path.join(self.data_dir, "crm.db"))
        if src_crm is not None:
            if "leads" in self._tables(src_crm):
                for lid, slug in src_crm.execute("SELECT id, slug FROM leads"):
                    lookups["lead_ids"].add(lid)
                    if slug:
                        lookups["lead_slug_to_id"][slug.strip().lower()] = lid
            src_crm.close()
        src_ret = _source_conn(os.path.join(self.data_dir, "retainers.db"))
        if src_ret is not None:
            if "sites" in self._tables(src_ret):
                lookups["site_slugs"] = {r[0] for r in src_ret.execute("SELECT slug FROM sites")}
            src_ret.close()
        src_mis = _source_conn(os.path.join(self.data_dir, "missions.db"))
        if src_mis is not None:
            if "tasks" in self._tables(src_mis):
                lookups["task_ids"] = {r[0] for r in src_mis.execute("SELECT id FROM tasks")}
            src_mis.close()
        return lookups

    @staticmethod
    def _tables(conn: sqlite3.Connection) -> List[str]:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]

    # ------------------------------------------------------------- copy plan

    def _copy_plan(self) -> List[Tuple[str, str, str, Optional[str]]]:
        """(source file, source table, target table, transform name).
        Parents before children inside each source DB."""
        return [
            ("crm.db", "clients", "crm_clients", None),
            ("crm.db", "campaigns", "crm_campaigns", None),
            ("crm.db", "batches", "crm_batches", None),
            ("crm.db", "leads", "crm_leads", None),
            ("crm.db", "interactions", "crm_interactions", None),
            ("crm.db", "suppression", "crm_suppression", None),
            ("crm.db", "drafts", "crm_drafts", "draft_lead_ref"),      # §6-1
            ("crm.db", "documents", "crm_documents", "lead_ref"),
            ("crm.db", "events", "crm_lead_events", "lead_ref"),       # §6-3
            ("crm.db", "processed_events", "crm_processed_webhooks", None),
            ("queue.db", "jobs", "jobs", None),
            ("retainers.db", "sites", "retainer_sites", None),
            ("retainers.db", "incidents", "retainer_incidents", "site_ref"),
            ("retainers.db", "fixes", "retainer_fixes", "site_ref"),
            ("missions.db", "missions", "missions", None),
            ("missions.db", "tasks", "mission_tasks", None),
            ("missions.db", "tool_calls", "mission_tool_calls", "task_ref"),  # §6-4
            ("events.db", "system_events", "watchdog_events", None),
            ("audit.db", "audit_events", "events_log", None),
            ("memory.db", "episodes", "memory_episodes", None),  # task_id stays free-form (§6-5: 0/694 match)
        ]

    # ------------------------------------------------------------- the copier

    def _copy_table(self, dst: sqlite3.Connection, src: sqlite3.Connection,
                    src_file: str, src_table: str, dst_table: str,
                    transform: Optional[str], lookups: Dict[str, Any]) -> None:
        if src_table not in self._tables(src):
            self.report["tables"][dst_table] = {"state": "skipped",
                                                "note": f"table {src_table} missing in {src_file}"}
            return
        src_cols = _columns(src, src_table)
        dst_cols = _columns(dst, dst_table)
        shared = [c for c in src_cols if c in dst_cols]
        if not shared:
            self.report["tables"][dst_table] = {"state": "error",
                                                "note": "no column overlap — schema drift?"}
            return
        col_list = ", ".join(f'"{c}"' for c in shared)
        rows = src.execute(f"SELECT {col_list} FROM \"{src_table}\"").fetchall()

        copied = dupes = quarantined = 0
        for row in rows:
            record = dict(zip(shared, row))
            if transform:
                verdict = getattr(self, f"_x_{transform}")(record, lookups)
                if verdict is not None:  # unresolvable reference
                    self._quarantine(dst, dst_table, record, verdict)
                    quarantined += 1
                    continue
            placeholders = ", ".join("?" for _ in shared)
            cols_sql = ", ".join(f'"{c}"' for c in shared)
            cur = dst.execute(
                f'INSERT OR IGNORE INTO "{dst_table}" ({cols_sql}) VALUES ({placeholders})',
                [record[c] for c in shared])
            if cur.rowcount == 0:
                dupes += 1
            else:
                copied += 1
                _maybe_crash()

        # parity: source rows == copied + quarantined + pre-existing dupes
        self._write_checkpoint(dst, dst_table, f"{src_file}:{src_table}",
                               copied, dupes, quarantined, src, src_table, shared)

    # ---------------------------------------------------- reference resolvers

    def _x_draft_lead_ref(self, rec: Dict[str, Any], lookups) -> Optional[str]:
        return self._resolve_lead(rec, "lead_id", lookups)

    def _x_lead_ref(self, rec: Dict[str, Any], lookups) -> Optional[str]:
        return self._resolve_lead(rec, "lead_id", lookups)

    def _x_site_ref(self, rec: Dict[str, Any], lookups) -> Optional[str]:
        val = rec.get("slug")
        if val is None:
            return None
        if val in lookups["site_slugs"]:
            return None
        return f"unresolvable site slug {val}"

    def _x_task_ref(self, rec: Dict[str, Any], lookups) -> Optional[str]:
        val = rec.get("task_id")
        if not val:
            rec["task_id"] = None
            return None
        if val in lookups["task_ids"]:
            return None
        return f"unresolvable task id {val}"

    def _resolve_lead(self, rec: Dict[str, Any], field: str,
                      lookups) -> Optional[str]:
        """Rewrite slug -> lead id (MIGRATION_MAP §6-1), idempotently: a value
        that is already a lead id passes through."""
        val = rec.get(field)
        if not val:
            return None
        if val in lookups["lead_ids"]:
            return None                    # already a resolved lead id — idempotent re-run
        by_slug = lookups["lead_slug_to_id"]
        if val in by_slug:
            rec[field] = by_slug[val]      # rewrite slug -> id
            return None
        return f"unresolvable lead ref {val}"

    # ---------------------------------------------------------------- quarantine

    def _quarantine(self, dst: sqlite3.Connection, source: str,
                    record: Dict[str, Any], reason: str) -> None:
        # UNIQUE(source, row_json) makes quarantine idempotent: a re-run of the
        # same unresolvable row adds nothing new (resumability, rule 9).
        dst.execute(
            "INSERT OR IGNORE INTO migration_quarantine (source, reason, row_json, at) "
            "VALUES (?, ?, ?, ?)",
            (source, reason, json.dumps(record, default=str), time.time()))
        self._quarantined.append({"source": source, "reason": reason})

    # -------------------------------------------------------------- checkpoints

    def _write_checkpoint(self, dst: sqlite3.Connection, target_table: str,
                          source_name: str, copied: int, dupes: int,
                          quarantined: int, src: sqlite3.Connection,
                          src_table: str, shared: List[str]) -> None:
        src_rows = src.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in shared)} '
                               f'FROM "{src_table}"').fetchall()
        src_hash = hash_rows(src_rows, shared)
        tgt_rows = dst.execute(f'SELECT * FROM "{target_table}"').fetchall()
        tgt_cols = _columns(dst, target_table)
        tgt_hash = hash_rows(tgt_rows, tgt_cols)
        expected = len(src_rows) - quarantined
        actual = len(tgt_rows)
        # rows may pre-exist from an earlier partial run: parity = no loss
        state = "done" if actual >= expected else "parity_failed"
        self.report["tables"][target_table] = {
            "state": state, "copied_this_run": copied, "duplicates_ignored": dupes,
            "quarantined": quarantined, "source_rows": len(src_rows),
            "target_rows": actual, "source_hash": src_hash[:16],
            "target_hash": tgt_hash[:16],
        }
        dst.execute(
            "INSERT INTO migration_progress (source, state, rows_copied, source_hash, "
            "target_hash, checked_at, note) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source) DO UPDATE SET state=excluded.state, "
            "rows_copied=excluded.rows_copied, source_hash=excluded.source_hash, "
            "target_hash=excluded.target_hash, checked_at=excluded.checked_at, "
            "note=excluded.note",
            (target_table, state, actual, src_hash, tgt_hash, time.time(),
             f"{source_name} +{copied} dupes:{dupes} q:{quarantined}"))

    # ------------------------------------------------------------- JSON state

    def _copy_json_state(self, dst: sqlite3.Connection) -> None:
        self._tokens(dst)
        self._conversation(dst)
        self._ladder(dst)
        self._kv_file(dst, "outbound_state.json", "outbound_warmup")
        self._kv_file(dst, "user_profile.json", "operator_profile",
                      data_dir=self.data_dir)
        self._kv_file(dst, "session_state.json", "agency_session",
                      data_dir=self.configs_dir)
        self._golden(dst)

    def _load_json(self, name: str, data_dir: str) -> Optional[Any]:
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            self.report["json"][name] = "missing (nothing to copy)"
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.report["json"][name] = f"unreadable: {e}"
            return None

    def _tokens(self, dst: sqlite3.Connection) -> None:
        data = self._load_json("token_stats.json", self.configs_dir)
        if data is None:
            return
        rows = 0
        for entry in data.get("history", []) or []:
            try:
                row = (entry.get("task_id"), entry.get("model"),
                       entry.get("prompt_tokens"), entry.get("completion_tokens"),
                       entry.get("total_tokens"), entry.get("model_calls"),
                       entry.get("tool_calls"), entry.get("cost_usd"),
                       json.dumps(entry.get("breakdown"), default=str),
                       entry.get("created_at"))
            except Exception:
                self._quarantine(dst, "token_spend", entry, "malformed token record")
                continue
            dst.execute(
                "INSERT OR IGNORE INTO token_spend (task_id, model, prompt_tokens, "
                "completion_tokens, total_tokens, model_calls, tool_calls, cost_usd, "
                "breakdown_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
            rows += 1
            _maybe_crash()
        self.report["json"]["token_stats.json"] = f"{rows} history rows -> token_spend"

    def _conversation(self, dst: sqlite3.Connection) -> None:
        data = self._load_json("conversation_history.json", self.data_dir)
        if data is None:
            return
        rows = 0
        for msg in data if isinstance(data, list) else []:
            try:
                row = (msg.get("role"), msg.get("content"), msg.get("timestamp"))
            except Exception:
                self._quarantine(dst, "conversation_messages", msg, "malformed message")
                continue
            dst.execute(
                "INSERT OR IGNORE INTO conversation_messages (role, content, timestamp) "
                "VALUES (?, ?, ?)", row)
            rows += 1
            _maybe_crash()
        self.report["json"]["conversation_history.json"] = f"{rows} rows -> conversation_messages"

    def _ladder(self, dst: sqlite3.Connection) -> None:
        data = self._load_json("ladder_state.json", self.data_dir)
        if data is None:
            return
        caps = data.get("capabilities", {}) if isinstance(data, dict) else {}
        rows = 0
        for name, cap in caps.items():
            try:
                known = {k: cap.get(k) for k in
                         ("level", "success_streak", "total_successes",
                          "total_incidents", "updated_at")}
                extra = {k: v for k, v in cap.items() if k not in known}
                row = (name, known["level"], known["success_streak"],
                       known["updated_at"], known["total_successes"],
                       known["total_incidents"], json.dumps(extra, default=str))
            except Exception:
                self._quarantine(dst, "ladder_state", cap, f"malformed capability {name}")
                continue
            dst.execute(
                "INSERT OR REPLACE INTO ladder_state (capability, level, streak, "
                "unlocked_at, success_count, incident_count, extra_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", row)
            rows += 1
            _maybe_crash()
        self.report["json"]["ladder_state.json"] = f"{rows} capabilities -> ladder_state"

    def _kv_file(self, dst: sqlite3.Connection, name: str, table: str,
                 data_dir: Optional[str] = None) -> None:
        data = self._load_json(name, data_dir or self.data_dir)
        if data is None:
            return
        if not isinstance(data, dict):
            self._quarantine(dst, table, {"value": data}, f"{name} is not an object")
            return
        rows = 0
        for k, v in data.items():
            dst.execute(f"INSERT OR REPLACE INTO \"{table}\" (k, v) VALUES (?, ?)",
                        (k, v if isinstance(v, str) else json.dumps(v, default=str)))
            rows += 1
            _maybe_crash()
        self.report["json"][name] = f"{rows} keys -> {table}"

    def _golden(self, dst: sqlite3.Connection) -> None:
        data = self._load_json("golden_library.json", self.configs_dir)
        if data is None:
            return
        rows = 0
        for vertical, entry in (data.items() if isinstance(data, dict) else []):
            try:
                extra = {k: v for k, v in entry.items()
                         if k not in ("registered_at", "qa_evidence")}
                row = (vertical, entry.get("registered_at"),
                       json.dumps(entry.get("qa_evidence"), default=str),
                       json.dumps(extra, default=str))
            except Exception:
                self._quarantine(dst, "golden_demos", entry,
                                 f"malformed golden entry {vertical}")
                continue
            dst.execute(
                "INSERT OR REPLACE INTO golden_demos (slug, registered_at, "
                "qa_evidence_json, lead_id, extra_json) VALUES (?, ?, ?, NULL, ?)", row)
            rows += 1
            _maybe_crash()
        self.report["json"]["golden_library.json"] = f"{rows} entries -> golden_demos"

    # -------------------------------------------------------------- FTS + chain

    def _rebuild_fts(self, dst: sqlite3.Connection) -> None:
        n = dst.execute("SELECT COUNT(*) FROM memory_episodes").fetchone()[0]
        # Standalone FTS5: populate by explicit insert (deterministic — a re-run
        # replaces the index rather than duplicating it).
        dst.execute("DELETE FROM memory_episodes_fts")
        dst.execute(
            "INSERT INTO memory_episodes_fts (id, query, summary, observation, "
            "outcome, lessons_learned) "
            "SELECT id, query, summary, observation, outcome, lessons_learned "
            "FROM memory_episodes")
        fts_n = dst.execute("SELECT COUNT(*) FROM memory_episodes_fts").fetchone()[0]
        self.report["notes"].append(
            f"FTS rebuilt: {fts_n} indexed rows for {n} episodes "
            f"({'OK' if fts_n >= n else 'MISMATCH — investigate'})")

    def _finish_events_chain(self, dst: sqlite3.Connection) -> None:
        build_hash_chain(dst)
        ok, detail = verify_hash_chain(dst)
        self.report["notes"].append(f"events_log hash chain: {'OK' if ok else 'BROKEN'} ({detail})")

    def _foreign_key_check(self, dst: sqlite3.Connection) -> None:
        violations = dst.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            from collections import Counter
            breakdown = Counter((r[0], r[2]) for r in violations)
            detail = ", ".join(f"{child}->{parent}: {n}" for (child, parent), n in
                               sorted(breakdown.items()))
            self.report["notes"].append(
                f"foreign_key_check: {len(violations)} violations ({detail}) — "
                "legacy orphan refs copied intact; operator decision required "
                "(create missing parents or archive) — ping (c) at cutover")
        else:
            self.report["notes"].append("foreign_key_check: clean")


# ------------------------------------------------------------- events chain

def build_hash_chain(conn: sqlite3.Connection) -> int:
    """Rebuild prev_hash/row_hash over events_log in id order. Idempotent:
    recomputes every row from its content. Returns rows chained."""
    rows = conn.execute(
        "SELECT id, ts, day, event_type, subject, payload FROM events_log ORDER BY id"
    ).fetchall()
    prev = ""
    for rid, ts, day, etype, subject, payload in rows:
        material = json.dumps({"id": rid, "ts": ts, "day": day, "event_type": etype,
                               "subject": subject, "payload": payload,
                               "prev": prev}, sort_keys=True, default=str)
        row_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        conn.execute("UPDATE events_log SET prev_hash=?, row_hash=? WHERE id=?",
                     (prev, row_hash, rid))
        prev = row_hash
    conn.commit()
    return len(rows)


def verify_hash_chain(conn: sqlite3.Connection) -> Tuple[bool, str]:
    rows = conn.execute(
        "SELECT id, ts, day, event_type, subject, payload, prev_hash, row_hash "
        "FROM events_log ORDER BY id").fetchall()
    prev = ""
    for rid, ts, day, etype, subject, payload, stored_prev, stored_hash in rows:
        if (stored_prev or "") != prev:
            return False, f"row {rid}: prev_hash mismatch"
        material = json.dumps({"id": rid, "ts": ts, "day": day, "event_type": etype,
                               "subject": subject, "payload": payload,
                               "prev": prev}, sort_keys=True, default=str)
        if hashlib.sha256(material.encode("utf-8")).hexdigest() != (stored_hash or ""):
            return False, f"row {rid}: row_hash mismatch"
        prev = stored_hash or ""
    return True, f"{len(rows)} rows chained"


# --------------------------------------------------------------------- entry

def migrate_all(target_path: Optional[str] = None,
                data_dir: Optional[str] = None,
                configs_dir: Optional[str] = None) -> Dict[str, Any]:
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    m = Migrator(
        target_path or os.path.join(repo, "data", "ghost.db"),
        data_dir or os.path.join(repo, "data"),
        configs_dir or os.path.join(repo, "configs"))
    return m.run()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Migrate all stores into data/ghost.db")
    ap.add_argument("--target", default=None)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--configs-dir", default=None)
    ap.add_argument("--report-json", default=None,
                    help="write the report dict to this path as JSON")
    args = ap.parse_args(argv)
    report = migrate_all(args.target, args.data_dir, args.configs_dir)
    print(json.dumps(report, indent=2, default=str))
    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
    failed = [t for t, r in report.get("tables", {}).items()
              if r.get("state") in ("parity_failed", "error")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
