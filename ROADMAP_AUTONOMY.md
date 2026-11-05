# GHOST: Full Autonomy Roadmap — "The Deal Engine"
**Written:** 2026-11-05 · **Status:** Planning
**North star:** Ghost finds paying work, does it, bills it, maintains it — and pings you only for (a) deals over $X, (b) client complaints, (c) anything it cannot verify. Everything else it just does, with evidence.

---

## 0. Non-negotiables (the constitution)

These rules outrank every goal below. The agent may not rewrite them; only you can.

1. **Evidence over assertion.** Every autonomous action ends with a verifiable artifact (screenshot, HTTP response, payment ID, email header). No `passed: true` without evidence. *(Already enforced in three_qa.py — extend repo-wide.)*
2. **Bounded blast radius.** Per-day hard caps on: emails sent, money moved, deploys, new leads contacted. Enforced by a deterministic policy engine, not by the LLM's judgment. When a cap is hit, Ghost queues, never exceeds.
3. **Reversibility budget.** Every action class gets a worst-case cost. Sending 50 emails: reversible-ish (apology + suppression). Charging a card: not reversible — requires higher autonomy tier. Deploying over a live client site: reversible only via snapshot rollback — so snapshots are mandatory before touch.
4. **One strike on trust.** A client reply classified as annoyed/confused → that lead goes to permanent suppression + human review of the sequence. Spam is a business-death event, not a metric.
5. **The operator can always pull the throat-cut handle.** One command (`ghost halt`) stops all outbound channels in <5s, mid-flight. Tested weekly.
6. **Honesty about gray zones.** Upwork auto-bidding violates ToS. We do it human-in-loop or not at all (§8). Nobody gets to "optimize" this rule away.

---

## 1. The Decision Engine — Ghost's executive brain

Today Ghost executes tasks. The jump to Jarvis is a **policy engine + scheduler** that decides *what's worth doing* from telemetry, deterministically. The LLM proposes; the policy engine disposes.

### 1.1 Architecture
- **`ghost/policy/engine.py`** — pure-Python, no LLM, fully unit-tested. Inputs: state snapshot. Outputs: ordered intent list + budget grants. Deterministic, replayable, auditable.
- **State snapshot (from CRM + token_tracker + ledger):**
  - active projects, pipeline value, last closed deal (age), cash collected MTD
  - quota/tokens/cost remaining (the "90% left" input), cost burn rate
  - domain reputation health, suppression list size, reply rates 7d/30d
  - retainer fleet status (n sites up, incidents open), churn risk flags
  - wall-clock: business hours for lead's timezone, your sleep hours (Ghost doesn't call at 3am)
- **Intents (priority order):**
  1. `HOLD_FIRE` — complaint open, domain reputation degraded, or halt flag → outbound frozen
  2. `SERVICE_EXISTING` — retainer incident > SLO, or client reply waiting → always outranks hunting
  3. `CLOSE_PIPELINE` — hot lead awaiting demo/quote/contract → finish the deal first (cheapest revenue)
  4. `GROW` — quota > threshold AND pipeline < target AND no deal in 24h → `FIND_LEADS` (your exact scenario: "no projects in 24h, 90% limit left, let's make money")
  5. `MAINTAIN` — idle capacity → improve golden demos, refresh recon cache, self-audit
- **Example rule (encodes your scenario):**
  ```python
  if (now - last_closed_deal_at) > 24h
     and budget.tokens_remaining_pct > 0.9
     and pipeline_value < PIPELINE_TARGET
     and domain_health == "green"
     and outbound_cap_remaining(today) > 0:
      grant(FIND_LEADS, max_new_contacts=cap, max_build_cost=₹N)
  ```

### 1.2 Autonomy ladder (how Ghost earns unattended execution)
Every capability is a card with a level. Levels only move up on evidence, and drop on incidents (auto-demote).

| Level | Meaning | Example |
|---|---|---|
| L0 Simulate | Dry-run, log only | New outreach sequence |
| L1 Shadow | Do it, don't send; you see diff | Email draft |
| L2 Sampled | Real, you review 1-in-N | N=5 demos, then N=20 |
| L3 Bounded-real | Real within caps, evidence logged | Demo build + deploy |
| L4 Autonomous | No human in loop, exception-only | Retainer auto-fix |

