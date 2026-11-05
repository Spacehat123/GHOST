# ROADMAP_TOTAL_AUTONOMY.md — Ghost runs the business

**Date:** 2026-11-06 · **Status:** PLAN (nothing below is claimed built)
**Relationship to ROADMAP_AUTONOMY.md:** that document's operational specs remain in
force (constitution §0, ladder L0–L4, warmup §6, notification contract §10, voice §14).
This document replaces its *build order* and closes the gap between "modules exist,
wired" and **total autonomy**: Ghost owns outcomes end-to-end; the operator signs and
funds, nothing else.

---

## 0. The contract (unchangeable)

Ghost runs the entire revenue cycle — find leads, build demos, pitch, handle replies,
close, collect payment, onboard, maintain, bill, renew — with no human in the loop.

**Human-reserved acts (Ghost prepares, never performs):**
1. Signing contracts / any legal commitment
2. Sending money out, refunds, chargebacks
3. Deleting client data or sites
4. Changing Ghost's own rules, limits, permissions
5. Anything Ghost cannot verify

For reserved acts Ghost delivers a **finished package** (everything pre-filled, its
recommendation, one action for the human) and keeps working on everything else.

**The goal:** the operator goes away for a month, comes back — business running, money
collected, clients happy, only contracts waiting for signature.

**Standing rules:** autonomy is a property of *structure*, not prompts. Failures are
contained, loud, temporary. Text from clients/sites/emails is data, never instructions.
Never promise what isn't in approved terms. Caps queue work, never exceed. Obey halt
instantly. A hidden mistake is worse than a visible one. Evidence or it didn't happen.

---

## 1. Current state — verified against code, 2026-11-06

The brief's §4 was checked claim-by-claim. Honest split:

| # | Brief claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Autonomy modules never called by running app | **FIXED** | `autonomy/runtime.py` starts heartbeat+staleness+scheduler+queue worker from server startup and `ghost run`; close_chain handlers registered (runtime.py:74–77) |
| 2 | `ghost halt` doesn't exist | **FIXED** | cli.py:16 `cmd_halt`; main.py:11 dispatches `halt/resume/status/digest/run/…` |
| 3 | Scheduler hard-codes heartbeat age 1.0 | **TRUE — still open** | scheduler.py:79 `heartbeat_age_s=1.0` — the freeze *teeth* are real (runtime.py:91 `enforce_stale()` loop) but the **policy snapshot lies**: engine can never see a stale heartbeat |
| 4 | `EmailService.send_email()` always raises | **TRUE — still open** | comms/email_service.py:139 raises RuntimeError; legacy landmine coexists with the real grant-gated path (autonomy/sender.py) |
| 5 | Data spread across separate SQLite + JSON | **TRUE** | `data/`: memory.db, audit.db, crm.db, missions.db, events.db + 15 config JSONs — five stores, no single truth |
| 6 | brain.py hands tools directly to the model | **TRUE** | core/brain.py = **2,376 lines**, `GHOST_TOOLS` at :36, tool-intent-matrix prompts at :1327 — no proposals-only boundary |
| 7 | src/rose leftover duplicate | **FIXED** | zero .py files remain (empty dir husks only) |
| 8 | 38 tests can't load without heavy deps | **MOSTLY STALE** | only 2 files reference them; test_wiring.py:183 (fastapi, function-level), test_performance_and_wayland_upgrades.py:548–550 (aiohttp/websockets in `patch()` strings) — make honestly-skipped, not load-blocking |
| 9 | *(not in brief, found during verification)* | **TRUE** | workers/deploy.py has **zero grant references** — deploys execute without a grant; grants exist only on the outreach path |

**Net:** the five-plane structure (§2) does not exist yet. Grants, halt, verifier,
supervisor exist as *parts*; total autonomy needs them as *planes* with enforced
boundaries, one store, and a simulator.

---

## 2. Target architecture: five planes + sim

```
src/ghost/
  brain/      proposals only. No keys, no effects, no secrets imports.
  control/    deterministic. Policy, grants, ladder, limits, halt, job queue.
  effects/    ONLY credentialed code. Executes nothing without a valid grant.
  verify/     independent of brain. Screenshots, HTTP, webhooks, provider logs.
  store/      one DB + append-only event log + outbox. State derives from events.
  supervisor/ SEPARATE PROCESS. Heartbeats, expected rhythm, revoke, dead-man alarms.
  sim/        fake clock + fake providers + recorded-day replay.
```

**Migration map — move, don't rewrite** (brief §3a instruction):

