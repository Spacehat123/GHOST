# GHOST PHASE 3 REMAINING ISSUES & TECHNICAL DEBT
## Audit of Warnings, Non-Blocking Debt, and Phase 4 Roadmap Hand-Off
**Document:** `PHASE3_REMAINING_ISSUES.md`  
**Status:** ACTIVE AUDIT  
**Date:** 2026-11-10  
**Repository:** `/home/pranc/projects/ghost`  

---

### 1. Python 3.14 Deprecations & Runtime Warnings

1. **`datetime.utcnow()` Deprecation:**
   - *Location:* Multiple legacy modules (`src/ghost/core/audit.py`, `src/ghost/autonomy/watchdog.py`, `src/ghost/core/state.py`).
   - *Issue:* Python 3.12+ deprecated `datetime.datetime.utcnow()` in favor of `datetime.datetime.now(datetime.timezone.utc)`.
   - *Impact:* Generates non-fatal `DeprecationWarning` during test runs and runtime logs.
   - *Resolution Plan (Phase 4):* Migrate timestamps across the codebase to `datetime.now(timezone.utc)`.

2. **Unclosed File and SQLite Descriptors in Legacy Tests:**
   - *Location:* Legacy tests such as `tests/test_p7_fixer.py`, `tests/test_t10_darkrun.py`, `tests/test_crm.py`.
   - *Issue:* Legacy test code uses `open(path).read()` or instantiates transient in-memory SQLite connections without explicit context managers (`with open(...) as f:`).
   - *Impact:* Triggers non-fatal `ResourceWarning: unclosed file` and `ResourceWarning: unclosed database` in Python 3.14 test discovery. All tests still pass cleanly.
   - *Resolution Plan (Phase 4):* Standardize all test file I/O on context managers and explicit `conn.close()` calls in `tearDown()`.

---

### 2. Peripheral Telemetry Fallbacks (Hardware & Compositor)

1. **Wayland / Hyprland Focused Window Telemetry:**
   - *Location:* `src/ghost/collectors/system_collector.py` & `src/ghost/server/app.py`.
   - *Current State:* `get_active_window()` inspects `hyprctl activewindow -j` and falls back to `xdotool getwindowfocus getwindowname` or `"Unknown (Desktop)"` when running headlessly or under non-Wayland sessions.
   - *Impact:* Desktop awareness gracefully reports `"Desktop Active"` when compositor IPC is unavailable.
   - *Enhancement Plan (Phase 4):* Deepen Hyprland socket2 event listener (`$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock`) for zero-polling push notifications of active window switches.

2. **NVIDIA GPU NVML Telemetry:**
   - *Location:* `src/ghost/collectors/system_collector.py`.
   - *Current State:* Samples GPU utilization, VRAM usage, and temperature using `nvidia-smi` subprocess or NVML bindings.
   - *Hardware Target:* Local NVIDIA GTX 1650 Mobile (4 GB VRAM).
   - *Fallback:* If `nvidia-smi` is unavailable or throttled, falls back to reporting CPU-only telemetry without interrupting the collector loop.

---

### 3. Domain Agent Expansion (Roadmap Hand-Off to Phase 4 & Phase 5)

1. **Agent Autonomy Escalation:**
   - *Current State:* Specialized agents (`SystemIntelligenceAgent`, `DevelopmentIntelligenceAgent`, `ResearchIntelligenceAgent`, `BusinessIntelligenceAgent`) are integrated with explicit `plan()` and `execute()` contracts and verified via unit tests and UI controls.
   - *Next Phase Integration:* In Phase 5 (Proactive Intelligence) and Phase 6, connect these agents into the background `AutonomyRuntime` task worker queue, allowing them to autonomously pick up planned objectives based on incoming anomaly events (e.g. `system.anomaly.cpu_spike` automatically invoking `SystemIntelligenceAgent`).

---

### 4. Summary Table of Items

| Item | Severity | Subsystem | Planned Action |
| :--- | :--- | :--- | :--- |
| `datetime.utcnow()` warnings | Low (Cosmetic) | Core / Autonomy | Standardize on `timezone.utc` in Phase 4 |
| Unclosed test file handles | Low (Cosmetic) | Legacy Tests | Wrap in `with open()` across test fixtures |
| Hyprland socket2 streaming | Medium (Enhancement) | Awareness Collector | Implement socket2 streaming in Phase 4 |
| Auto-dispatch on anomaly | Medium (Feature) | Proactive Engine | Connect EventBus to TaskOrchestrator in Phase 5 |
