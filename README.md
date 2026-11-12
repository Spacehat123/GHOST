# GHOST Autonomous Agency Engine

Verified personal intelligence and built-in workspace environment.

# GHOST: Universal AI Operating Environment

[![Architecture Target](https://img.shields.io/badge/Architecture-Universal%20OS-blue)](ARCHITECTURE_TARGET.md)
[![Security Model](https://img.shields.io/badge/Security-Fail--Closed%20HMAC-red)](SECURITY_AND_PERMISSION_MODEL.md)
[![Hardware Target](https://img.shields.io/badge/Target-Arch%20Linux%20%7C%20GTX%201650-green)](RESOURCE_MANAGEMENT_PLAN.md)
[![Status](https://img.shields.io/badge/Status-Phase%203%20Active-brightgreen)](IMPLEMENTATION_ROADMAP.md)

GHOST is an autonomous, context-aware AI operating environment designed for deep desktop awareness, multi-domain intelligence, deterministic tool execution, and continuous agent orchestration.

Originally conceived as a local-business agency engine with ambient HUD and full-duplex voice capabilities, GHOST has expanded into a **Universal Intelligence System** operating under strict hardware budgets (8 GB host RAM, 4 GB VRAM on NVIDIA GTX 1650 Mobile) and zero-local-model cloud routing via OmniRoute.

---

## Architectural Principles

1. **Continuous Environmental Awareness:** GHOST does not sit idle waiting for user prompts; it continuously monitors system metrics, Git repositories, desktop events, and workflows via non-blocking collectors.
2. **Deterministic Fail-Closed Tool Execution:** High-risk actions require single-use cryptographically signed HMAC capability tokens (`authority/protocol.py`). No tool executes outside its permission level.
3. **Instant Circuit Breaking:** GHOST honors `data/HALTED` instantly across all background tasks, subagents, and network dispatchers.
4. **Zero Local Model Burden:** All LLM reasoning is offloaded via an ultra-low latency local reverse proxy (`http://127.0.0.1:20128/v1`), reserving 100% of local GPU resources for Wayland compositor stability, local Kokoro ONNX speech synthesis, and real-time screen OCR.

---

## The Seven Intelligence Domains

```
┌────────────────────────────────────────────────────────────────────────┐
│                      GHOST UNIVERSAL INTELLIGENCE                      │
├─────────────┬─────────────┬─────────────┬─────────────┬────────────────┤
│   SYSTEM    │ DEVELOPMENT │  RESEARCH   │  PERSONAL   │    PHYSICAL    │
│  procfs/GPU │ Git/CI/Test │ Web/Scrape  │ PIM/Tasks   │  Home/Sensors  │
├─────────────┴──────┬──────┴─────────────┴──────┬──────┴────────────────┤
│                    │         BUSINESS          │       CREATIVE        │
│                    │ CRM / Outreach / Invoices │ Media / Demos / 3D    │
└────────────────────┴───────────────────────────┴───────────────────────┘
```

1. **System Intelligence:** Kernel metrics (`/proc`), NVML GPU telemetry, bottleneck detection, anomalous crash detection, self-health monitoring.
2. **Development Intelligence:** Multi-repo Git tracking, change set detection, test runner invocation, automated patch generation, AST graph updates (`graphify`).
3. **Research Intelligence:** Headless CDP search, Brave web integration, deep page extraction, research synthesis, claim verification.
4. **Personal Intelligence:** Obsidian daily log journaling, scheduling, calendar synchronization, contextual reminders.
5. **Physical Intelligence:** Local network topology discovery, hardware device management, home sensor ingest.
6. **Business Intelligence:** Automated local business discovery, multi-channel outreach pipeline, CRM state machines, proposal delivery.
7. **Creative Intelligence:** Visual demo generation, Three.js 3D viewport synthesis, Next.js production site export, typography and design token systems.

---

## System Architecture

GHOST is structured into five functional planes across nine architectural layers:

```
[Layer 1: Perception]       -> Collectors (System, Git, Screen, Web, Audio)
                                       │ (Universal Events)
[Layer 2: Event Bus]        -> SQLite WAL-backed Pub/Sub (events_v2)
                                       │
[Layer 3: State Engine]     -> SystemStateManager & Shared Memory Snapshots
                                       │
[Layer 4: Intelligence]     -> Cloud OmniRoute Gateway (L0-L4 Permission Engine)
                                       │
[Layer 5: Orchestrator]     -> Deterministic Task DAG & Capability Granting
                                       │
[Layer 6: Agents]           -> Domain Subagents (Patrol, Coder, Outreach, QA)
                                       │ (HMAC Signed Tokens)
[Layer 7: Tools]            -> Sandboxed Execution Registry & Filesystem Guards
                                       │
[Layer 8: Persistence]      -> Vector Memory, SQLite, Obsidian, File Ledger
                                       │
[Layer 9: Verification]     -> Post-Action DOM QA, AST Linters, Self-Healers
```

---

## Quickstart & Installation

### Prerequisites
- **OS:** Linux (Arch Linux recommended, Hyprland / Wayland supported)
- **Python:** Python 3.10+ (recommended Python 3.12 or 3.14 venv)
- **OmniRoute Proxy:** Running locally at `http://127.0.0.1:20128/v1`
- **Optional Audio:** `pulseaudio` / `pipewire`, `espeak-ng`

### Installation

Clone the repository and install in editable development mode:

```bash
git clone https://github.com/pranc/ghost.git
cd ghost
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Starting GHOST

Launch the core services:

```bash
# Start GHOST API and Web Server
python -m uvicorn ghost.server.app:app --host 127.0.0.1 --port 8765

# Launch the Full Desktop Ambient HUD
ghost --mode hud

# Run the Autonomous Watchdog Patrol in headless mode
ghost --mode watchdog
```

---

## Security & Verification

GHOST adheres to a multi-tiered security model:

- **Level 0 (Observer):** Read-only system and metric collectors. No tokens required.
- **Level 1 (Drafting):** Code generation, proposal draft, staging demos. Local filesystem writes inside sandbox.
- **Level 2 (Active Testing):** Local test execution, local build verification, offline DOM linting.
- **Level 3 (Supervised External):** Outbound emails, CDP browser navigation, network requests. Requires human review or explicit policy grant.
- **Level 4 (Direct Financial):** Collect-only payment link generation. Outbound payouts are cryptographically disabled.

To trigger an emergency system stop, write `HALT` to the kill-switch:
```bash
echo "HALT" > data/HALTED
```

---

## Authoritative Documentation

For deep technical specifications, consult the project documents:

- [Target Architecture (`ARCHITECTURE_TARGET.md`)](ARCHITECTURE_TARGET.md)
- [Gap Analysis (`ARCHITECTURE_GAP_ANALYSIS.md`)](ARCHITECTURE_GAP_ANALYSIS.md)
- [Implementation Roadmap (`IMPLEMENTATION_ROADMAP.md`)](IMPLEMENTATION_ROADMAP.md)
- [Interface Contracts (`INTERFACE_CONTRACTS.md`)](INTERFACE_CONTRACTS.md)
- [Security & Permission Model (`SECURITY_AND_PERMISSION_MODEL.md`)](SECURITY_AND_PERMISSION_MODEL.md)
- [Resource Management Plan (`RESOURCE_MANAGEMENT_PLAN.md`)](RESOURCE_MANAGEMENT_PLAN.md)
- [Directory Map (`DIRECTORY_MAP.md`)](DIRECTORY_MAP.md)
- [Repository Audit (`REPOSITORY_AUDIT.md`)](REPOSITORY_AUDIT.md)

---

## License

Internal proprietary research & autonomous operating environment. All rights reserved.