**Promotion rule:** 50 consecutive evidence-backed successes at level N with zero complaints → eligible for N+1. One verified incident → drop one level. This is mechanical, not vibes — the LLM can't talk its way up.

Initial target levels: lead-hunting L3, demo build/deploy L3 (QA gate mandatory), email outreach L2→L3 after 100 sends with <2% negative, contracts L2 (you e-sign), payments L2 (link generation autonomous, capture verified against webhook), client calls L1 (Ghost listens, drafts, you speak) until voice is proven.

---

## 2. The Money Pipeline (end-to-end)

Current skeleton exists (`lead_hunter` → `recon_scout` → `lead_qualifier` → `three_site_builder` → `three_qa` → `deploy` → `outreach_drafter` → `deal_closer` → `payments` → templates: contract/invoice/welcome). Here is each stage made real.

### Stage 1 — Find expensive businesses without websites (L3)
- **Sources (ranked by signal quality):**
  1. Google Maps API / Places scrape by category+city: `website` field absent or Facebook-only → the exact "no website" filter
  2. Instagram business accounts with 10k+ followers + link-in-bio missing → high-ticket, digitally invisible
  3. New business registrations (India: MCA filings; UK: Companies House) — freshest, least contacted
  4. Directories with stale/dead links (HTTP 404 check) — "has website, website broken" is an even better pitch
- **Qualification (extend `lead_qualifier`):** estimated ticket (menu prices, review sentiment "expensive/upscale", hotel star rating) ≥ threshold; contactable (email or WhatsApp business); not in suppression; not already served by competitor pitch (search the business name + "website concept" — avoid double-pitching).
- **Dedup & memory:** CRM fingerprint (name + address + phone hash). Contacted-once-ever rule per business (see §6).
- **Cost guard:** recon burns tokens. Cache aggressively; classify cheap leads with local model (§5).

### Stage 2 — Build the demo (L3, minutes not hours)
- **Golden demo library:** 5–8 flawless per-vertical templates (restaurant, clinic, detailing, boutique, real estate) + curated GLB set — vendored, QA-verified once, then personalized per lead (copy, palette, photos from their Maps listing — licensed via listing embed, never scraped hotlinked).
- **Per-lead personalization budget:** one LLM pass for copy, one for outreach email. Hard cap.
- **QA gate:** the new screenshot QA is **mandatory before deploy** — chain it: `deploy.py` refuses without `qa.json.passed == true && screenshots_captured > 0`.
- **Deploy:** Vercel per-lead preview URLs (exists). Add: auto-expire demos at 14 days unless replied (cost control + scarcity, honest).

### Stage 3 — Outreach without spam (L2→L3) — see §6 for the full deliverability system

### Stage 4 — Reply handling & deal motion (L2)
- **Reply classifier** (extend `flow_router`): INTERESTED / QUESTION / OBJECTION / NOT_NOW / ANGRY / AUTO_REPLY / BOUNCE.
  - INTERESTED → Ghost answers from a vertical FAQ knowledge base, sends demo link, offers call slots.
  - QUESTION it can't answer with ≥95% confidence → `PING_OPERATOR` (category (c): can't verify).
  - ANGRY/CONFUSED → freeze sequence, suppress, ping you (category (b)).
- **Call-needed detection:** explicit ask ("can we talk"), negotiation beyond script (price pushback >15%), or 3+ exchanges without close → `PING: call recommended` + one-paragraph brief on the client (who they are, what they want, likely objections) so the call takes you 5 minutes of prep: zero.
- **Voice later (L1):** Ghost joins the call to take notes/transcribe, you talk. Full Ghost-calls happen only after the Jarvis voice stack (duplex, interruption, VAD pre-roll) passes 50 real calls with no talk-overs.

### Stage 5 — Close (L2)
- **Pricing ladder encoded:** demo concept (₹15–40k) → full site (₹60–150k) → retainer (₹5–15k/mo, §7). Ghost never discounts >10% without ping.
- **Contract:** `templates/contract` exists. Generate from lead data, pre-filled, you e-sign (L2). Ghost never signs.
- **Payment:** Razorpay provider exists. Ghost creates payment links (autonomous), verifies capture against **webhook signature** (never against a "verified" flag it wrote itself — same honesty rule), and only then triggers handover. Manual bank fallback exists for India-first clients.

