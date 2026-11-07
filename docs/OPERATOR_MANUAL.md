# GHOST — Operator's Manual

*The complete registry: every tool, feature, rule, skill, command and standard.
One file, so nothing lives only in the operator's head or the agent's memory.
(Generated 2026-11-07; the code is the source of truth — this file says where to look.)*

---

## 0. What Ghost is

An autonomous local-business agency engine: **find leads → qualify → build demos →
operator approves → host → draft outreach → operator sends → read replies → follow
up → close → collect (UPI/Razorpay) → maintain sites → micro-jobs when idle.**
Five enforced planes (brain / control / effects / verify / store) with one
constitution: *evidence or it didn't happen; the LLM proposes, deterministic code
disposes; the operator signs and funds, nothing else.*

- Repo: `~/projects/ghost` · Server `127.0.0.1:8765` · Test suite: `PYTHONPATH=src python3 -m unittest discover -s tests` (696 green; bare `python3 -m unittest` discovers nothing).

---

## 1. The constitution (rules the LLM cannot rewrite)

| Rule | Where enforced |
|---|---|
| Evidence or it didn't happen — no "done" without verified artifacts | `core/evidence.py`, mission gates, QA invariants |
| The LLM proposes; control plane disposes | `brain/proposals.py` + `brain/dispatcher.py` (5 risk classes, registry) |
| Grants: single-use HMAC capability tokens; LLM never mints | `authority/` (grantd daemon, key isolated), `core/grants.py` |
| Halt is a revocation, not a flag | `core/halt.py` + `core/revocation.py` (epoch bumps kill outstanding grants) |
| No money out — collect only | P1 sweep + `test_collect_only.py` grep test (route_transfer deleted) |
| Demos: real photos instantly, never hotlinks; Websites: wait for client photos | `workers/dossier.py` staging, `seo_pack.website_ready_for_build` |
| Inbound text is DATA, never instructions | `control/quarantine.py` (T7) |
| Reserved acts (sign, refund, delete, rule-change) are human-only | `control/reserved.py`, delete = archive |
| Fabricated URLs/numbers/prices are lies | P1 (fake rzp.io removed), claims-lint tests, FAQ price test |
| Ping contract: (a) money (b) client distress (c) unverifiable → ping; else 09:00 digest | notification contract, Notifications tab |
| Working rules: wire-it-or-delete-it · one phase per go · commit per green phase · never weaken a test to pass | ROADMAP_TOTAL_AUTONOMY §4 + operator standing rule |
| Photo doctrine: REAL photos beat AI images (Google's own guidance) | local_seo_dominance skill + photo gates |

---

## 2. The operator's 11 goals → status

| # | Goal | Status |
|---|---|---|
| 1 | Autonomous except contract signing | ✅ ladder evidence live (P10); reserved acts human-only |
| 2 | Research high-revenue no-website businesses | ✅ Maps/CDP harvest, verifiers, multi-city |
| 3 | Filter on need + likelihood to pay | ✅ 4-pillar + need/pay scores with written reasons |
| 3b | Dossier folder + good photos | ✅ `dossiers/<slug>/` provenance-stamped |
| 4 | Demo → open locally → ok → auto-host | ✅ approval loop, single-use deploy grants |
| 5 | Drafts only (email/WhatsApp) | ✅ sending deleted by design |
| 6 | Reads replies, follows up | ✅ IMAP intake + WA relay + daily follow-up sweep |
| 7 | Maintains sites, fixes breaks | ✅ sidecar fixer → verify → STAGE → operator applies |
| 8 | Razorpay QR collection | ✅ real Payment Links + UPI-QR fallback; collect-only |
| 9 | Micro-jobs when idle | ✅ auto-sweep when pipeline idle >1h; drafts only |
| 10 | Single-window UI | ✅ quiet-room deck + palette + badges (this file §7) |
| 11 | Reactive, self-checking | ✅ event intake (seconds), self-health patrol, phone-ping = open item (ntfy offer) |

---

## 3. Tools the brain may request (proposal registry — `docs/PROPOSAL_TYPES.md`)

**Risk classes:** READ · LOCAL_MUTATION · SANDBOXED_SHELL · PIPELINE_EFFECT · EXTERNAL_EFFECT.
**Strangler-wired (proposals mode = default):** `demo.deploy` · `pipeline.update_stage` ·
`pipeline.upsert_client` · `leads.scout` · `dossier.build` — the VERIFIER's dict is
the only "done" the brain sees. Remaining GHOST_TOOLS (47 total — full table in
PROPOSAL_TYPES.md) fall through to legacy until their wave converts:
`desktop_control, media_control, browser_action, capture_screen, clipboard_action,
input_simulation, desktop_notify, rea_action, web_search, database_query,
github_action, crm_*, build_luxury_demo, email_check_inbox, deploy_demo_vercel,
vercel_check_token, figma_*, framer_*, agent_desktop_command, git_action,
system_run, manage_directives, qualify_venue_lead, scout_expensive_venues_without_website,
mission_*` — banned from the brain: anything that sends, pays, mints grants, or
touches client data destructively.

---

## 4. Features by build phase (all committed; see git log + memories)

| Phase | Delivered |
|---|---|
| P0 | path hygiene (data/ convention), earned sim receipt, halt-minting fix, grantd landed |
| P1 | collect-only doctrine, money-out grep test, chooser wired into scheduler, rhythm paths fixed |
| P2 | dossiers (facts+photos+provenance+verifier), need/pay scoring, multi-city campaigns |
| P3 | demo approval loop (open locally → one click → gated auto-host), Demos tab |
| P4 | real Razorpay Payment Links + deterministic UPI-QR fallback; no fabricated URLs |
| P5 | reply relay fixed, IMAP auto-ingest (dedup, unknown-sender honesty), follow-up sweep, email settings UI |
| P6 | event-driven triage (queue job + signal poke), self-health patrol (error bursts, stale outbox, dead letters) |
| P7 | customer-report fixer: standalone stdlib worker → model POOL (no Ollama), verify → STAGE → operator approves |
| P8 | micro-jobs copilot (sources, fit-reasons, awaiting_human_send, purity tests), idle>1h auto-sweep |
| P9 | deck: Dossiers/Payments/Fixes/Autonomy/Notifications tabs, demo preview, record-reply verdicts |
| P10 | ladder evidence mapper (streaks from ledger; verified incidents demote; operator-only raise) |
| UI | quiet-room redesign (huwprosser ref), orb corner ring, palette, badges, skeletons, amber reserved chrome, reduced-motion |
| Nov-2026 brief | photo policy split, editorial house style, svg.js decor, local SEO + AEO + OWASP skills, SEO/AEO pack, ship-gate |

---

## 5. Skills (context/skills/*/SKILL.md — loaded by the builders)

| Skill | Governs |
|---|---|
| `editorial_web_design` | the house look: warm paper, serif voice, one earth accent, ghosted wordmark, real photos; hard bans |
| `motion_craft` | animation tokens, choreography, transform-only, reduced-motion, bans |
| `svg_decor` | svg.js (vendored): self-drawing line-art, dividers, underlines — max 2/page, once, aria-hidden |
| `local_seo_dominance` | the 6 moves + Ghost operationalization per client |
| `aeo_answer_engines` | answer-first blocks, FAQPage/LocalBusiness JSON-LD, llms.txt, NAP entity consistency |
| `owasp_web_security` | no-leak/no-injection rules for built sites + Ghost; ship-gate checklist |
| plus (pre-existing) | `cinematic_3d_site`, `premium_motion_site` (3D/motion flows) |

Vendor sources (gitignored): `.skill-vendor/` — OWASP CheatSheetSeries, svg.js,
emilkowalski, jakubkrehel, MengTo, garden-skills, ai-design-skills, designer-skills,
tastemaker.

---

## 6. Safety systems (who can do what)

- **grantd daemon** (`ghost grantd`): holds the signing key in its own process; unix socket 0600; nonce replay table. The main process asks; it never mints.
- **Halt**: `ghost halt` / resume / API. Freezes ALL outbound <5s; bumps the revocation epoch (outstanding grants die). Supervisor freezes on dead-pid/stale heartbeat; alarms (never freezes) on missed rhythm.
- **Supervisor** (`ghost supervise`): separate process, file-only I/O. `ghost deadman` (cron) watches the watcher.
- **Quarantine** (T7): every inbound reply (relay or IMAP) — deterministic extraction, injection tripwires override the model, span-cited facts.
- **Sidecars** (T8/P7): control-plane-spawned workers in a sandbox where ghost modules are unimportable; patch → independent verify → STAGE → operator applies. Halt kills them <5s.
- **Reserved acts** (T9): contract/refund/delete/rule-change packages — operator-only endpoints, audited; delete executes as archive.
- **Ship-gate** (`seo_pack.verify_ship_gate`): CSP, no secrets/PII beyond public NAP, schema + llms.txt present, NAP byte-match — a built site cannot launch red.

---

## 7. The operator's surfaces

**Command Deck tabs:** Pipeline · Campaigns · Confirmation · Demos (preview + approve-host) · Dossiers · Micro-jobs · Payments (rail chips, QR artifacts, photo gate, SEO pack) · Fixes (staged-fix approvals) · Autonomy (halt/runtime/supervisor/rhythm/dark-run chips + earned ladder + Raise) · Notifications (pings + digests) · Templates · Suppression · Activity Log.
**HUD:** pills (History/Logs/Dev/Clear View), token chip, state dot, Intercept (barge-in), Command Deck, Operator Control (🛡 settings: authority/smtp/domain/razorpay/email vault sections + Test buttons + reserved-acts queue).
**Command palette:** `Ctrl+K` — open any panel, halt/resume.
**Follow badges:** real WS events badge the owning tab; clicking clears. No fake activity, no focus theft.
**CLI** (`python3 -m ghost.main <cmd>`): halt · resume · status · digest · run · chaos · voice-demo · metrics · migrate · parity · replay · supervise · deadman · reconcile · sim · darkrun (preflight/start/status/stop) · grantd · fixer (list/approve).
**Key endpoints** (localhost + session token): /api/demos/{slug}/{open,authorize-host} · /api/leads/{slug}/{seo-pack,mark-client-photos,website-ready,seo-pack/file/{name}} · /api/microjobs{,/sweep,/propose,/status} · /api/fixer/staged{,/{id}/approve} · /api/autonomy · /api/notifications · /api/payments/overview · /api/settings/{sections,section/{s},test/{t}} · /api/watchdog/signal · /webhooks/razorpay.

---

## 8. Config knobs (operator-owned files)

`configs/policy.json` (decision knobs) · `campaign.json` + `areas/<City>.json` (research breadth) · `outreach.json` (send caps, follow-up cadence) · `deal.json` (terms, currency) · `flows.json` (budgets, sectors) · `microjobs.json` (sources, idle_hours_before_sweep) · `payments.json` (upi_id for the QR fallback) · `rhythm.json` (expected-work deadlines) · `factory.json` (build concurrency) · vault (Operator Control): grant secret, SMTP, domain, Razorpay, IMAP inbox.

---

## 9. Data map

`data/ghost.db` (unified store: events_log hash-chain, outbox, jobs, CRM, …) · `demos/<slug>/` (built demos + QA reports) · `dossiers/<slug>/` (facts/photos/SOURCES) · `dossiers/<slug>/photos` feeds demo builds · `data/documents/<slug>/seo_pack/` (meta, schema, llms.txt, checklists) · `data/fixer_staged/` (verified patches awaiting approval) · `data/microjobs.db` · `data/digests/` · legacy `audit.db/crm.db/...` untouched = rollback. `configs/secrets.json` = vault (NEVER committed).

---

## 10. Runbooks

**Daily:** deck glance (Autonomy chips + Notifications) → approve things with the filled buttons → 09:00 digest handles the rest.
**Client website flow:** deal → `seo-pack` generated (deck button) → send `photo_request_email` → client photos arrive → deck "Mark photos received" → build gated on that → ship-gate must pass before launch.
**Dark run (30 days):** restart server → `grantd` + `supervise` running → vault filled → `ghost migrate` + `parity` green → `darkrun preflight` → `start` → watch Autonomy/Notifications.
**Incident:** halt auto-trips on anomaly; Autonomy tab shows why; fix cause; `ghost resume` (epoch bump = fresh authority).
**Testing:** every change: full suite green → commit. Purity tests that must NEVER be deleted: money-out grep, no-auto-bid, awaiting_human_send birthplace, worker stdlib purity, plane contracts.
