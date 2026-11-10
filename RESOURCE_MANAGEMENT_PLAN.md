# GHOST RESOURCE MANAGEMENT PLAN
**Document:** `RESOURCE_MANAGEMENT_PLAN.md`  
**Status:** CANONICAL HARDWARE CONSTRAINTS & BUDGET SPECIFICATION  
**Target Platform:** Arch Linux (x86_64)  
**Host Hardware:** ~8 GB System RAM, NVIDIA GeForce GTX 1650 Mobile (4 GB VRAM), AMD iGPU, Hyprland / Wayland  
**Working Directory:** `/home/pranc/projects/ghost`  

---

## 1. Hardware Constraints & Operational Realities

| Resource | Physical Capacity | Ghost Core Budget | Ceiling Behavior / Safeguards |
|---|---|---|---|
| **System RAM** | 7.6 GB Available | **1.5 GB Max RSS** | When system RAM exceeds 88%, background collectors slow poll frequency; microjobs pause. |
| **GPU VRAM** | 4.0 GB GDDR6 (GTX 1650 Mobile) | **1.0 GB Max VRAM** | Kokoro TTS ONNX engine allocates ~600 MB VRAM. If VRAM > 3.2 GB (due to external apps/games), auto-falls back to CPU INT8. |
| **CPU (Cores)** | Multi-core x86_64 | **< 15% Average Idle** | Telemetry collectors run in non-blocking threads; no continuous tight loops. |
| **Storage / Disk** | NVMe SSD | **< 5 GB Storage Total** | SQLite WAL mode with auto-checkpointing; snapshots prune older backups after 30 days. |
| **Network** | Broadband / Wi-Fi | **Rate-Limited Outbound** | Brave CDP requests enforce 500ms pacing; IMAP polls restricted to 300s cadence. |

---

## 2. Model Execution & Zero Local Model Directive

In strict accordance with the host hardware profile and user directives:
- **Zero Local LLM Inference:**
  - Running local LLMs (Ollama, vLLM, OpenCode) on 4 GB VRAM and 8 GB RAM causes severe system memory swapping, compositor frame drops in Hyprland, and out-of-memory kernel kills (`oom-killer`).
  - **All reasoning, coding, and classification tasks are strictly routed via OmniRoute Cloud Proxy (`http://127.0.0.1:20128/v1`) using `antigravity/gemini-3.8-flash-low`.**
- **Neural TTS (Kokoro ONNX):**
  - Kokoro uses small ONNX weights (`kokoro-v0_19.onnx`, ~300 MB).
  - Uses `CUDAExecutionProvider` when available with `ArenaCfg` capped at 768 MB.
  - If CUDA libraries are missing or VRAM allocation fails, cleanly degrades to `CPUExecutionProvider` with INT8 quantization without crashing.

---

## 3. Worker Concurrency & Queue Throttling

To prevent worker swarms from consuming system resources:
1. **Concurrency Ceiling:** Max **2 concurrent background worker tasks** executing simultaneously from `JobQueue`. Additional tasks wait safely in the `pending` state in SQLite.
2. **Lease Management:** Jobs are claimed with a default lease of **300.0 seconds**. If a worker crashes or hangs, the lease expires and the job is automatically reclaimed.
3. **Exponential Backoff:** Retrying failed tasks enforces `min(300.0, 2^attempts + jitter)` seconds to prevent rapid CPU-burning retry loops.

---

## 4. Telemetry Collector Polling Cadences

| Collector | Target Metrics | Polling Interval | Overhead / Resource Footprint |
|---|---|---|---|
| **SystemCollector** | CPU %, RAM %, Disk Free, GPU Temp, VRAM | 15 seconds | < 0.2% CPU, < 5 MB RAM (procfs & NVML query) |
| **ProcessCollector**| Internal threads, MCP child processes | 30 seconds | < 0.1% CPU |
| **GitCollector** | Active repo branch, unstaged status | 60 seconds (or inotify) | < 0.2% CPU |
| **IntakePoller** | IMAP SSL inbox inspection | 300 seconds | Network I/O only |
| **WatchdogPatrol** | Full health audit sweep | 1,800 seconds (30m) | < 1% CPU burst |

---

## 5. Graceful Degradation Matrix

```
[System RAM > 85%] ──> Pause microjobs sweep; force SQLite WAL checkpoints.
[System RAM > 92%] ──> Flush in-memory conversation turn caches; alert operator.
[GPU VRAM > 80%]   ──> Unload CUDA TTS session; switch Kokoro to CPU execution.
[CPU Temp > 85°C]  ──> Throttle background demo generation; delay scheduled jobs.
[Offline / No Net] ──> OmniRoute calls queue for retry; offline DOM QA handles validation.
```