### Stage 6 — Welcome & onboarding (L3)
- Sequence on payment-verified webhook: welcome email (`templates/welcome`), onboarding page (content intake form: photos, menu/services, hours, socials — the *only* thing the client must do), project timeline, single point of contact = Ghost's client-facing identity.
- **Onboarding chase rule:** 2 nudges, then ping you (client unresponsive >7 days = at-risk revenue).

### Stage 7 — Retainers: the actual business (L4 eventually)
The real money and the real autonomy showcase. Monthly retainer = monitoring + auto-fix + monthly report.

**Monitors (new worker `site_monitor`):**
- Uptime (1-min checks, 3 regions), SSL expiry (alert 21d before), broken links (weekly crawl), SEO drift (weekly: title/meta intact, robots.txt, sitemap, Core Web Vitals via PageSpeed API), domain expiry, analytics heartbeat.
- **Auto-fix tiers:**
  - T1 (L4): cert renewal, DNS typo, cache purge, dependency bump on *staging* + QA-gated promote
  - T2 (L3): broken link → restore from snapshot; content drift → restore + notify client
  - T3 (L2): anything needing new code or client decisions → staged fix + ping you with a one-click approve
- **Snapshot discipline:** every live client site gets daily git snapshot + weekly full backup (host + DB + assets). No touch without a snapshot that restore-drill has *proven* restorable (monthly random restore test into a sandbox — evidence, again).
- **Billing:** Razorpay subscriptions; dunning (retry ×3 → grace 7d → suspend with 14-day hold → ping you before any deletion). **Ghost never deletes a paid client's site without explicit human confirmation. Ever.**
- **Monthly client report:** auto-generated (uptime %, fixes applied, traffic if analytics shared) — this report is why clients stay.

---

## 3. Reliability (the part that makes it a product, not a demo)

1. **Queue, not cron.** Single durable job queue (Postgres-backed or Redis+RQ) wrapping every worker: idempotency keys, exponential backoff, poison-pill quarantine, dead-letter with ping. One job crashing never kills a pipeline.
2. **Transactional CRM.** Lead state transitions (`demo_state_machine` exists) become DB transactions. State drift between workers becomes impossible.
3. **Watchdog with teeth.** Existing `watchdog/service.py` extended: heartbeat every 60s; missed beats → auto-restart, 3 failures → ping + freeze outbound (a sick agent must not talk to customers).
4. **Chaos budget.** Weekly automated kill-tests: kill the queue mid-pipeline, corrupt a report, expire a token — assert recovery is clean. Recovery code that's never been run is fiction.
5. **Secrets.** Move all keys (Razorpay, Maps, resend/mail, Vercel) to env vault; LLM contexts never see raw keys; `agy` subprocess runs with a restricted env + allowlist of dirs.
6. **Observability.** Every job writes structured events to a local ledger (`audit_engine`/`source_ledger` extended) → Grafana or a simple web dashboard. "What did Ghost do today and why" must be answerable in one query, for every day, forever.

## 4. Speed

