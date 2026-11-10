# GHOST REPOSITORY AUDIT REPORT
**Audit Task:** GHOST TASK 001 — Repository Audit & Cleanup Plan  
**Status:** READ-ONLY EVIDENCE-BASED AUDIT  
**Audit Timestamp:** 2026-11-10T14:28:00+05:30  
**Repository Working Directory:** `/home/pranc/projects/ghost`  
**Git Branch:** `main` (commit `ceedccd`)  
**Python Virtualenv:** `/home/pranc/.venvs/ghost-gpu` (Python 3.14.0rc3 / Linux x86_64)  
**Test Suite Baseline:** 773 tests discovered — 771 PASSED, 2 FAILED (160.26s)  

---

## 1. Executive Summary

This comprehensive audit of the Ghost codebase was conducted in strict adherence to the **GHOST Engineering Contract** and the **Read-Only Directive**. Zero project files were modified, deleted, moved, or reformatted during this inspection.

Ghost has evolved from an ambient full-duplex desktop voice HUD (the early "Rose / Jarvis" lineage) into a production-hardened, 5-plane autonomous local-business agency engine (the "Operator's 11" architecture). The repository possesses exceptional test discipline (773 unit/integration tests running in under 3 minutes) and rock-solid fail-closed authority gates. 

However, rapid phased growth has left noticeable architectural sediment:
1. **Packaging & Dependency Gaps:** Essential third-party packages (`openai`, `sympy`, `pillow`) are imported across core subsystems but are completely missing from [`requirements.txt`](file:///home/pranc/projects/ghost/requirements.txt). The root [`pyproject.toml`](file:///home/pranc/projects/ghost/pyproject.toml) refers to a non-existent `README.md` (which breaks `pip install -e .`) and demands an unused dependency (`fury-sdk`).
2. **Repository & Git Tracking Hygiene:** Active runtime snapshots (containing binary `.jpg`/`.png` screenshots) and historical archive folders in [`data/`](file:///home/pranc/projects/ghost/data/) are actively tracked in Git. This causes continuous dirty-tree git status churn (`data/heartbeat.json`, `data/rhythm/scheduler_cycle.json`). An accidental runtime data directory exists inside the source tree at [`src/data/`](file:///home/pranc/projects/ghost/src/data/).
3. **Abandoned & Artifact Directories:** An empty skeleton directory tree [`src/rose/`](file:///home/pranc/projects/ghost/src/rose/) exists with zero files. The [`temp/`](file:///home/pranc/projects/ghost/temp/) directory holds 2.4 MB of loose binaries (`WhatsApp Image...`, `a.zip`, `.avif`). An unconventionally named directory [`Compo's/`](file:///home/pranc/projects/ghost/Compo's/) contains trailing newline characters in folder names.
4. **Documentation Divergence:** 12 top-level markdown files exist in the root directory. Early documents (`PLAN.md`, `agents.md`, `ROADMAP_AUTONOMY.md`, `RESEARCH_JARVIS_ARCHITECTURE.md`) describe superseded 4-agent paradigms or outdated hosting assumptions that contradict the authoritative [`chief.md`](file:///home/pranc/projects/ghost/chief.md) and [`docs/OPERATOR_MANUAL.md`](file:///home/pranc/projects/ghost/docs/OPERATOR_MANUAL.md).

---

## 2. Phase 1 — Repository Inventory Summary

### 2.1 Codebase Scale & Distribution
- **Total Tracked Files:** 940+ files
- **Total Python Code:** ~38,070 lines across 85 modules in `src/ghost/`
- **Total Test Code:** ~25,400 lines across 67 test modules in `tests/`
- **Total Core Templates:** 13 Section Kit components, 1 Motion Site kit, 1 Three.js kit, legal templates
- **Total Databases:** 1 Unified Database (`data/ghost.db`, 2.5 MB), 8 legacy store databases (`data/*.db`)

### 2.2 Top-Level Directory Breakdown
| Directory | Nature | Files / Size | Active Responsibility |
|---|---|---|---|
| [`src/ghost/`](file:///home/pranc/projects/ghost/src/ghost/) | Production Code | 85 Python files (38k LOC) | Core 5-plane autonomous agency engine |
| [`src/rose/`](file:///home/pranc/projects/ghost/src/rose/) | Dead Artifact | 7 empty dirs, 0 files | Abandoned "Rose" skeleton; zero active references |
| [`src/data/`](file:///home/pranc/projects/ghost/src/data/) | Accidental Runtime | 1 JSON file | Misplaced runtime rhythm state (`scheduler_cycle.json`) |
| [`gui/`](file:///home/pranc/projects/ghost/gui/) | Frontend Dashboard | HTML, CSS, JS (25k HTML) | Operator HUD, chat stream, notifications, CRM GUI |
| [`configs/`](file:///home/pranc/projects/ghost/configs/) | Configuration | 31 files (JSON, YAML, py) | Policy, directives, token stats, provider configs |
| [`data/`](file:///home/pranc/projects/ghost/data/) | Persistence & State | 7.2 MB (SQLite, JSON, logs) | Unified `ghost.db`, audit logs, CRM, job queue |
| [`demos/`](file:///home/pranc/projects/ghost/demos/) | Client Demo Builds | 13 business folders | Local previews (`grasshopper`, `salt-stg`, etc.) |
| [`dossiers/`](file:///home/pranc/projects/ghost/dossiers/) | Recon Research | 1 folder (`halfpoto`) | Lead qualification dossiers |
| [`temp/`](file:///home/pranc/projects/ghost/temp/) | Scratch / Garbage | 14 files (2.4 MB) | WhatsApp dumps, `a.zip`, scratch notes |
| [`Compo's/`](file:///home/pranc/projects/ghost/Compo's/) | Staged Components | 15+ TSX / folders | Unconventional folder names with trailing newlines |
| [`.skill-vendor/`](file:///home/pranc/projects/ghost/.skill-vendor/) | Vendored Upstream | 11 repos | Vendored skills (`tastemaker`, `svg.js`, etc.) |
| [`templates/`](file:///home/pranc/projects/ghost/templates/) | Code Generation | 13 sections, legal docs | Section Kit HTML/MD, contract, invoice templates |
| [`scripts/`](file:///home/pranc/projects/ghost/scripts/) | Operations & Ops | 3 scripts (bash, python) | Launchers, store census, GPU diagnostic tools |
| [`tests/`](file:///home/pranc/projects/ghost/tests/) | Verification | 67 test suites | 773 automated unit & integration tests |
| [`docs/`](file:///home/pranc/projects/ghost/docs/) | Documentation | 6 markdown/JSON files | Operator manual, migration maps, census reports |
| `graphify-out/` | Knowledge Graph | 12 MB (`graph.json`, html) | AST knowledge graph and architecture reports |

---

## 3. Phase 2 — Architecture Reconstruction Summary

Ghost operates across five enforced structural planes under the unchangeable constitution: **"Evidence or it didn't happen; the LLM proposes, deterministic code disposes; the operator signs and funds, nothing else."**

```
┌────────────────────────────────────────────────────────────────────────┐
│                        1. BRAIN PLANE (Proposals)                      │
│   OmniRouteBrain (OpenAI Client) ──> GhostAgent (Multi-turn Loop)      │
│   System Prompt (Directives Snapshot) ──> Proposals (Risk Classes 1-5) │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Proposes Actions
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       2. CONTROL PLANE (Authority)                     │
│   PolicyEngine (policy.json) ──> Assert Not Halted (HALTED file)       │
│   Quarantine T7 (Sanitize Inputs) ──> Grant Authority (HMAC Tokens)    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Issues Single-Use Effect Grants
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      3. EFFECTS PLANE (Capabilities)                   │
│   AutonomousDemoStateMachine ──> SectionKit ──> BraveCDPBridge         │
│   VercelDeployer (QA Gate) ──> EmailService (SMTP) ──> Razorpay Collect│
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Emits Evidence
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                    4. VERIFY PLANE (Reconciliation)                    │
│   Visual QA Gate (CDP / Offline DOM) ──> Claims Linter ──> Auditor     │
│   Reconciliation Engine (T5) ──> Autonomy Ladder (L0 to L4 Promotion)  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Logs Cryptographic Hash Chain
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       5. STORE PLANE (Persistence)                     │
│   Facade API ──> ghost.db (Unified WAL SQLite) ──> Audit Ledger        │
│   CRM Database ──> JobQueue (Lease/Backoff) ──> CoreMemoryStore        │
└────────────────────────────────────────────────────────────────────────┘
```

### Key Execution Lifecycles:
1. **Startup:** `ghost.server.app:app` boots via FastAPI; mounts `gui/static/` and initializes `AutonomyRuntime` background tasks (heartbeat, scheduler, intake, ladder, microjobs, queue-worker).
2. **Inbound Ingress:** External emails poll IMAP read-only; web lead forms post to `/api/leads/{slug}/contact` or serverless stubs `/api/contact`. All messages pass through **T7 Quarantine** before hitting CRM.
3. **Execution & Tool Isolation:** `GhostAgent` enforces strict turn caps (8 model rounds, 15 tool executions max, 4k character output truncation). Outbound side-effects (deploying to Vercel, sending emails) require single-use cryptographic HMAC grants minted by `authority/protocol.py`.
4. **Kill-Switch:** Touching `data/HALTED` or bumping `data/REVOCATION_EPOCH` instantly aborts all pending grants and pauses workers at safe transactional boundaries.

---

## 4. Phase 3 — Documentation Audit

| Document | Status | Verdict | Findings & Action |
|---|---|---|---|
| [`chief.md`](file:///home/pranc/projects/ghost/chief.md) | Active | **Authoritative** | Single source of operational truth for Ghost director role. Preserve. |
| [`docs/OPERATOR_MANUAL.md`](file:///home/pranc/projects/ghost/docs/OPERATOR_MANUAL.md) | Active | **Authoritative** | Full registry of tools, rules, skills, and constitutions. Preserve. |
| [`ROADMAP_TOTAL_AUTONOMY.md`](file:///home/pranc/projects/ghost/ROADMAP_TOTAL_AUTONOMY.md) | Active | **Authoritative** | T0–T10 build log and Operator's 11 phase map. Preserve. |
| [`docs/PROPOSAL_TYPES.md`](file:///home/pranc/projects/ghost/docs/PROPOSAL_TYPES.md) | Active | **Authoritative** | Schema for 5 proposal risk classes. Preserve. |
| [`docs/MIGRATION_MAP.md`](file:///home/pranc/projects/ghost/docs/MIGRATION_MAP.md) | Complete | Reference | T1b store migration documentation. Retain as reference. |
| [`ROADMAP_AUTONOMY.md`](file:///home/pranc/projects/ghost/ROADMAP_AUTONOMY.md) | Outdated | Superseded | Superseded by `ROADMAP_TOTAL_AUTONOMY.md`. Propose move to `docs/archive/`. |
| [`PLAN.md`](file:///home/pranc/projects/ghost/PLAN.md) | Historical | Superseded | Nov 1 day-one draft. Propose move to `docs/archive/`. |
| [`RESEARCH_JARVIS_ARCHITECTURE.md`](file:///home/pranc/projects/ghost/RESEARCH_JARVIS_ARCHITECTURE.md) | Historical | Superseded | Early desktop assistant research. Propose move to `docs/archive/`. |
| [`agents.md`](file:///home/pranc/projects/ghost/agents.md) | Historical | Superseded | Deprecated 4-agent specification. Propose move to `docs/archive/`. |
| [`WEBSITE_BUILD_GUIDE.md`](file:///home/pranc/projects/ghost/WEBSITE_BUILD_GUIDE.md) | Outdated | Superseded | Hand-crafted HTML guide superseded by `section_kit.py`. Propose archive. |
| [`LOCAL_SITE_BUILD_BRIEF.md`](file:///home/pranc/projects/ghost/LOCAL_SITE_BUILD_BRIEF.md) | Stale | Superseded | Early manual site prompt guidelines. Propose archive. |
| [`KOKORO_GPU_FIX_PLAN.md`](file:///home/pranc/projects/ghost/KOKORO_GPU_FIX_PLAN.md) | Complete | Historical | Completed Kokoro TTS GPU fix notes. Propose archive. |
| [`fix.md`](file:///home/pranc/projects/ghost/fix.md) | Stale | Scratch | Temporary 1.6KB notes from Nov 4. Propose cleanup. |
| [`flow.md`](file:///home/pranc/projects/ghost/flow.md) | Active | Useful | Outbound flow diagrams and lead states. Keep or consolidate in `docs/`. |

---

## 5. Phase 4 — Dependency Audit

### 5.1 Critical Missing Dependencies from `requirements.txt`
The following packages are actively imported in `src/ghost/` but are **omitted** from [`requirements.txt`](file:///home/pranc/projects/ghost/requirements.txt):
1. **`openai`** (installed: `3.45.0`): Imported in [`src/ghost/core/brain.py:10`](file:///home/pranc/projects/ghost/src/ghost/core/brain.py#L10) (`from openai import AsyncOpenAI`). Without this, the entire LLM brain crashes on fresh install.
2. **`sympy`** (installed: `1.14.0`): Imported in [`src/ghost/math/engine.py`](file:///home/pranc/projects/ghost/src/ghost/math/engine.py), [`src/ghost/math/parser.py`](file:///home/pranc/projects/ghost/src/ghost/math/parser.py), and [`src/ghost/math/verifier.py`](file:///home/pranc/projects/ghost/src/ghost/math/verifier.py).
3. **`pillow` / `PIL`** (installed: `13.0.0`): Imported in [`src/ghost/workers/three_qa.py:27`](file:///home/pranc/projects/ghost/src/ghost/workers/three_qa.py#L27).

### 5.2 Broken Packaging in `pyproject.toml`
1. **Missing Root `README.md`:** [`pyproject.toml:9`](file:///home/pranc/projects/ghost/pyproject.toml#L9) specifies `readme = "README.md"`. No `README.md` exists in the repository root. Running `pip install -e .` fails immediately with `FileNotFoundError: [Errno 2] No such file or directory: 'README.md'`.
2. **Unused Ghost Dependency:** [`pyproject.toml:12`](file:///home/pranc/projects/ghost/pyproject.toml#L12) mandates `"fury-sdk>=0.3.2"`. `fury-sdk` is not imported anywhere in the source tree (only mentioned in an informal docstring in `src/ghost/voice/task_runner.py`).
3. **Outdated Package Metadata:** Description still reads `"Ghost: Full-Duplex Voice & Ambient Desktop HUD Assistant inspired by Huw Prosser's Rose / Jarvis"`.

---

## 6. Phase 5 — Code Health Audit

### 6.1 Oversized Modules Requiring Future Decomposition
Modules exceeding 1,000 lines:
- [`src/ghost/core/brain.py`](file:///home/pranc/projects/ghost/src/ghost/core/brain.py): **2,609 lines** (contains system prompts, 14 tool definitions, tool dispatcher, model routing).
- [`src/ghost/workers/demo_state_machine.py`](file:///home/pranc/projects/ghost/src/ghost/workers/demo_state_machine.py): **2,287 lines** (handles recon, brief, sectional compose, DOM QA, visual self-healing).
- [`src/ghost/mcp/bridge.py`](file:///home/pranc/projects/ghost/src/ghost/mcp/bridge.py): **1,727 lines** (CDP automation, tab management, web search).
- [`src/ghost/server/agency_api.py`](file:///home/pranc/projects/ghost/src/ghost/server/agency_api.py): **1,483 lines** (monolithic endpoint router for all CRM/proposal operations).
- [`src/ghost/core/agent.py`](file:///home/pranc/projects/ghost/src/ghost/core/agent.py): **1,247 lines** (interactive conversation engine and tool executor).
- [`src/ghost/crm/db.py`](file:///home/pranc/projects/ghost/src/ghost/crm/db.py): **1,091 lines** (SQLite schema, queries, Obsidian vault Markdown sync).

### 6.2 Resource Leak & Deprecation Warnings
During test suite execution, Python 3.14 surfaced:
- **40+ Unclosed Database Warnings:** `ResourceWarning: unclosed database in <sqlite3.Connection>` throughout `src/ghost/store/schema.py` and test fixtures. Connections must be wrapped in `contextlib.closing(conn)` or explicit `.close()`.
- **15+ Unclosed File Warnings:** `ResourceWarning: unclosed file <_io.TextIOWrapper>` from direct `json.load(open(path))` calls in `src/ghost/server/agency_api.py:541` and `tests/test_wiring5_voice.py`.
- **Deprecated FastAPI Lifespan Events:** `on_event("startup")` and `on_event("shutdown")` in [`src/ghost/server/app.py:69,93`](file:///home/pranc/projects/ghost/src/ghost/server/app.py#L69-L93) trigger `DeprecationWarning: on_event is deprecated, use lifespan event handlers instead`.
- **Deprecated UTC Datetime:** `datetime.datetime.utcnow()` in `tests/test_wiring3_sources.py:102` triggers `DeprecationWarning`.

---

## 7. Phase 6 — Data & Security Audit

### 7.1 Secrets & Sensitive Configuration Protection
- **Zero Leaked Secrets:** In accordance with the GHOST Engineering Contract, secret values were never printed or exposed during this audit.
- [`configs/secrets.json`](file:///home/pranc/projects/ghost/configs/secrets.json): Correctly set to restrictive permissions `0600` (`-rw-------`). Contains API keys and `GHOST_GRANT_SECRET`.
- `.gitignore` properly excludes `configs/secrets.json`, `configs/*.secret.json`, `configs/token_stats.json`, `configs/session_state.json`.

### 7.2 Git Tracking Defects in `data/`
While `.gitignore` excludes `data/*.db`, Git is actively tracking historical and runtime files in `data/`:
1. **52 Archive Dirs:** [`data/archive/t9-site-*/index.html`](file:///home/pranc/projects/ghost/data/archive/) are tracked in Git.
2. **Snapshot Binaries:** [`data/snapshots/grasshopper/`](file:///home/pranc/projects/ghost/data/snapshots/grasshopper/) contains multiple 1600x1200 JPEG photos (`photo-01.jpg` to `photo-08.jpg`) and PNG screenshots tracked in Git.
3. **Volatile Runtime JSON Files:** [`data/heartbeat.json`](file:///home/pranc/projects/ghost/data/heartbeat.json), [`data/rhythm/scheduler_cycle.json`](file:///home/pranc/projects/ghost/data/rhythm/scheduler_cycle.json), and [`data/intake_seen.json`](file:///home/pranc/projects/ghost/data/intake_seen.json) are tracked, causing constant unstaged git modifications.

---

## 8. Phase 7 — Testing Baseline Summary

- **Total Tests Discovered:** 773 tests
- **Passed:** 771 tests (99.74%)
- **Failed:** 2 tests (0.26%)
- **Total Execution Time:** 160.255 seconds (2.67 minutes)
- **Execution Command:** `PYTHONPATH=src /home/pranc/.venvs/ghost-gpu/bin/python -m unittest discover -s tests`

### 8.1 Analysis of the 2 Failures
Both failures reside in [`tests/test_agent_loop.py`](file:///home/pranc/projects/ghost/tests/test_agent_loop.py):
1. `test_bounded_model_rounds_ceiling`:
   - *Error:* `AssertionError: 5 != 4`
   - *Root Cause:* The mock client expected exactly 4 completion calls when hitting the model rounds ceiling, but recent circuit-breaker enhancements perform 1 initial call + 4 retry turns = 5 calls.
2. `test_bounded_tool_executions_ceiling`:
   - *Error:* `AssertionError: 'Remaining work' not found in ''`
   - *Root Cause:* In interactive chat mode, when tool executions hit the ceiling, Ghost emitted a blank spoken string instead of the synthesised "Remaining work" summary string expected by the test fixture.

*Note: Per the Read-Only Directive, zero test code or production code was modified to force tests to pass.*

---

## 9. Next Steps
Review the accompanying audit documents for implementation details:
- Detailed directory layout: [`DIRECTORY_MAP.md`](file:///home/pranc/projects/ghost/DIRECTORY_MAP.md)
- Runtime execution flow: [`ARCHITECTURE_CURRENT.md`](file:///home/pranc/projects/ghost/ARCHITECTURE_CURRENT.md)
- Proposed cleanup operations: [`CLEANUP_PROPOSAL.md`](file:///home/pranc/projects/ghost/CLEANUP_PROPOSAL.md)
- Complete testing report: [`TEST_BASELINE.md`](file:///home/pranc/projects/ghost/TEST_BASELINE.md)
