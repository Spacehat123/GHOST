# PROPOSAL_TYPES — T6a taxonomy (brain proposes; control disposes; effects execute)

**Date:** 2026-11-06 · **Source:** full GHOST_TOOLS inventory (47 tools, extracted programmatically from core/brain.py:36)
**Contract:** the LLM's only output for actions is a typed proposal
`{action, args, risk_class, personalization_facts}`. Control validates the
risk class against the ladder + halt + grants; effects execute; verify
checks the outcome and its result becomes the loop's tool result. **The
legacy direct-tool path is deleted per capability once parity is proven (T6b).**

## Risk classes

| class | meaning | authority required |
|---|---|---|
| `READ` | no state change anywhere | sandbox only (no grant) |
| `LOCAL_MUTATION` | reversible local change (files, configs, missions, media) | sandbox + audit (no grant) |
| `SANDBOXED_SHELL` | arbitrary command inside the agy sandbox | sandbox launch + audit |
| `PIPELINE_EFFECT` | mutates the revenue pipeline / external SaaS state | grant (`pipeline`) + outbox |
| `EXTERNAL_EFFECT` | public/money/identity: deploy, messaging | grant (capability-specific) + outbox + verifier |

## The revenue cycle converts FIRST (strangler order)

| # | capability (today's tool) | proposal action | risk | executor (effects/) | verifier (verify/) |
|---|---|---|---|---|---|
| 1 | `scout_expensive_venues_without_website` | `leads.scout` | PIPELINE_EFFECT | workers/lead_sources + campaign | lead_hunt.candidates audit + dedupe check |
| 2 | `qualify_venue_lead` | `leads.qualify` | PIPELINE_EFFECT | workers/lead_qualifier | scores present + gaps_json valid |
| 3 | `assess_3d_fit` / `choose_flow` | `leads.choose_flow` | PIPELINE_EFFECT | workers/flow_router | flow ∈ {standard,motion,3d} + reason cited |
| 4 | `build_luxury_demo` / `build_3d_site` / `build_motion_site` | `demo.build` | PIPELINE_EFFECT (cost-capped by grant bounds) | workers/demo* + glb_tools | **three_qa screenshots (existing)** |
| 5 | `deploy_demo_vercel` | `demo.deploy` | EXTERNAL_EFFECT | workers/deploy (T2 grant+snapshot+outbox ✓) | deploy URL HTTP 200 (reconciliation ✓) |
| 6 | `crm_update_client` | `pipeline.update_stage` | PIPELINE_EFFECT | crm/db | stage ∈ enum + audit trail |
| 7 | `whatsapp_action` (browser) | `message.send_whatsapp` | EXTERNAL_EFFECT (operator identity!) | **not auto-convertible — drafts only until T9 operator approval exists** | draft queue row |
| 8 | `email_*` (already banned from brain) | `message.send_email` | EXTERNAL_EFFECT | autonomy/sender (T2 grants ✓) | provider-log reconciliation (T5 ✓) |

## Remaining 39 capabilities (convert in T6b by risk order)

### READ (no grant; sandboxed)
`capture_screen` · `clipboard_action(read)` · `desktop_control(read_context, list/focus)` ·
`browser_action(list_tabs, open_url)` · `media_control(get_track)` · `web_search` ·
`database_query` *→ promoted to SANDBOXED_SHELL: arbitrary db_path+SQL is not a read* ·
`crm_get_client` · `crm_list_pipeline` · `email_check_inbox` (IMAP read-only) ·
`get_premium_resources` · `vercel_check_token` · `figma_inspect_file` · `figma_extract_tokens` ·
`list_skills` · `get_skill` · `mission_status` · `glb_inspect` · `find_glb` ·
`github_action` (read subset) · `rea_action(analyze|doctor|capabilities)`

### LOCAL_MUTATION (audited, reversible)
`desktop_control(move_file, copy_file, wallpaper, workspace, launch_app)` ·
`clipboard_action(write)` · `input_simulation` · `media_control(play..volume)` ·
`desktop_notify` · `git_action` (local) · `mission_create` / `mission_add_task` /
`mission_update_task` · `manage_directives` · `download_glb` *(keeps its
operator_approved parameter — unchanged)* · `generate_image_asset` ·
`glb_optimize` · `get_procedural_audio` · `rea_action(decompile|search|xrefs|trace)`

### SANDBOXED_SHELL
`system_run` · `agent_desktop_command` · `git_action(push/remote)` *(network egress:
stays behind sandbox + explicit operator allowlist until T9)*

### EXTERNAL_EFFECT (grant + outbox + verifier; convert last)
`framer_sync_cms` (writes an external CMS) · `whatsapp_action` (see #7)

## What the brain loop looks like after conversion

```
LLM emits: {"proposal": {"action": "demo.deploy",
                          "args": {"slug": "grasshopper-concept"},
                          "personalization_facts": [],
                          "risk_class": "EXTERNAL_EFFECT"}}
→ control: risk sanity → halt → ladder level for capability → grant mint
→ effects: VercelDeployer.deploy_demo(slug, grant_token=…)   (T2 gates inside)
→ verify:  QA evidence + outbox row + live-URL reconciliation
→ loop tool result = the VERIFIER's dict — never the brain's own claim
```

## Non-negotiables carried from the constitution

- The LLM never mints/edits/reuses grants, changes limits, or approves its own work.
- `operator_approved` parameters on destructive/identity tools remain operator-only.
- Money in Decimal; never delete paid-client data; halt dominates every path.
- Legacy path deleted only at golden-trace parity (6c) — trace = recorded legacy
  session, same inputs → equivalent observable outcomes.

## Strangler progress (measured, not claimed — review finding #2)

**core/brain.py is still 2,373 lines.** The model-facing tool layer is only
PARTLY converted — this is a strangler migration with a legacy fallback, and
pretending otherwise would be the exact dishonesty this project exists to
prevent.

| item | state |
|---|---|
| converted + legacy branch DELETED | `deploy_demo_vercel`, `scout_expensive_venues_without_website` (identical executors, verified parity) |
| adapter-wired (route in proposals mode) | `pipeline.upsert_client`, `pipeline.update_stage`, `demo.deploy`, `leads.scout` |
| mapped but unwired (stay legacy) | `choose_flow`, `build_*`, `whatsapp_action`, everything in the READ/LOCAL_MUTATION tables |
| baseline violations remaining | 4 — brain→comms.email_service (drafts/IMAP reads) + brain→workers.deploy (`vercel_check_token` READ); burn when the READ wave converts |
| line-count trajectory | 2,376 → 2,373 so far; the burn accelerates as each wave deletes its branch |

The honest reading: **the planes are enforced by contract tests today; they
become physical as the waves land.** The authority daemon (grant key out of
the main process) is the exception — it IS structural isolation, not a test.