- **Vendored everything** (done for three.js) — no CDN in the render path, QA runs in ~60s vs unbounded before.
- **Parallel pipeline:** lead-hunt fan-out (N leads recon'd concurrently), build + QA + deploy as one async chain per lead; 50/day means ~4/hour of pipeline work — trivial once sequential bottlenecks (agy session per task) are pooled.
- **Warm `agy` sessions:** persistent conversation IDs (CONTEXT.md §3) reused per vertical, so the model isn't cold each build.
- **Cache:** recon results (Maps data ~30d TTL), generated copy fragments, QA render browser kept alive between runs (connect cost dominates).
- **Target SLOs:** lead→qualified 30s; qualified→demo deployed 8min; reply→response drafted 60s; incident→auto-fix 10min.

## 5. Cost (unit economics, tracked like a business)

| Item | Target |
|---|---|
| Recon per qualified lead | < ₹2 (local classifier filters before LLM) |
| Demo build (tokens + Vercel) | < ₹30 |
| Full QA pass | ~₹0 (local compute) |
| **Cost per contacted lead** | **< ₹35** |
| Cost per closed deal (50 leads:1 close assumed) | < ₹1,800 vs ₹15k+ revenue → 8x+ margin |
- **Cheap-first model routing:** classification/qualification/routing on a local or small model (Qwen via Ollama, or Haiku-class); only copywriting and codegen hit the big model. `token_tracker` (exists) enforces per-intent budgets from the policy engine.
- **The 90%-quota rule inverted:** leftover quota at day's end is *not* "spend it hunting" — it's reserve. The decision engine treats quota like cash: hunt budget ≤ 40% of remaining, 60% reserved for serving existing paying clients. Paying work always outranks prospecting.

## 6. Anti-spam & deliverability (how 50/day never becomes spam)

**Definition we operate by:** one personalized, truthful, immediately-honoring-unsubscribe email to a business address, about their actual business, with a real artifact they can use for free. Never: purchased lists, sequences >3, retry-after-unsubscribe, masked identity.

1. **Dedicated sending domain** (e.g., `getghost.dev`), never your personal domain. SPF + DKIM + DMARC (p=reject) from day one. Cold outreach *will* hurt reputation; contain the blast radius.
2. **Warmup:** 5/day week 1 → 20/day week 2 → 50/day week 4. Second domain warmed in parallel as failover (reputation alert → switch, cool down primary).
3. **Caps:** 50 unique businesses/day across domains, **lifetime one-email-per-business-first-touch**, max 2 follow-ups spaced 4d/7d, then permanent suppression. `suppression.py` exists — make it the single source of truth every sender must consult (tested).
4. **Personalization gate:** outreach refuses to send if the email doesn't cite ≥2 lead-specific facts from recon (proof it's not a template blast).
5. **Every email:** real sender identity, working reply-to (a human reads it — you or Ghost triage), one-click "not interested" link that suppresses instantly and silently.
6. **Compliance:** India DPDP Act 2023 (purpose limitation, withdrawal), CAN-SPAM/GDPR if expanding. No sensitive categories, no minors-adjacent verticals. Legal review before non-India expansion.
7. **Reputation telemetry:** bounce %, reply %, complaint % on dashboards; any spike → auto-freeze + ping (rule 4 in §0).

## 7. Fleet economics — why retainers are the goal
One closed demo deal = nice. **The compounding asset is the retainer fleet.** 20 retainers × ₹8k/mo = ₹1.6L/mo recurring ≈ full autonomy funded. Decisions optimize for retainer conversion (demo → site → retainer ladder), not one-off deal count. Churn is the metric that kills this business — hence §7's monthly reports and 7-day at-risk escalation.

## 8. Freelance overflow — the honest version
Upwork/Fiverr ToS **prohibits automated bidding**. Account bans would burn a reusable asset. So:
- **Phase A (compliant):** Ghost as *bid copilot* — it monitors RSS/API feeds for matching jobs, drafts bids + delivery plans in a queue; **you press send** (5 min/day, 10 bids). Speed-to-bid is the actual edge; automation isn't required for it.
- **Phase B (if ever):** only on platforms whose ToS permits automation, or via direct "overflow" contracts with 2–3 agencies (§9) where ToS doesn't apply.
- Scope-creep detector: incoming client message classifier flags requests outside original scope → auto-drafts change-order + price → ping you (category (a) if > $X).

## 9. White-label for agencies
- **Multi-tenant CRM:** agency = tenant with own branding config (logo, colors, from-name, domain for outreach).
- **Product surface:** a simple API + dashboard the agency sees: "submit lead → get demo link → approve → delivered". They never see Ghost's internals.
- **Revenue split:** automated — Razorpay routes: agency share to their account, platform share to yours (Razorpay Route exists for exactly this).
- **Onboarding an agency = 20 retainer clients at once.** Sales motion: you pitch 10 agencies with *Ghost's own work* as the portfolio. Priority below retainers, above freelance overflow.

## 10. The Notification Contract (when Ghost pings you)
Exactly three categories, enforced in the policy engine — every other event goes to the daily digest (read at your leisure):
- **(a) Money:** deal > ₹X (configurable), any discount >10%, any refund/chargeback, monthly retainer churn.
- **(b) Client distress:** complaint classification, 2+ unresolved tickets, at-risk onboarding.
- **(c) Unverifiable:** QA inconclusive, reply it can't classify at confidence, policy conflict, cap hit with queued work that expires soon.
Everything else: **daily digest at 09:00** — leads contacted, demos live, replies, fixes shipped, MRR, quota burn. One screen. This digest is your Jarvis experience: the system ran all day; here's the harvest.

