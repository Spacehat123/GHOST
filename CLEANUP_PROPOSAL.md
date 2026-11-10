# GHOST CLEANUP & REFACTORING PROPOSAL
**Audit Task:** GHOST TASK 001 — Repository Audit & Cleanup Plan  
**Status:** PROPOSED PLAN (AWAITING OPERATOR APPROVAL — ZERO ACTIONS APPLIED)  
**Date:** 2026-11-10T14:31:00+05:30  
**Repository Path:** `/home/pranc/projects/ghost`  

---

## 1. Notice of Read-Only Constraint

> [!IMPORTANT]
> **NO ACTIONS HAVE BEEN PERFORMED.**
> In strict accordance with the GHOST Engineering Contract, this document is a **proposal only**. No files will be moved, modified, renamed, or deleted until the operator reviews this proposal and gives explicit approval.

---

## 2. Category A: Safe Cleanup Candidates (Zero Risk)

These items are confirmed dead artifacts, scratch files, or test outputs that have zero runtime references.

| Exact Path | Reason | Evidence | Dependencies / References | Risk Level | Expected Benefit | Reversibility |
|---|---|---|---|---|---|---|
| `src/rose/` | Abandoned directory skeleton from early project renaming. | Contains 7 empty folders and empty `__pycache__` dirs; 0 files. | Grep across entire codebase finds zero imports of `src.rose` or `rose`. | **None** | Eliminates confusion about dual root packages; clean `src/` tree. | Trivial (git restore / recreate empty dirs) |
| `temp/WhatsApp Image*.jpeg` (6 files, 1.2 MB) | Loose personal image dumps dropped into repo. | Static binary files with no references in code or tests. | Zero code references. | **None** | Reclaims 1.2 MB disk space and cleans root working tree. | Trivial |
| `temp/a.zip` (1.18 MB) | Loose zip archive from past session. | Binary zip archive unreferenced by any script or test. | Zero code references. | **None** | Reclaims 1.2 MB disk space. | Trivial |
| `temp/84c6e18427517d0becf88f88ecd984c9.avif` (43 KB) | Loose unreferenced image asset. | Single unreferenced AVIF image in `temp/`. | Zero code references. | **None** | Clean `temp/` folder. | Trivial |
| `demos/kit-sm-11234b/` | Old test-run state machine output. | Automated fixture output from previous testing session. | Zero production references; test suite generates fresh slugs. | **None** | Prevents demo directory bloat. | Trivial |
| `demos/kit-sm-62ba17/` | Old test-run state machine output. | Automated fixture output from previous testing session. | Zero production references. | **None** | Prevents demo directory bloat. | Trivial |
| `demos/kit-sm-651075/` | Old test-run state machine output. | Automated fixture output from previous testing session. | Zero production references. | **None** | Prevents demo directory bloat. | Trivial |
| `demos/kit-sm-654707/` | Old test-run state machine output. | Automated fixture output from previous testing session. | Zero production references. | **None** | Prevents demo directory bloat. | Trivial |
| `demos/kit-sm-b88609/` | Old test-run state machine output. | Automated fixture output from previous testing session. | Zero production references. | **None** | Prevents demo directory bloat. | Trivial |
| `demos/kit-sm-bd8d36/` | Old test-run state machine output. | Automated fixture output from previous testing session. | Zero production references. | **None** | Prevents demo directory bloat. | Trivial |
| `src/data/rhythm/scheduler_cycle.json` | Misplaced runtime data in source tree. | Runtime scheduler cycle state written to `src/data/` instead of `data/rhythm/`. | Runtime reads from `data/rhythm/scheduler_cycle.json`. | **None** | Keeps `src/` purely code-only. | Easy |
| `projects/` | Empty directory. | Zero files inside `projects/`. | Zero references. | **None** | Removes empty directory artifact. | Trivial |

---

## 3. Category B: Duplicated & Superseded Documentation

The repository currently contains 12 top-level markdown files in root. Several represent historical milestones that contradict the active architecture.