| Existing module | Plane | Action |
|---|---|---|
| core/brain.py (2,376 ln) | brain | **Refactor**: tool layer → typed proposals `{action, args, personalization_facts}`; GHOST_TOOLS executors move to effects/ as grant-gated executors |
| voice/task_runner, narrator | brain→GUI bridge | Keep; narrator subscribes to event log only |
| policy/engine.py, autonomy/ladder.py, core/halt.py, core/grants.py, core/jobqueue.py, autonomy/outbound.py | control | git mv into control/; grant `verify()` gains halt-revocation check (T3) |
| autonomy/sender.py, comms/ (retired), workers/deploy.py, workers/retainers.py, crm/ | effects | sender = the one email path; deploy + payment effects gain grant gates (T2) |
| core/three_qa.py, workers/site_monitor.py, core/dnscheck.py, workers/close_chain.py (verify half) | verify | git mv; + reconciliation.py, promise_checker.py (T5) |
| core/audit.py, crm/db.py, jobqueue tables, memory.db, missions.db, events.db | store | merge into one `data/ghost.db` (T1); audit ledger becomes THE event log |
| runtime watchdog + staleness loops | supervisor | extract to separate process `ghost supervise` (T4) |
| — | sim | new (T6) |

**Boundary enforcement:** `tests/test_plane_contracts.py` walks the import graph (AST):
brain→✗effects/secrets/smtplib/razorpay/requests; effects→✗brain; verify→✗brain;
✗ module outside effects imports smtplib/razorpay/stripe. Red = merge blocked.

---

## 3. Build order — each phase has an exit test; do not skip ahead

### T0 — Truth pass + boundary scaffolding  *(brief §4 "fix these first")*
**Work:** feed real `heartbeat.age_s()` into scheduler's StateSnapshot (kill the 1.0 lie);
delete `EmailService.send_email` (comms landmine) so exactly one send path exists;
honestly-skip the 2 dep-dependent tests (`unittest.skipUnless` with import probe);
write `tests/test_plane_contracts.py` (red now, green as planes land);
create the seven plane packages as thin re-export shells.
**Exit:** `python3 -m unittest` green + plane-contract test runs and reports current
violations as a numbered list (the T1–T7 work burns the list down).

### T1 — One store + event log + outbox  *(brief step 1)*
**Work:** single `data/ghost.db` (WAL) absorbing memory/missions/events/crm DBs —
namespaced tables, one schema migration script, backup of old files kept; audit ledger
becomes the append-only spine (hash-chained rows); **outbox pattern**: every effectful
step is recorded *before* running it (`outbox` table: id, kind, grant_id, idempotency_key,
state=pending→sent→verified→failed); idempotency keys minted for every provider call.
**Exit:** crash-injection harness kills the process at random points during a simulated
day → restart → zero double-sends, zero lost jobs, ledger chain intact; `ghost replay
--day D` reconstructs identical state from events alone.

