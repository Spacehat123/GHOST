# GHOST DIRECTORY MAP & INVENTORY
**Audit Task:** GHOST TASK 001 — Repository Audit & Cleanup Plan  
**Status:** READ-ONLY EVIDENCE-BASED DIRECTORY MAP  
**Generated:** 2026-11-10T14:30:00+05:30  
**Repository Path:** `/home/pranc/projects/ghost`  

---

## 1. Top-Level Directory Overview

```
/home/pranc/projects/ghost/
├── assets/                 # Brand iconography & vector logos
├── Compo's/                # Staged standalone React/TSX UI components (trailing newlines)
├── configs/                # JSON/YAML policy, directives, token stats & provider configs
├── context/                # User profile, Obsidian sync, capability skills, app tools
├── data/                   # Unified SQLite database, audit logs, CRM state, snapshots
├── demos/                  # Client demo build artifacts & local staging folders
├── docs/                   # Authoritative manuals, migration maps & proposal schemas
├── dossiers/               # Client qualification and venue research dossiers
├── graphify-out/           # Ast-derived knowledge graph database & visualization
├── gui/                    # Single-window web HUD frontend (HTML/CSS/JS)
├── node_modules/           # Node.js dependencies (chrome-remote-interface)
├── projects/               # Empty directory
├── scripts/                # Launchers, store census, and Kokoro GPU diagnostic scripts
├── .skill-vendor/          # Vendored third-party design and SVG skill repositories
├── src/                    # Source code root (src/ghost, src/rose, src/data)
├── temp/                   # Loose temporary files, binaries, zip archives, scratch notes
├── templates/              # Section Kit, motion, legal, and SVG code-generation templates
└── tests/                  # 67 automated test suites & golden traces
```

---

## 2. Source Code Architecture (`src/ghost/`)