| Exact Path | Reason | Evidence | Dependencies / References | Risk Level | Expected Benefit | Reversibility |
|---|---|---|---|---|---|---|
| `ROADMAP_AUTONOMY.md` | Superseded by `ROADMAP_TOTAL_AUTONOMY.md`. | Replaced by Rev 2 / Rev 3 in `ROADMAP_TOTAL_AUTONOMY.md`. | Referenced as historical context in `ROADMAP_TOTAL_AUTONOMY.md`. | **Low** | Move to `docs/archive/ROADMAP_AUTONOMY.md`. Eliminates duplicate roadmap confusion. | Easy (`git mv`) |
| `PLAN.md` | Day-one initial vision draft (Nov 1). | Lacks 5-plane architecture, Operator's 11, and unified store details. | None. | **Low** | Move to `docs/archive/PLAN.md`. Preserves history without cluttering root. | Easy (`git mv`) |
| `RESEARCH_JARVIS_ARCHITECTURE.md` | Early research document for desktop voice HUD. | Historical background notes. | None. | **Low** | Move to `docs/archive/RESEARCH_JARVIS_ARCHITECTURE.md`. | Easy (`git mv`) |
| `agents.md` | Early 4-agent specification (Nov 3). | Describes discarded 4-subagent swarm superseded by single Ghost director + Section Kit. | Mentioned in early plans. | **Low** | Move to `docs/archive/agents.md`. Prevents confusion on subagent architecture. | Easy (`git mv`) |
| `WEBSITE_BUILD_GUIDE.md` | Early manual site guide. | Superseded by `templates/sections/README.md` and `section_kit.py`. | None. | **Low** | Move to `docs/archive/WEBSITE_BUILD_GUIDE.md`. | Easy (`git mv`) |
| `LOCAL_SITE_BUILD_BRIEF.md` | Early prompt engineering guide for local builds. | Superseded by `AutonomousDemoStateMachine` Phase 2 brief generator. | None. | **Low** | Move to `docs/archive/LOCAL_SITE_BUILD_BRIEF.md`. | Easy (`git mv`) |
| `KOKORO_GPU_FIX_PLAN.md` | Specific debug plan for CUDA ONNX execution. | Completed fix log from Nov 4. | Script `scripts/kokoro_gpu_diagnose.py` is the active implementation. | **Low** | Move to `docs/archive/KOKORO_GPU_FIX_PLAN.md`. | Easy (`git mv`) |
| `fix.md` | Stale scratch notes from Nov 4 (1.6 KB). | Unstructured notes. | None. | **Low** | Move to `docs/archive/fix.md` or delete. | Easy (`git mv`) |
| `temp/07_3D_SITE_PLAN.md`, `temp/08_GHOST_THINKING_AND_TASK_SPLITTING.md`, `temp/09_FREE_ASSET_STACK.md`, `temp/10 flow router and motion site.md`, `temp/skill_cinematic_3d_site.md` | Planning notes left in `temp/`. | Valuable design notes accidentally saved in scratch temp. | None. | **Low** | Move to `docs/archive/planning_notes/` for preservation. | Easy (`git mv`) |

---

## 4. Category C: Potentially Unused Code Requiring Operator Confirmation

These components appear to be superseded by the Section Kit and Next.js production exporter, but require explicit operator confirmation before any action.

| Exact Path | Reason | Evidence | Dependencies / References | Risk Level | Expected Benefit | Reversibility |
|---|---|---|---|---|---|---|
| `Compo's/` | Directory has malformed folder names (trailing `\n`) and apostrophe. | 15+ standalone React UI components (`Swipetoconfirmbutton`, `DynamicIslandtoast.tsx`, etc.). | Not imported by any Python worker or template. | **Low** | Sanitize and rename directory to `components/` or move to `templates/components/`. | Easy |
| `src/ghost/workers/framer.py` | Framer desktop copy-paste worker. | Legacy worker for pushing components into Framer clipboard. | Tested in `test_imports.py`; not called in core pipeline. | **Medium** | Archive to `src/ghost/workers/legacy/` if Framer workflow is inactive. | Easy |
| `src/ghost/workers/figma.py` | Figma REST API worker. | Legacy worker for Figma node inspections. | Tested in `test_imports.py`. | **Medium** | Archive to `src/ghost/workers/legacy/` if Figma token is unconfigured. | Easy |
| `src/ghost/workers/motion_site_builder.py` | Standalone single-file HTML motion builder. | Superseded by `section_kit.py` modular composer. | Tested in `test_motion_site_builder.py`. | **Medium** | Keep until Section Kit fully replaces all motion templates. | Easy |
| `src/ghost/workers/three_site_builder.py` | Three.js WebGL site builder. | Superseded by standard Section Kit for local businesses. | Tested in `test_three_builder.py`. | **Medium** | Retain for 3D showcase clients; do not delete. | Easy |