## 11. Security & blast-radius hardening (prereq for L3+)
- `agy`/LLM subprocesses: no shell escalation, filesystem allowlist, network allowlist, no secrets in context.
- Outbound actions signed by policy engine grants (a worker without a grant token can't send mail — enforced at the mail provider wrapper, not at the LLM prompt).
- Client data (their site content, chats) encrypted at rest; per-tenant isolation; GDPR-style deletion path (right to be forgotten is also good hygiene).
- **Weekly automated red-team:** a test harness tries to make Ghost break each constitution rule (spam cap, payment gate, deletion gate). Any success = auto-demote all levels + ping.

## 12. Metrics (the scoreboard replaces vibes)
- **Growth:** leads/day, reply rate (target >8% on cold), demo→call rate, close rate, CAC, MRR, churn.
- **Autonomy:** % actions unattended, incidents/1k actions, mean time to auto-fix, evidence-coverage (fraction of actions with verifiable artifacts — target 100%).
- **Cost:** ₹/lead, ₹/demo, ₹/closed deal, gross margin per retainer.
- **Trust:** negative reply rate (<0.5% hard ceiling), bounce (<2%), zero spam complaints.

## 13. Build order (each phase ships value, each exit is measurable)

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0 — Skeleton hardening** (1–2 wk) | Queue + transactions + watchdog teeth + secrets + halt switch + audit ledger | Kill-test week passes clean; zero silent failures in a 50-lead simulated day |
| **P1 — Decision engine v1** (1 wk) | Policy engine + state snapshot + FIND_LEADS rule + caps + daily digest | Runs a full simulated day deterministically; replay test identical |
| **P2 — Pipeline wiring** (2 wk) | Deploy gated on QA; reply classifier; Razorpay webhook verification; contract/payment/welcome chain | 10 real leads end-to-end with you sampling everything; zero unverified sends |
| **P3 — Autonomy ramp** (3–4 wk) | Ladder promotion per capability, 50/day outreach with warmup, call-needed detection | 30 consecutive days: zero spam complaints, zero unverified actions, ≥1 closed deal unattended except signature |
| **P4 — Retainer engine** (2–3 wk) | site_monitor + snapshot/restore drills + auto-fix tiers + subscriptions + dunning + monthly reports | First 5 paying retainers; restore drill passes monthly; incident→fix <10min |
| **P4.5 — Voice presence** (2 wk, overlaps P4) | Runner-pattern task loop + narrator on the event bus + Kokoro streaming acks + orb activity states + barge-in | Reference-parity 30s demo on tape (§14.3): command → narrated milestones → "Done"; every spoken number traceable to a logged event |
| **P5 — Scale & white-label** (ongoing) | Agency tenants, revenue routing, second sending domain, market expansion, voice client line | First agency signed; MRR covers all compute + your attention is optional |

## 14. Voice Presence Protocol — "talk and work simultaneously"

**Reference behavior** (`Reference.mp4`, "POV: JARVIS Builds A Website In 30 Seconds"): the user gives one voice command; the assistant immediately acknowledges ("Searching…"), **narrates continuously while it works** (real progress, real counts), the result visibly materializes, and completion is announced verbally. Work and speech are simultaneous, never sequential. This is the behavior contract for Ghost's voice surface — and it applies to the *whole* system, not just demos.

### 14.1 The interaction contract (per task, by expected duration)

| Class | Duration | Ghost's behavior |
|---|---|---|
| **Instant** | < 2s (facts, media, desktop control) | Answer **while acting** — first syllable within 500ms of intent, action lands mid-sentence. No preamble, no "sure!" |
| **Short** | 2–30s (search, small edits, a demo build) | **"On it."** → work in silence or 1–2 milestone lines → **"Done — [result + artifact]"** |
| **Long** | > 30s (pipeline runs, lead hunts, multi-step builds) | **"I'm doing it now."** → narrate real milestones as they fire → **"Done"** with evidence; if it exceeds estimate: proactive status ("Still on it — QA running, 40 seconds in") |
| **Background** | minutes–hours (retainer fixes, campaigns) | Acknowledge once → work silently → announce outcome when you're next interacting (or digest). Never nags mid-task unless a §10 ping condition hits |

The rule the user sees: **Ghost is never quiet-and-busy, and never chatty-and-idle.** Speech tracks actual work state, exactly like the reference video.

### 14.2 Engineering: the narrator is a subscriber, not a liar

This is the critical design decision, and it's the honesty constitution applied to speech:

1. **Single event bus.** Every worker already emits lifecycle events for the audit ledger (§3.6). The **Narrator** subscribes to that same bus — it can only speak what an event just proved. Ghost physically cannot say "searching 211 photographs" unless the recon worker emitted `assets_found: 211`. No invented progress, ever — same rule as `three_qa.py`: no verdict without evidence, and here, **no narration without an event**.
2. **Event → speech mapping (per pipeline):** `lead_hunt.candidates` → "Found 14 businesses without websites." · `demo.deployed` → "Demo's live." · `qa.passed` → "QA passed — ten screenshots clean." · `payment.captured` → "Payment confirmed, ₹45,000. Contract sent." Templates are deterministic; the LLM only does fluency, not facts.
3. **Runner pattern (CONTEXT.md §2.1) is the spine.** Task execution as an interruptible async generator — so voice *and* work share one loop. Speech can't block work; work can't block speech; barge-in cancels both instantly.
4. **TTS latency budget (Kokoro, streaming):** ack ≤ 500ms (pre-warmed phrases), milestone lines ≤ 1s from event, sentence-level streaming so long results start speaking before generation finishes.
5. **The orb narrates too** (CONTEXT.md §1 visual spec): idle breathing → activity filaments on speech; while working silently (background class) the orb shows a subtle "working" pulse — visible state without words, so you can tell busy from idle at a glance.
6. **Barge-in discipline:** your voice interrupts output *and* task narration mid-stream (VAD pre-roll, 32ms frames). "Stop" freezes the pipeline at the next yield point, states what was completed and what's suspended, and awaits orders.
7. **Milestone throttling:** max 1 narration per ~4s per task (the reference video's rhythm: sparse, confident counts — not a auctioneer). Only milestones on the class's allowance: Instant 0, Short ≤2, Long ≤1/8s of task.

### 14.3 Deliverable definition of done for voice
A 30-second reference-parity demo, verifiable on tape: you say "find me an expensive restaurant without a website and build them a demo." Ghost says "On it — searching Kalena Agrahara…" (recon event fires) → "Found 6. Building for the top one." (builder event) → "QA passed, deploying." → "Done. Demo's live at <URL> — ₹5,000-a-head, no website. Draft email ready when you are." Every spoken number traceable to a logged event in the same timestamp. **That recording becomes Ghost's own marketing asset** (Ghost for Ghost, §15.4).

## 15. Beyond what you listed (the "cause this is all I can think of" section)

1. **Referral engine:** every closed client gets a "know another business that needs this?" nudge with a finder's discount — cheapest leads you'll ever get, zero spam risk.
2. **Portfolio flywheel:** every finished site (with client permission) improves the golden templates → demos get better → close rate rises → demos get better. This compounding loop is the real moat.
3. **Renewal concierge:** 30d before annual domain/hosting renewals, Ghost shops prices, proposes savings, handles migration. Clients save money → they stay.
4. **Ghost for Ghost:** dogfood everything — Ghost's own landing site, uptime, and SEO are maintained by Ghost. The best sales demo is the product maintaining itself.
5. **Voice client line (post-P5):** clients call a number, Ghost triages ("site down" → incident flow; "change my menu" → change order; "human" → you). This is where the Jarvis stack earns money instead of admiration.
6. **Market timing intel:** Ghost watches new-business registrations and seasonal openings (restaurants before festival season, tax offices before fiscal year) and *times* outreach — the same email sent the week a business opens converts 5x.
7. **Churn prediction:** reply latency trend + report open rate → at-risk score → proactive sweetener (free small upgrade) before they think about leaving.
8. **The "$X knob" philosophy:** every threshold in this doc (ping price, caps, discount limits) is one config file you own. As trust grows you turn the knobs; the system never decides its own limits.
