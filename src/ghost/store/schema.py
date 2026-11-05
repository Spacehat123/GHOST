"""STORE PLANE — target schema for the unified store `data/ghost.db` (T1b).

One transactional SQLite database (WAL) absorbing the 8 SQLite stores and the
7 state JSON files mapped in docs/MIGRATION_MAP.md. Old stores are NEVER
mutated — they are the rollback. Signal files (halt/heartbeat/epoch) and the
secrets vault stay files by design (§2 single-writer rule).

stdlib only: the migrator must not depend on the modules it migrates.
"""
import os
import sqlite3

# --- events spine ------------------------------------------------------------
EVENTS_LOG = """
CREATE TABLE IF NOT EXISTS events_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    day TEXT NOT NULL,
    event_type TEXT NOT NULL,
    subject TEXT,
    payload TEXT,
    prev_hash TEXT,
    row_hash TEXT,
    origin TEXT NOT NULL DEFAULT 'main'
)
"""
OUTBOX = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    grant_id TEXT,
    idempotency_key TEXT UNIQUE,
    payload TEXT,
    state TEXT NOT NULL DEFAULT 'pending',   -- pending|sent|verified|failed
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""
# --- CRM (source: crm.db) -----------------------------------------------------
CRM = [
    """CREATE TABLE IF NOT EXISTS crm_clients (
        slug TEXT PRIMARY KEY, business_name TEXT NOT NULL, contact_name TEXT,
        email TEXT, phone TEXT, preferred_channel TEXT, status TEXT,
        deal_value REAL, notes TEXT, created_at REAL, updated_at REAL)""",
    """CREATE TABLE IF NOT EXISTS crm_interactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, client_slug TEXT NOT NULL
            REFERENCES crm_clients(slug) ON DELETE CASCADE,
        channel TEXT, direction TEXT, content TEXT, timestamp REAL)""",
    """CREATE TABLE IF NOT EXISTS crm_campaigns (
        id TEXT PRIMARY KEY, name TEXT, city TEXT, created_at REAL,
        harvest_per_batch INTEGER, keep_fraction REAL)""",
    """CREATE TABLE IF NOT EXISTS crm_batches (
        id TEXT PRIMARY KEY, campaign_id TEXT REFERENCES crm_campaigns(id),
        category TEXT, status TEXT, harvested_count INTEGER,
        qualified_count INTEGER, built_count INTEGER)""",
    """CREATE TABLE IF NOT EXISTS crm_leads (
        id TEXT PRIMARY KEY, batch_id TEXT REFERENCES crm_batches(id),
        slug TEXT, name TEXT, address TEXT, phone TEXT, website TEXT,
        rating REAL, review_count INTEGER, maps_cid TEXT, google_category TEXT,
        domain TEXT, entity_type TEXT, score_revenue REAL, score_fit REAL,
        score_friction REAL, score_total REAL, gaps_json TEXT, status TEXT,
        needs_confirmation INTEGER, created_at REAL, demo_url TEXT)""",
    """CREATE TABLE IF NOT EXISTS crm_suppression (
        id TEXT PRIMARY KEY, kind TEXT, value_normalised TEXT, reason TEXT,
        added_at REAL, source TEXT)""",
    # FK target FIXED per MIGRATION_MAP §6-1: writers resolve slug -> id,
    # unresolvable rows are quarantined by the migrator, never inserted.
    """CREATE TABLE IF NOT EXISTS crm_drafts (
        id TEXT PRIMARY KEY, lead_id TEXT NOT NULL
            REFERENCES crm_leads(id) ON DELETE CASCADE,
        channel TEXT NOT NULL, subject TEXT, body TEXT NOT NULL, link TEXT,
        status TEXT NOT NULL DEFAULT 'draft', created_at REAL NOT NULL,
        approved_at REAL, sent_at REAL)""",
    """CREATE TABLE IF NOT EXISTS crm_documents (
        id TEXT PRIMARY KEY, lead_id TEXT NOT NULL
            REFERENCES crm_leads(id) ON DELETE CASCADE,
        kind TEXT NOT NULL, version INTEGER DEFAULT 1, path TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft', created_at REAL NOT NULL)""",
    # FK ADDED per MIGRATION_MAP §6-3 (source events.lead_id had none).
    """CREATE TABLE IF NOT EXISTS crm_lead_events (
        id TEXT PRIMARY KEY, lead_id TEXT REFERENCES crm_leads(id),
        type TEXT, payload_json TEXT, at REAL)""",
    """CREATE TABLE IF NOT EXISTS crm_processed_webhooks (
        event_id TEXT PRIMARY KEY, provider TEXT, processed_at REAL)""",
]
# --- queue (source: queue.db, DDL from core/jobqueue.py) ----------------------
JOBS = [
    """CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL UNIQUE,
        idempotency_key TEXT UNIQUE,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 4,
        next_run_at REAL NOT NULL DEFAULT 0,
        lease_until REAL,
        lease_owner TEXT,
        last_error TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_jobs_ready ON jobs(state, next_run_at)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state)",
]
# --- retainers (source: retainers.db; FKs added per MIGRATION_MAP §2.6) -------
RETAINERS = [
    """CREATE TABLE IF NOT EXISTS retainer_sites (
        slug TEXT PRIMARY KEY, url TEXT NOT NULL,
        plan TEXT NOT NULL DEFAULT 'retainer',
        amount_inr REAL NOT NULL DEFAULT 0,
        billing_state TEXT NOT NULL DEFAULT 'active',
        charge_failures INTEGER NOT NULL DEFAULT 0, grace_until REAL,
        last_charge_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS retainer_incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL REFERENCES retainer_sites(slug),
        kind TEXT NOT NULL, severity TEXT NOT NULL, detail TEXT, evidence TEXT,
        status TEXT NOT NULL DEFAULT 'open', opened_at REAL NOT NULL,
        resolved_at REAL, resolution TEXT)""",
    """CREATE TABLE IF NOT EXISTS retainer_fixes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL REFERENCES retainer_sites(slug),
        incident_id INTEGER NOT NULL REFERENCES retainer_incidents(id),
        tier TEXT NOT NULL, action TEXT NOT NULL, approved_by TEXT,
        result TEXT, at REAL NOT NULL)""",
]
# --- memory (source: memory.db; FTS rebuilt, never row-copied) ----------------
MEMORY = [
    """CREATE TABLE IF NOT EXISTS memory_episodes (
        id TEXT PRIMARY KEY, task_id TEXT, query TEXT, summary TEXT,
        observation TEXT, outcome TEXT, error TEXT, lessons_learned TEXT,
        importance REAL, created_at REAL, metadata_json TEXT)""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS memory_episodes_fts USING fts5(
        id, query, summary, observation, outcome, lessons_learned)""",
]
# --- missions (source: missions.db; composite PK preserved) -------------------
MISSIONS = [
    """CREATE TABLE IF NOT EXISTS missions (
        id TEXT PRIMARY KEY, goal TEXT, created_at TEXT, status TEXT,
        skill TEXT, notes TEXT, total_elapsed REAL, total_tokens INTEGER,
        metadata TEXT)""",
    """CREATE TABLE IF NOT EXISTS mission_tasks (
        id TEXT NOT NULL, mission_id TEXT NOT NULL REFERENCES missions(id),
        idx INTEGER, title TEXT, description TEXT, acceptance TEXT,
        evidence_required TEXT, depends_on TEXT, status TEXT, evidence TEXT,
        result_summary TEXT, attempts INTEGER, budget TEXT, tools_allowed TEXT,
        task_type TEXT, PRIMARY KEY (id, mission_id))""",
    # FK ADDED per MIGRATION_MAP §6-4 (source tool_calls.task_id had none).
    """CREATE TABLE IF NOT EXISTS mission_tool_calls (
        id TEXT PRIMARY KEY, mission_id TEXT REFERENCES missions(id),
        task_id TEXT, tool_name TEXT, command TEXT, exit_code INTEGER,
        output TEXT, created_at TEXT, metadata TEXT)""",
]
# --- watchdog (source: events.db) ---------------------------------------------
WATCHDOG = ["""CREATE TABLE IF NOT EXISTS watchdog_events (
    id TEXT PRIMARY KEY, signature TEXT, event_type TEXT, source TEXT,
    summary TEXT, payload_json TEXT, urgency TEXT, occurrence_count INTEGER,
    first_seen_at REAL, last_seen_at REAL, occurrences_json TEXT,
    status TEXT)"""]
# --- JSON state flattened (MIGRATION_MAP §3) ----------------------------------
STATE = [
    """CREATE TABLE IF NOT EXISTS token_spend (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, model TEXT,
        prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
        model_calls INTEGER, tool_calls INTEGER, cost_usd REAL,
        breakdown_json TEXT, created_at REAL,
        UNIQUE (task_id, created_at))""",
    """CREATE TABLE IF NOT EXISTS conversation_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT,
        timestamp TEXT,
        UNIQUE (role, timestamp, content))""",
    """CREATE TABLE IF NOT EXISTS ladder_state (
        capability TEXT PRIMARY KEY, level INTEGER, streak INTEGER,
        unlocked_at REAL, success_count INTEGER, incident_count INTEGER,
        extra_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS outbound_warmup (
        k TEXT PRIMARY KEY, v TEXT)""",
    """CREATE TABLE IF NOT EXISTS golden_demos (
        slug TEXT PRIMARY KEY, registered_at REAL, qa_evidence_json TEXT,
        lead_id TEXT REFERENCES crm_leads(id), extra_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS operator_profile (
        k TEXT PRIMARY KEY, v TEXT)""",
    """CREATE TABLE IF NOT EXISTS agency_session (
        k TEXT PRIMARY KEY, v TEXT)""",
]
# --- migration machinery -------------------------------------------------------
MIGRATION = [
    """CREATE TABLE IF NOT EXISTS migration_progress (
        source TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'pending',
        rows_copied INTEGER NOT NULL DEFAULT 0,
        source_hash TEXT, target_hash TEXT, checked_at REAL, note TEXT)""",
    """CREATE TABLE IF NOT EXISTS migration_quarantine (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
        reason TEXT NOT NULL, row_json TEXT NOT NULL, at REAL NOT NULL,
        UNIQUE (source, row_json))""",
]

ALL_STATEMENTS = ([EVENTS_LOG, OUTBOX] + CRM + JOBS + RETAINERS + MEMORY
                  + MISSIONS + WATCHDOG + STATE + MIGRATION)


def connect_target(path: str) -> sqlite3.Connection:
    """Open/create the unified store: WAL, 0600, single writer by convention."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    os.chmod(path, 0o600)
    return conn


def create_target(conn: sqlite3.Connection) -> None:
    for stmt in ALL_STATEMENTS:
        conn.execute(stmt)
    conn.commit()