---

## 5. Category D: Architectural Problems & Code Smells

| Exact Path | Reason | Evidence | Dependencies / References | Risk Level | Expected Benefit | Reversibility |
|---|---|---|---|---|---|---|
| `src/ghost/core/brain.py` (2,609 lines) | Oversized module violating single responsibility. | Combines system prompt strings, 14 tool schema definitions, execution dispatcher, and model routing. | Core module imported by `agent.py` and server. | **Medium** | Split into `core/prompts.py`, `core/tools_schema.py`, and `core/tool_dispatcher.py`. Dramatically improves maintainability. | Moderate (refactoring) |
| `src/ghost/workers/demo_state_machine.py` (2,287 lines) | Oversized state machine module. | Combines Recon, Brief generation, Section assembly, DOM QA, visual self-healing, and outreach pitching. | Imported by `demo_builder.py` and CLI. | **Medium** | Extract Phase 4 visual self-healing and Phase 2 brief generation into dedicated worker modules. | Moderate |
| `follow.py` (in root) | Standalone root script rather than integrated CLI subcommand. | Direct script running `DirectiveManager` functions (`add`, `list`, `remove`). | Wraps `src/ghost/core/follow.py`. | **Low** | Move into `scripts/follow.py` and register `ghost directives` subcommand in `src/ghost/cli.py`. | Easy |
| `src/ghost/server/app.py:69,93` | Deprecated FastAPI lifespan event handlers. | Uses `@app.on_event("startup")` and `"shutdown"`. | FastAPI server lifecycle. | **Low** | Migrate to standard `@asynccontextmanager async def lifespan(app: FastAPI)` pattern to clear DeprecationWarnings. | Easy |

---

## 6. Category E: Dependency Corrections

| Exact Path | Reason | Evidence | Dependencies / References | Risk Level | Expected Benefit | Reversibility |
|---|---|---|---|---|---|---|
| `requirements.txt` | Missing core production dependencies. | `openai`, `sympy`, and `pillow` are imported in `src/ghost/` but omitted from `requirements.txt`. | `brain.py` imports `openai`; `math/` imports `sympy`; `three_qa.py` imports `PIL`. | **Low** | Append `openai>=1.40.0`, `sympy>=1.13.0`, `pillow>=10.0.0` to `requirements.txt`. Ensures clean environment reproduction. | Easy |
| `pyproject.toml:9` | References non-existent `README.md`. | `readme = "README.md"` causes `pip install -e .` to fail with `FileNotFoundError`. | Packaging setup. | **Low** | Create concise root `README.md` or set `readme = ""` / point to existing doc. | Easy |
| `pyproject.toml:12` | Unused dependency `fury-sdk`. | `fury-sdk>=0.3.2` listed as mandatory requirement, but zero Python files import it. | None. | **Low** | Remove `fury-sdk` from `pyproject.toml` dependencies. Reduces install friction. | Easy |

---

## 7. Category F: Security & Configuration Improvements