The primary codebase resides in [`src/ghost/`](file:///home/pranc/projects/ghost/src/ghost/). It contains 22 specialized subpackages:

### 2.1 Core Orchestration & Authority
- **[`src/ghost/authority/`](file:///home/pranc/projects/ghost/src/ghost/authority/)**:
  - `protocol.py`: Cryptographic HMAC token generation, single-use grant issuance, grant verification, and process isolation.
  - `service.py`: Unix socket daemon (`grantd`) for key isolation between workers and the signing authority.
- **[`src/ghost/core/`](file:///home/pranc/projects/ghost/src/ghost/core/)**:
  - `agent.py`: `GhostAgent` interactive conversation loop, circuit breaker, tool budget limiter, barge-in detection.
  - `brain.py`: `OmniRouteBrain` LLM gateway, system prompt assembly, tool schema registry, tool execution dispatcher.
  - `audit.py`: `AuditLedger` append-only hash-chained SQLite event audit logging.
  - `halt.py`: Kill-switch (`assert_not_halted`, `trip`, `resume`, `HALTED` file checks).
  - `heartbeat.py`: System pulse writer (`data/heartbeat.json`) and staleness watchdog.
  - `jobqueue.py`: Durable transactional job queue in SQLite with exponential backoff and worker loops.
  - `memory.py`: 3-layer memory (Working Context, Episodic SQLite, Core Facts with contradiction resolution).
  - `model_routing.py`: Model routing tiers (`fast`, `coding`, `deep_reasoning`, `cloud_vision`).
  - `mission.py`: Multi-step goal decomposition, milestone tracking, and task state machine.
  - `reply_classifier.py`: Zero-shot/few-shot email reply intent classification.
  - `secrets.py`: Secure credential loader with environment variable fallback.
  - `skills.py`: Skill capability injection and directive compiler.
  - `token_tracker.py`: Real-time token usage accounting and budget tracking (`configs/token_stats.json`).
  - `rhythm.py`: Autonomous rhythm tracking and operation cadence.
  - `revocation.py`: Epoch-based grant revocation.
  - `follow.py`: Directives management (`DirectiveManager`).

### 2.2 Autonomy & Control Plane
- **[`src/ghost/autonomy/`](file:///home/pranc/projects/ghost/src/ghost/autonomy/)**:
  - `runtime.py`: Headless background daemon orchestrating 7 worker loops (heartbeat, staleness, scheduler, intake, ladder, microjobs, queue-worker).
  - `scheduler.py`: Cron/rhythm scheduler executing operations on designated cycles.
  - `intake.py`: IMAP SSL read-only email poller and draft generator.
  - `reply_loop.py`: Inbound customer reply processor.
  - `ladder.py` & `ladder_evidence.py`: Evidence-based promotion ladder (L0 to L4 trust levels).
  - `outbound.py`: Pure outbound pipeline asserting halt, policy, and grants.
  - `sender.py`: Gated email and WhatsApp outbound dispatcher.
  - `digest.py`: Daily markdown summary compiler.
- **[`src/ghost/control/`](file:///home/pranc/projects/ghost/src/ghost/control/)**:
  - `fixer_worker.py` & `fixers.py`: Diagnoses and stages site fixes for customer-reported issues.
  - `quarantine.py`: T7 input sanitizer (untrusted lead and email text isolation).
  - `reserved.py`: Human-reserved action enforcement (payouts, deletions, refunds).
  - `sidecars.py`: Isolated fix execution runners.
  - `darkrun.py`: 30-day autonomous dark-run test harness.
- **[`src/ghost/policy/`](file:///home/pranc/projects/ghost/src/ghost/policy/)**:
  - `engine.py`: Evaluates declarative business rules (`configs/policy.json`) before outbound actions.

### 2.3 Storage & Verification Plane
- **[`src/ghost/store/`](file:///home/pranc/projects/ghost/src/ghost/store/)**:
  - `facade.py`: Unified database router (redirects calls to `data/ghost.db`).
  - `schema.py`: Unified SQLite DDL schema definitions.
  - `migrator.py`: T1b legacy store migrator copying 8 separate databases into `ghost.db`.
  - `parity.py`: T1c verification ensuring no legacy events went missing during migration.
  - `replay.py`: Historical event replayer reconstructing daily state.
  - `chain.py`: Cryptographic hash chain verification for audit integrity.
- **[`src/ghost/crm/`](file:///home/pranc/projects/ghost/src/ghost/crm/)**:
  - `db.py`: `CRMDatabase` managing lead progression, contact details, deal stages, and Obsidian Vault sync.
- **[`src/ghost/verify/`](file:///home/pranc/projects/ghost/src/ghost/verify/)**:
  - `reconciliation.py`: T5 daily reconciliation verifying external reality against local records.
  - `promise_checker.py`: Validates proposals against delivered commitments.
- **[`src/ghost/supervisor/`](file:///home/pranc/projects/ghost/src/ghost/supervisor/)**:
  - `service.py`: Independent watchdog process supervising kill-switch and heartbeat staleness.
- **[`src/ghost/watchdog/`](file:///home/pranc/projects/ghost/src/ghost/watchdog/)**:
  - `service.py`: Autonomous health monitor, crash recovery, and desktop notifications.

### 2.4 Client Acquisition & Production Workers
- **[`src/ghost/workers/`](file:///home/pranc/projects/ghost/src/ghost/workers/)**:
  - `demo_state_machine.py`: 5-stage luxury demo generator with visual self-healing and vocabulary linting.
  - `section_kit.py`: Component composition engine enforcing strict photo provenance and typography doctrine.
  - `export_demo_to_production.py`: Compiles approved static demos into full Next.js 14 App Router codebases.
  - `recon_scout.py`: Automated Google Maps venue and photo scraper via Brave CDP.
  - `deploy.py`: Fail-closed Vercel deployment engine guarded by the visual screenshot QA gate.
  - `lead_hunter.py`, `lead_qualifier.py`, `lead_sources.py`: Regional lead discovery and enrichment.
  - `deal_closer.py`: Proposals, contracts, invoices, and closing packs.
  - `outreach_drafter.py`: Personalized pitch generation authored in the operator's voice.
  - `retainers.py`: Monthly maintenance retainer agreements.
  - `seo_pack.py`: Local SEO checklist and performance audit generator.
  - `batch_demo_worker.py`: Batch parallel demo generation pipeline.
  - `microjobs.py`: Idle-time proposal drafter for freelance boards.
  - `source_ledger.py`: Real-world photo sha256 provenance tracking.
  - `snapshots.py`: Demo versioning and pre-deploy snapshots.
  - `suppression.py`: Lead opt-out and suppression list manager.
  - `payments/`: Razorpay payment link integration (`razorpay_provider.py`).

### 2.5 Screen, Voice & Server Interface
- **[`src/ghost/server/`](file:///home/pranc/projects/ghost/src/ghost/server/)**:
  - `app.py`: FastAPI application serving REST endpoints, WebSocket streams, and GUI static files.
  - `agency_api.py`: Comprehensive agency API for leads, proposals, closing, settings, and health.
- **[`src/ghost/voice/`](file:///home/pranc/projects/ghost/src/ghost/voice/)**:
  - `kokoro.py`: Kokoro ONNX neural TTS engine (CUDA GPU with CPU INT8 fallback).
  - `vad.py`: Voice Activity Detection (Silero ONNX with energy-based fallback).
  - `duplex.py`: Full-duplex audio stream coordinator with barge-in interruption.
- **[`src/ghost/screen/`](file:///home/pranc/projects/ghost/src/ghost/screen/)**:
  - `client.py` & `overlay.py`: Hyprland / Wayland window inspection, cursor control, and visual overlay.
- **[`src/ghost/mcp/`](file:///home/pranc/projects/ghost/src/ghost/mcp/)**:
  - `bridge.py`: Model Context Protocol bridge for Brave CDP automation and web search.

---

## 3. Dead, Misplaced, & Auxiliary Source Directories

### 3.1 [`src/rose/`](file:///home/pranc/projects/ghost/src/rose/) (DEAD ARTIFACT)
- **Contents:** 7 empty directories (`core/`, `mcp/`, `server/`, `voice/`, and empty `__pycache__` folders). Zero files.
- **Origin:** Leftover skeleton from the early "Rose" assistant refactoring into "Ghost".
- **References:** Zero imports or references across `src/`, `tests/`, or `configs/`.
- **Status:** 100% safe to remove.

### 3.2 [`src/data/`](file:///home/pranc/projects/ghost/src/data/) (MISPLACED RUNTIME STATE)
- **Contents:** `src/data/rhythm/scheduler_cycle.json`.
- **Origin:** Runtime scheduler state was accidentally written to `src/data/` instead of `data/rhythm/`.
- **Status:** Requires relocation to `data/` and removal from `src/`.

---

## 4. Frontend & User Interface (`gui/`)

- **[`gui/index.html`](file:///home/pranc/projects/ghost/gui/index.html)**: 25.7 KB single-page dashboard:
  - Header: System status badges, kill-switch button, current time, voice orb.
  - Main Layout: 5 tab views (Pipeline CRM, Live Demos, Proposals/Closing, Audit Ledger, Settings/Config).
  - Floating Voice HUD: Interactive voice waveform orb (`orb.js`).
- **[`gui/static/js/`](file:///home/pranc/projects/ghost/gui/static/js/)**:
  - `app.js`: Client-side logic for API requests, lead status transitions, and WebSocket event streams.
  - `orb.js`: Canvas-based audio reactive orb visualization.
  - `audio.js`: Audio worklet and PCM audio streaming over WebSocket.
- **[`gui/static/css/`](file:///home/pranc/projects/ghost/gui/static/css/)**:
  - `style.css`: Minimalist luxury dark theme with glassmorphism tokens.

---

## 5. Configurations (`configs/`)

| File | Classification | Purpose |
|---|---|---|
| `secrets.json` | **Secret Vault (0600)** | API keys (Vercel, Razorpay, OpenCode) & `GHOST_GRANT_SECRET` |
| `policy.json` | Core Policy | Outbound email, hunt, and spend bounds |
| `directives.json` | Core Memory | Persistent user instructions, preferences, and verified facts |
| `token_stats.json` | Runtime Mutable | Cumulative token usage accounting across models |
| `session_state.json`| Runtime Mutable | GUI active tab and window persistence |
| `omniroute.json` | Active Config | Local LLM proxy endpoint configuration |
| `email.json` | Active Config | IMAP/SMTP server connection details |
| `microjobs.json` | Active Config | RSS feed and search queries for freelance intake |
| `rules.py` | Active Python | Python-based validation rules |
| `*.example` files | Template Configs | Sample templates for git, figma, framer, vercel, omniroute |

---

## 6. Persistence & Runtime Data (`data/`)

- **Unified Database:** [`data/ghost.db`](file:///home/pranc/projects/ghost/data/ghost.db) (2.5 MB SQLite database holding jobs, audit events, leads, memory, and proposals).
- **Legacy Databases:** `audit.db`, `crm.db`, `events.db`, `memory.db`, `missions.db`, `queue.db`, `microjobs.db`, `grant_authority.db`.
- **Runtime State:** `heartbeat.json`, `intake_seen.json`, `ladder_cursor.json`, `REVOCATION_EPOCH`.
- **Active Sockets:** `grantd.sock` (Unix domain socket).
- **Archives & Snapshots:**
  - `data/archive/`: 52 legacy HTML generation folders (erroneously tracked in Git).
  - `data/snapshots/`: Pre-deploy backup folders with large JPEG/PNG assets (erroneously tracked in Git).

---

## 7. Client Demos (`demos/`)

Contains 13 folders:
- **Production / Active:** `grasshopper` (live Vercel deployment), `salt-stg`.
- **Photo Doctrine Test Cases:** `halfpoto` (partial photos), `nopoto` (zero photos), `askpoto` (interactive request).
- **Test Pipeline Artifacts:** `kit-sm-11234b`, `kit-sm-62ba17`, `kit-sm-651075`, `kit-sm-654707`, `kit-sm-b88609`, `kit-sm-bd8d36` (generated during previous test executions; safe for cleanup).

---

## 8. Scratch, Temporary, & Unconventional Folders

### 8.1 [`temp/`](file:///home/pranc/projects/ghost/temp/) (2.4 MB GARBAGE BIN)
Contains loose files from historical sessions:
- `WhatsApp Image 2026-11-04 at 4.08.*.jpeg` (6 images, ~1.2 MB)
- `a.zip` (1.18 MB binary archive)
- `84c6e18427517d0becf88f88ecd984c9.avif` (43 KB)
- Scratch markdown notes (`07_3D_SITE_PLAN.md`, `08_GHOST_THINKING_AND_TASK_SPLITTING.md`, `09_FREE_ASSET_STACK.md`, `10 flow router and motion site.md`, `skill_cinematic_3d_site.md`).

### 8.2 [`Compo's/`](file:///home/pranc/projects/ghost/Compo's/) (UNCONVENTIONAL NAMING)
Contains standalone React components:
- Problem: Directory name contains an apostrophe (`'`) and 8 subdirectories contain **trailing newline characters `\n`** in their folder names (e.g. `'AI thinking text loader'$'\n'`).
- Status: Staged component library; requires renaming to `components/` and sanitizing newline characters.

---

## 9. Code Generation Templates (`templates/`)

- **[`templates/sections/`](file:///home/pranc/projects/ghost/templates/sections/)**: 13 verified Section Kit components (`nav_floating`, `hero_editorial`, `gap_banner`, `story_philosophy`, `services_bento`, `proof_reviews`, `gallery_strip`, `journey_steps`, `booking_module`, `faq_answers`, `hours_location`, `lead_capture`, `footer_mono`).
- **`templates/contract/`**: Commercial retainer and service agreement Markdown templates.
- **`templates/invoice/`**: Invoice templates with Razorpay payment link placeholders.
- **`templates/outreach/`**: Cold outreach pitch templates.
- **`templates/welcome/`**: Client onboarding welcome templates.
- **`templates/svg/`**: Vector ornamental SVG definitions.
- **`templates/three_site/`** & **`templates/motion_site/`**: Legacy WebGL/motion site templates.