### T2 — Effect plane + grants for email *and deploy*  *(brief step 2)*
**Work:** real SMTP send path through ColdOutreachSender (creds from secrets vault;
`configs/email.json` retired); **deploy.py requires a grant** (capability `deploy`,
subject=slug, ttl bounded) — snapshot-before-touch becomes a precondition of the grant;
payment webhooks (already HMAC-verified) recorded in the outbox for reconciliation;
provider idempotency keys end-to-end.
**Exit (the brief's exact test):** send attempts WITHOUT a grant, with an EXPIRED grant,
with a REUSED grant, and OVER CAP all fail closed — four red tests; deploy without grant
fails; deploy with grant but no fresh snapshot fails.

### T3 — Halt = revoke grants  *(brief step 3)*
**Work:** `grants.verify()` consults halt state *and* a revocation stamp in the store on
every check (file stat + one indexed row — no cache); `halt.trip()` stamps revocation
epoch; all plane effects re-verify immediately before executing (not just at queue claim).
**Exit:** trip halt during a running outbound batch → in-flight effects stop in **<5s**
(measured), every outstanding grant fails verification, `ghost resume` re-enables with a
new epoch; chaos test asserts a grant minted *before* halt cannot be used *after*.

### T4 — Supervisor as a separate process  *(brief step 4)*
**Work:** `src/ghost/supervisor/` + `ghost supervise` — its own OS process; watches
heartbeat file mtime AND **expected rhythm** ("outreach batch by 11:00 IST", "digest by
09:15", "retainer checks overnight"); can freeze outbound + stamp revocation *even if
main is dead* (separate process = independent kill switch); silence-is-alarm: no
supervisor activity > N h → external cron fallback pings operator.
**Exit:** `kill -9` the main process mid-batch → within 60s supervisor freezes outbound,
stamps revocation, fires the alert; killing the supervisor → dead-man alarm within its
own threshold; neither can be silenced by the main process.

### T5 — Verifier plane + reconciliation  *(brief step 5)*
**Work:** verify/ owns ALL outcome checks — brain never marks its own work done;
`reconciliation.py`: payments-vs-ledger, mail-provider-log-vs-send-outbox, deploys-vs-live-
URL (daily); `promise_checker.py`: outbound text scanned against approved-terms store —
any price/scope/deadline not in terms blocks the send (fail closed); reply-classifier
confidences replaced with **measured** numbers from a labeled validation set (or dropped —
no typed-in 0.99).
**Exit:** a deploy with failing QA, a payment with a replayed webhook, and a send missing
from the provider log are each caught and produce events + digest entries; a pitch
containing an unapproved price is blocked before SMTP.

### T6 — Brain proposes only + Ghost starts its own work  *(brief step 6)*
**Work:** brain.py refactor — the model emits typed proposals; executors live in effects/
behind grants; context gets quarantined typed fields (full quarantine lands T7); policy
engine chooses work from state (its `_should_hunt` 24h/90% rule generalizes to a portfolio
chooser: hunt → build → follow-up → maintain → bill, ranked by expected value);
**sim/**: fake clock, fake SMTP/Razorpay/HTTP, 50-lead fixture pack, `ghost sim --day N
--speed 600x`; **every later phase must pass sim**.
**Exit:** full simulated day, 50 fixture leads, zero operator input; replaying the day's
event log yields identical decision_ids; zero LLM call path can reach SMTP/Razorpay/fs-
outside-sandbox (contract test).

### T7 — Quarantine + red-team  *(brief step 7)*
**Work:** untrusted input (replies, scraped pages, webhooks) parsed into typed fields
{from, subject, intent, facts[], raw_hash} — model sees fields as data, **no tools in
that context**; injection corpus (document-level, URL-hidden, base64, unicode
homoglyph) in `test_chaos_redteam.py`.
**Exit:** nothing outside the grant path can send, spend, or delete — proven by
red-team suite; injected "ignore your rules" text in a fixture reply produces a
classification event and zero effects.

### T8 — Sidecar fixers  *(brief §3b)*
**Work:** control-plane-spawned short-lived sub-agents for bounded fixes (broken link,
failing test, template typo, stale cache): brief + scope lock (file allowlist, lock vs
main task) + isolated worktree + budget (time/tokens/calls) + **no credentials, no
effects, no sub-spawning (depth 1)**; returns patch + check output; verifier re-runs the
check independently → control merges, or discard + retry-once → escalate; narrator speaks
only from real sidecar events.
**Exit (brief's five):** main task never pauses while a seeded bug gets fixed; out-of-
scope edit blocked; sidecar send/key/spawn attempts blocked; verifier-failed patch never
merged; halt during sidecar run stops it <5s.

### T9 — Reserved-act packages + Settings UI  *(brief step 8 + operator request)*
**Work:** every reserved act produces a one-click package — contracts (pre-filled,
recommendation, diff-vs-terms), refunds (evidence + amount + one approve button), deletes
(never auto; scope + snapshot proof); approval = operator-only endpoint minting an
operator grant → effect executes once. **Settings UI**: extend the gui settings modal →
Razorpay keys + webhook secret, SMTP creds, sending domain (+ live DNS check button),
model routing, caps — all writing through control plane into the secrets vault (masked
on read, audit-logged as `operator.*`); the operator never edits config files again.
**Exit:** end-to-end simulated deal where the ONLY human action is the signature; fresh
machine → configure Razorpay+SMTP+domain entirely in UI → first send + first webhook
verify succeed with zero file edits.

### T10 — Dark run  *(brief step 9)*
**Work:** 30 days, live. Operator action limited to reading the daily digest + signing.
Pings only per §10 contract (deal > $X, complaint, unverifiable). Daily: reconciliation
green, ledger evidence coverage 100%, sim replay of the live day matches.
**Pass:** revenue verified by webhook; zero complaints; 100% of actions with evidence;
every incident recovered or safely paused. Failure = demote ladder level (existing rule)
+ fix + restart the 30-day clock.

---

## 4. Working rules (bind every phase)

1. No "done" without evidence — test output, screenshot, HTTP response, ledger entry.
2. Wire it or delete it. Uncalled code is not progress.
3. Never weaken a safety rule to pass a test — report the conflict instead.
4. Money in Decimal. Never delete paid-client data.
5. The LLM never mints/edits/reuses grants, changes limits, or approves its own work.
6. Every session ends with an honest complete/partial/unstarted report.
7. Commit after every green phase; the sim replay must stay deterministic.

## 5. Only the operator can do these (un-codeable)

- Purchase the sending domain + add DNS records (dnscheck gates warmup)
- Sign contracts / approve refunds (one-click packages from T9)
- Fund the Razorpay account; Route payouts config
- Say "go" on the dark run