| Exact Path | Reason | Evidence | Dependencies / References | Risk Level | Expected Benefit | Reversibility |
|---|---|---|---|---|---|---|
| `data/archive/` (52 directories) | Git is actively tracking historical site archives. | `git ls-files data/archive/` shows 52 tracked directories. | None. | **Low** | Run `git rm --cached -r data/archive/` and add `data/archive/` to `.gitignore`. Prevents repo bloating. | Easy |
| `data/snapshots/` | Git is actively tracking multi-megabyte binary screenshot files. | `data/snapshots/grasshopper/` contains tracked JPEGs and PNGs. | `snapshots.py`. | **Low** | Run `git rm --cached -r data/snapshots/` and add `data/snapshots/` to `.gitignore`. Reduces git clone size. | Easy |
| `data/heartbeat.json`, `data/rhythm/scheduler_cycle.json` | Volatile runtime pulse files tracked in Git. | `git status` constantly reports these modified after running commands. | Background runtime loops. | **Low** | Run `git rm --cached` on these runtime files and ensure they are covered by `.gitignore`. Keeps git working tree clean. | Easy |

---

## 8. Category G: Directory Restructuring Proposals

```
PROPOSED TARGET RESTRUCTURING:
├── docs/
│   ├── archive/              <── Move PLAN.md, agents.md, ROADMAP_AUTONOMY.md, temp/*.md here
│   ├── OPERATOR_MANUAL.md    <── Retained (Authoritative)
│   ├── MIGRATION_MAP.md      <── Retained
│   └── PROPOSAL_TYPES.md     <── Retained
├── components/               <── Rename from "Compo's/" and sanitize trailing \n in folder names
├── scripts/
│   ├── follow.py             <── Move root follow.py here (with CLI alias `ghost directives`)
│   ├── launch_ghost.sh       <── Retained
│   ├── store_census.py       <── Retained
│   └── kokoro_gpu_diagnose.py<── Retained
├── README.md                 <── Create professional root README (fixes pyproject.toml build)
└── src/
    └── ghost/                <── Primary code (cleanly isolated, zero src/rose / src/data)
```

---

## 9. Category H: Missing Documentation

1. **Root `README.md`:** Currently missing. A clean README explaining Ghost's purpose, setup instructions (`pip install -r requirements.txt`), and quickstart CLI commands (`ghost status`, `ghost run`) is required.
2. **Developer Onboarding Guide:** Documenting the 5-plane model, HMAC grant requirement, and running the test suite (`PYTHONPATH=src python3 -m unittest discover -s tests`).

---

## 10. Category I: Components That MUST Be Preserved

The following components are vital to Ghost's operation and **must never be deleted or degraded**:

1. **All 5 Structural Planes:**
   - Control Plane: [`src/ghost/authority/`](file:///home/pranc/projects/ghost/src/ghost/authority/) (HMAC grants, `grantd.sock`), [`src/ghost/control/`](file:///home/pranc/projects/ghost/src/ghost/control/) (T7 quarantine, reserved settings).
   - Core Plane: [`src/ghost/core/`](file:///home/pranc/projects/ghost/src/ghost/core/) (`halt.py`, `agent.py`, `brain.py`, `jobqueue.py`, `audit.py`).
   - Storage Plane: [`src/ghost/store/`](file:///home/pranc/projects/ghost/src/ghost/store/) (unified database facade), `data/ghost.db`, `data/crm.db`.
2. **Client Demo Generation Pipeline:**
   - [`src/ghost/workers/demo_state_machine.py`](file:///home/pranc/projects/ghost/src/ghost/workers/demo_state_machine.py), [`src/ghost/workers/section_kit.py`](file:///home/pranc/projects/ghost/src/ghost/workers/section_kit.py), [`src/ghost/workers/export_demo_to_production.py`](file:///home/pranc/projects/ghost/src/ghost/workers/export_demo_to_production.py), [`src/ghost/workers/deploy.py`](file:///home/pranc/projects/ghost/src/ghost/workers/deploy.py).
   - Component templates in [`templates/sections/`](file:///home/pranc/projects/ghost/templates/sections/).
3. **The 773-Test Automated Test Suite:**
   - All files in [`tests/`](file:///home/pranc/projects/ghost/tests/), golden traces, and contract fixtures.
4. **Authoritative Operational Docs:**
   - [`chief.md`](file:///home/pranc/projects/ghost/chief.md) and [`docs/OPERATOR_MANUAL.md`](file:///home/pranc/projects/ghost/docs/OPERATOR_MANUAL.md).
