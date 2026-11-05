# MIGRATION_MAP — T1a schema census (target: one store, `data/ghost.db`)

**Date:** 2026-11-06 · **Phase:** T1a of ROADMAP_TOTAL_AUTONOMY.md · **Status:** CENSUS COMPLETE — nothing merged yet
**Method:** code-driven (every env-overridable path + every `json.dump` audited), not `ls data/`-driven. Mechanical facts regenerate via `python3 scripts/store_census.py` → `docs/store_census.generated.md`. **Nothing merges that isn't in this map** (roadmap rule).
**Live volumes (2026-11-06):** small — crm 9 tables (13 clients / 127 interactions / 1 lead / 8 drafts), memory 694 episodes, missions 3/15/190, watchdog 37, audit 1. The *mapping* is the cost, not the copy.

---

## 1. Store inventory — 9 SQLite + 29 JSON (not the 5 DBs the brief assumed)

| # | Store | Default path | Env override | Owner module | On disk | Class | Disposition |
|---|---|---|---|---|---|---|---|
| 1 | audit ledger | data/audit.db | GHOST_AUDIT_DB_PATH | core/audit.py | yes | DB (event log) | **→ ghost.db `events_log` (THE spine)** |
| 2 | CRM | data/crm.db | GHOST_CRM_DB_PATH | crm/db.py | yes | DB | → ghost.db `crm_*` (§2.2) |
| 3 | episodes memory | data/memory.db | GHOST_MEMORY_DB | core/memory.py | yes | DB (FTS5) | → ghost.db `memory_episodes` + rebuilt FTS |
| 4 | missions | data/missions.db | GHOST_MISSIONS_DB | core/mission.py | yes | DB | → ghost.db `mission*` (§2.4) |
| 5 | watchdog events | data/events.db | GHOST_EVENTS_DB | watchdog/service.py | yes | DB | → ghost.db `watchdog_events` |
| 6 | job queue | data/queue.db | GHOST_QUEUE_DB_PATH | core/jobqueue.py | no | DB (code-defined) | → ghost.db `jobs` |
| 7 | retainers | data/retainers.db | GHOST_RETAINER_DB_PATH | workers/retainers.py | no | DB (code-defined) | → ghost.db `retainer_*` |
| 8 | monitor | data/monitor.db | GHOST_MONITOR_DB_PATH | workers/site_monitor.py | no | **DEAD** — path declared, zero opens (verified: no CREATE/CONNECT) | delete declaration in 1b |
| 9 | token stats | configs/token_stats.json | GHOST_TOKEN_STATS_PATH | core/token_tracker.py, core/agent.py, crm/db.py | yes (44 KB) | STATE json (runtime-written ×3 modules) | → ghost.db `token_spend` |
| 10 | conversation history | data/conversation_history.json | — | core/brain.py | yes (21 KB) | STATE json | → ghost.db `conversation_messages` |
| 11 | ladder state | data/ladder_state.json | GHOST_LADDER_STATE_PATH | autonomy/ladder.py | no | STATE json | → ghost.db `ladder_state` (KV rows) |
| 12 | outbound warmup | data/outbound_state.json | GHOST_OUTBOUND_STATE_PATH | autonomy/outbound.py | no | STATE json | → ghost.db `outbound_warmup` |
| 13 | golden library | configs/golden_library.json | GHOST_GOLDEN_LIBRARY_PATH | workers/golden_library.py | no | STATE json | → ghost.db `golden_demos` |
| 14 | user profile | data/user_profile.json | — | core/follow.py (read) | yes | KNOB json (operator identity) | → ghost.db `operator_profile` |
| 15 | session state | configs/session_state.json | — | server/agency_api.py (read) | yes | KNOB json | → ghost.db `agency_session` |
| 16 | secrets vault | configs/secrets.json | GHOST_SECRETS_PATH | core/secrets.py | no | CREDENTIALS (chmod 600) | **stays a file — never enters ghost.db** (§11) |
| 17 | email cfg (creds) | configs/email.json | — | comms/email_service.py (read), brain.py (runtime-writes `_config`) | yes | CREDENTIALS + runtime-written | **superseded by secrets vault in T2, then file deleted** |
| 18 | halt signal | data/HALTED + data/HALT.json | GHOST_HALT_FILE/META | core/halt.py | – | SIGNAL file (atomic rename) | **stays a file by design** (§2 single-writer) |
| 19 | heartbeat | data/heartbeat.json | GHOST_HEARTBEAT_PATH | core/heartbeat.py | – | SIGNAL file | stays a file by design |
| 20 | revocation epoch | data/REVOCATION_EPOCH | — (T3 creates) | core/grants.py (future) | – | SIGNAL file | stays a file by design |
| 21 | drafts fallback | data/drafts.jsonl | — | comms/email_service.py | yes | DEGRADED-write artifact | stays a file; row-level drafts live in `crm_drafts` |
| 22 | digest marker | /tmp/ghost_digest_marker | GHOST_DIGEST_MARKER | autonomy/scheduler.py | – | ephemeral marker | unchanged |
| 23–29 | knobs | policy.json (GHOST_POLICY_CONFIG_PATH), model_routing.json (GHOST_MODEL_ROUTING_PATH), tenants.json (GHOST_TENANTS_PATH), faq_knowledge.json (GHOST_FAQ_KB_PATH), outreach/campaign/deal/directives/flows/factory/payments/vercel/git/omniroute/figma/framer/mcp_servers.json | various | policy engine, router, agency, faq, workers | mixed | KNOB json (operator-edited; no runtime writers found — see §8-1) | **stay files** (operator-owned, versioned in git) |
| — | dead knob | configs/three.json | — | **zero code references** | yes | DEAD | delete in 1b |
| — | artifacts | data/reports/*, reports/screenshots, demos/*, obsidian clients dir, silero model | — | workers | yes | ARTIFACTS | never migrate |

---

## 2. SQLite → ghost.db column-level map

Unified namespace: domain-prefixed table names, one DB (`data/ghost.db`, WAL). Column names preserved **except** where noted; every source column is listed in `docs/store_census.generated.md` (regenerated from live schema) — this section records the *decisions*.

### 2.1 audit.db → `events_log` (the spine)
`audit_events(id INTEGER PK AUTOINCREMENT, ts, day, event_type, subject, payload)`. 1:1 copy. **1b adds:** hash chain (`prev_hash`, `row_hash`) + `origin` column (`origin='main'` until supervisor/sidecars exist — they never write the store, §2 rule). `day` index kept. This table becomes the outbox's evidence backbone; the outbox itself is a NEW table (`outbox`: id, kind, grant_id, idempotency_key, state, created/updated) — no source, created in 1b.

### 2.2 crm.db → `crm_*`
| source | target | notes |
|---|---|---|
| clients | crm_clients | PK `slug` kept |
| interactions | crm_interactions | FK client_slug→crm_clients.slug (declared, kept) |
| campaigns | crm_campaigns | |
| batches | crm_batches | FK campaign_id kept |
| leads | crm_leads | **`id` vs `slug` split — see §6-1** |
| suppression | crm_suppression | |
| drafts | crm_drafts | FK target fix per §6-1 |
| documents | crm_documents | |
| events | crm_lead_events | **gains declared FK lead_id→crm_leads.id (source has none)** |
| processed_events | crm_processed_webhooks | webhook dedupe ledger; keys with outbox idempotency |

### 2.3 memory.db → `memory_episodes`
`episodes(id, task_id, query, summary, observation, outcome, error, lessons_learned, importance, created_at, metadata_json)` — 1:1. **FTS5 `episodes_fts` is REBUILT after copy** (`INSERT INTO episodes_fts(episodes_fts) VALUES('rebuild')`); the 4 shadow tables are never row-copied. `metadata_json`/`lessons_learned` are JSON-in-TEXT → stay JSON-with-schema (queried rarely, structure unstable — documented exception to structuring, revisit on demand).

### 2.4 missions.db → `missions`, `mission_tasks`, `mission_tool_calls`
`tasks` has a **composite PK (id, mission_id)** — preserved. `tool_calls.task_id` has **no declared FK → gains FK to mission_tasks.id** (§6-4).

### 2.5 queue.db (unmaterialized) → `jobs`
Exact DDL from core/jobqueue.py:34: `id INTEGER PK AI, job_id TEXT UNIQUE NOT NULL, idempotency_key TEXT UNIQUE, kind, payload JSON-TEXT, state, attempts, max_attempts, next_run_at, lease_until, lease_owner, last_error, created_at, updated_at` + 2 indexes. `payload` stays JSON-TEXT (heterogeneous per kind).

### 2.6 retainers.db (unmaterialized) → `retainer_sites`, `retainer_incidents`, `retainer_fixes`
DDL from workers/retainers.py:38–80. `incidents.slug` / `fixes.slug` **gain declared FKs → retainer_sites.slug** (source has none — string match today). `fixes.incident_id` gains FK → retainer_incidents.id.

### 2.7 events.db → `watchdog_events`
`system_events(id, signature, event_type, source, summary, payload_json, urgency, occurrence_count, first_seen_at, last_seen_at, occurrences_json, status)` — 1:1; `signature` dedupe semantics preserved.

## 3. JSON state → ghost.db tables

| source | target | shape decision |
|---|---|---|
| token_stats.json (44 KB, ×3 writers) | `token_spend(day, backend/model, intent, tokens, ts)` — row per record | unstructured→structured: today's file is nested per-day counters; 1b parses and flattens; ambiguous keys → quarantine |
| conversation_history.json | `conversation_messages(id INTEGER AI, session, role, content, ts)` | list-of-dicts → rows |
| ladder_state.json | `ladder_state(capability TEXT PK, level INT, streak INT, unlocked_at REAL, ...)` | dict-of-capabilities → rows |
| outbound_state.json | `outbound_warmup(key TEXT PK, value TEXT)` | KV — warmup counters |
| golden_library.json | `golden_demos(slug TEXT PK, registered_at, qa_evidence, ...)` | gains FK `lead_id → crm_leads.id` where resolvable (§6-2) |
| user_profile.json | `operator_profile(k TEXT PK, v TEXT)` | KV |
| session_state.json | `agency_session(k TEXT PK, v TEXT)` | KV |

## 4. Stays-file-by-design (never in ghost.db)
Signal files (atomic-rename cross-process protocol per §2): HALTED, HALT.json, heartbeat.json, REVOCATION_EPOCH (T3). Credentials: secrets.json (vault), email.json until T2 retires it. Artifacts: reports/screenshots/demos/obsidian. Degraded-write fallback: drafts.jsonl.

## 5. Dead stores — delete in 1b (with a commit note, not silently)
`configs/three.json` (zero refs), `MONITOR_DB_PATH` declaration in site_monitor.py (path never opened; checks are stateless, incidents belong to retainers.db).

## 6. Implicit relationships — findings + resolutions (the §1a core)

1. **`drafts.lead_id → leads.id` is a lie in practice.** `add_or_update_lead` mints `lid = lead_data.get("id") or f"lead_{uuid4().hex[:12]}"` (crm/db.py:321) and dedupes by `slug`; the outreach sender writes `add_or_update_draft(lead_id=req.lead_slug ...)` (sender.py:138). Unless a caller passes `id == slug`, every draft row orphans. **Resolution:** target keeps FK `crm_drafts.lead_id → crm_leads.id`; migration rewrites draft rows via `leads.slug → leads.id`; unresolvable rows → quarantine table + ping (c). 1b also fixes the writer to pass the resolved id.
2. **`leads.demo_url` → demos/ + golden library** — string match to `demos/<slug>/` dirs and golden_library slugs. Resolution: `golden_demos` gains `lead_id` FK (resolvable via slug); `demo_url` stays informational TEXT.
3. **`crm.events.lead_id` has NO declared FK** → gains `FK → crm_leads.id` in target.
4. **`tool_calls.task_id` has NO declared FK** (only mission_id) → gains `FK → mission_tasks.id`.
5. **`episodes.task_id` ↔ missions/tasks** — format unverified (episodes predate missions?). 1b: sample-match; resolvable → keep as plain TEXT with a lookup index; unresolvable → documented as free-form (no FK), never fabricated.
6. **`audit_events.subject` is polymorphic** (lead slugs, event ids, draft ids, decision ids) — stays TEXT by design; it is the log, not a relation. Documented convention: `event_type` discriminates the subject kind.
7. **`interactions.client_slug → clients.slug`** — declared AND truthful (verified writer paths) — kept.
8. **`system_events.signature` dedupe** — self-contained; unchanged.

## 7. Migration risks (must be answered by the 1b harness)
R1 FTS5 rebuild ordering (copy → rebuild → verify counts). R2 composite PK copy order (missions before tasks). R3 id/slug rewrite for drafts (§6-1) must be idempotent on re-run. R4 token_stats flatten may hit unexpected key shapes → quarantine, never crash. R5 three modules write token_stats (token_tracker, agent, crm) — concurrent writes today; single-writer rule makes ghost.db the fix, but 1b must serialize during cutover. R6 hash-chain rebuild must be verified against pre-chain row content, then extend only.

## 8. Verification results (live migration run, 2026-11-06 — docs/migration_report_2026-11-06.json)
1. ~~Generic-writer scan for knob JSONs~~ — **still open for 1c** (instrument one day of runtime, then classify).
2. `email.json` runtime-writes — **still open** (T2 retirement order).
3. `episodes.task_id` — **SETTLED: 0/694 match mission ids** (own `task_<epoch_ms>` id space) → free-form TEXT, no FK, as §6-5 anticipated.
4. Draft orphan rate — **SETTLED: 8/8 live drafts orphaned, 0 slug-resolvable** → all 8 quarantined with reasons (`lead-t0` ×4, `test-lead` ×4 — T0-era test drafts). The §6-1 rewrite path is proven by the test suite (slug-resolvable case); live data needed only quarantine.
5. **NEW (found by the harness): 114 FK violations `crm_interactions → crm_clients`** — 4 demo-era slugs (`obsidian-dining-lounge`, `obsidian-reserve`, `apex-detailing`, `grasshopper-concept`) carry interaction history (114 of 127 rows) but have **no client rows**. Copied INTACT (quarantining 90% of history over 4 missing parents would be data destruction); operator decision required at 1c cutover: create the missing client rows or archive the interactions. Surfaced as a ping (c). Live result: **1,082 rows copied, 8 quarantined, 0 parity failures, FTS 694/694, chain OK**.

## 9. Usage matrix (module → store, from code audit)
audit.py→audit.db W; crm/db.py→crm.db R/W + token_stats W; memory.py→memory.db R/W + directives.json R; mission.py→missions.db R/W; watchdog/service.py→events.db R/W + email.json R; jobqueue.py→queue.db R/W; retainers.py→retainers.db R/W; site_monitor.py→(monitor.db dead) stateless; golden_library.py→golden_library.json R/W; ladder.py→ladder_state.json R/W; outbound.py→outbound_state.json R/W; token_tracker.py+agent.py→token_stats.json R/W; brain.py→conversation_history.json R/W + email.json R/W; follow.py→user_profile.json R; agency_api.py→session_state.json R + tenants.json R; faq.py→faq_knowledge.json R; sender.py→crm.db W (drafts) + secrets R; deploy.py→vercel.json R/W; bridge.py→git.json R + mcp_servers.json R.
