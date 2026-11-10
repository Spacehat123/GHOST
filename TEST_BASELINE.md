# GHOST TEST SUITE BASELINE REPORT
**Audit Task:** GHOST TASK 001 — Repository Audit & Cleanup Plan  
**Status:** READ-ONLY EVIDENCE-BASED BASELINE  
**Execution Timestamp:** 2026-11-10T14:28:16+05:30  
**Environment:** Linux x86_64 / Python 3.14.0rc3 / Virtualenv `/home/pranc/.venvs/ghost-gpu`  
**Working Directory:** `/home/pranc/projects/ghost`  
**Execution Command:** `PYTHONPATH=src /home/pranc/.venvs/ghost-gpu/bin/python -m unittest discover -s tests`  

---

## 1. Executive Summary

| Metric | Result |
|---|---|
| **Total Tests Discovered** | **773** |
| **Tests Passed** | **771 (99.74%)** |
| **Tests Failed** | **2 (0.26%)** |
| **Tests Errored** | **0** |
| **Tests Skipped** | **0** |
| **Execution Duration** | **160.255 seconds (2m 40s)** |
| **Overall Status** | **FAILED (failures=2)** |

In strict accordance with the GHOST Engineering Contract, zero test or production files were modified to force tests to pass during this audit.

---

## 2. Detailed Failure Analysis

Both failures occurred in a single test module: [`tests/test_agent_loop.py`](file:///home/pranc/projects/ghost/tests/test_agent_loop.py).

### Failure 1: Model Rounds Ceiling Assertion
- **Test:** `TestAgentLoop.test_bounded_model_rounds_ceiling`
- **Location:** [`tests/test_agent_loop.py:80`](file:///home/pranc/projects/ghost/tests/test_agent_loop.py#L80)
- **Failure Trace:**
  ```python
  Traceback (most recent call last):
    File "/usr/lib/python3.14/unittest/mock.py", line 1456, in patched
      return await func(*newargs, **newkeywargs)
    File "/home/pranc/projects/ghost/tests/test_agent_loop.py", line 80, in test_bounded_model_rounds_ceiling
      self.assertEqual(mock_client.chat.completions.create.call_count, 4)
  AssertionError: 5 != 4
  ```
- **Root Cause Analysis:**
  The test verifies that execution stops when the maximum model rounds limit is hit. In `GhostAgent.chat_turn()`, the loop executed 5 times instead of 4 because of the recent circuit-breaker guard enhancement that allows 1 initial prompt turn plus 4 tool-turn retries before halting. The test assertion expected `call_count == 4`.

---

### Failure 2: Empty Spoken Output on Tool Execution Ceiling
- **Test:** `TestAgentLoop.test_bounded_tool_executions_ceiling`
- **Location:** [`tests/test_agent_loop.py:121`](file:///home/pranc/projects/ghost/tests/test_agent_loop.py#L121)
- **Failure Trace:**
  ```python
  Traceback (most recent call last):
    File "/usr/lib/python3.14/unittest/mock.py", line 1456, in patched
      return await func(*newargs, **newkeywargs)
    File "/home/pranc/projects/ghost/tests/test_agent_loop.py", line 121, in test_bounded_tool_executions_ceiling
      self.assertIn("Remaining work", "".join(spoken_tokens))
  AssertionError: 'Remaining work' not found in ''
  ```
- **Root Cause Analysis:**
  When tool execution reaches the safety ceiling, `GhostAgent` halts tool dispatching. However, in interactive chat turns where `on_token` streaming callback is provided, the final synthetic spoken string was buffered rather than streamed through `on_token`, resulting in `"".join(spoken_tokens)` returning an empty string `''` in the test assertion.

---

## 3. Runtime Warnings & Resource Leaks

During test suite execution, Python 3.14 emitted multiple resource warnings that indicate unclosed file handles and SQLite connections:

### 3.1 Unclosed SQLite Database Connections (40+ instances)
- **Warning:** `ResourceWarning: unclosed database in <sqlite3.Connection object at 0x...>`
- **Locations:**
  - `src/ghost/store/schema.py:227`
  - `src/ghost/server/agency_api.py:541`
  - Multiple test setup/teardown methods where connection objects were created without context managers (`contextlib.closing(conn)` or explicit `.close()`).
- **Remediation:** Ensure all SQLite connections in production code and test helpers are wrapped in `with closing(...)` or explicit `conn.close()`.

### 3.2 Unclosed File Descriptors (15+ instances)
- **Warning:** `ResourceWarning: unclosed file <_io.TextIOWrapper name='...'>`
- **Locations:**
  - Direct calls to `json.load(open(path))` in:
    - `src/ghost/server/agency_api.py:541` (`open(qa_path)`)
    - `tests/test_section_kit.py:221,263`
    - `tests/test_wiring5_voice.py:37,46,59`
    - `tests/test_t10_darkrun.py:99,117`
    - `tests/test_collect_only.py:165`
- **Remediation:** Replace bare `open(...)` calls with `with open(...) as f:` context managers.

### 3.3 Deprecation Warnings
1. **FastAPI Lifespan Events:**
   - Location: [`src/ghost/server/app.py:69,93`](file:///home/pranc/projects/ghost/src/ghost/server/app.py#L69-L93)
   - Message: `DeprecationWarning: on_event is deprecated, use lifespan event handlers instead.`
   - Impact: In future versions of FastAPI/Starlette, `@app.on_event` will be removed.
2. **Deprecated UTC Datetime:**
   - Location: [`tests/test_wiring3_sources.py:102`](file:///home/pranc/projects/ghost/tests/test_wiring3_sources.py#L102)
   - Message: `DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects: datetime.datetime.now(datetime.UTC).`

---

## 4. Test Execution Invariants & Requirements

1. **`PYTHONPATH=src` Requirement:**
   - The test suite **cannot** be executed using bare `python3 -m unittest` because the packages under `src/` are not on `sys.path`.
   - Running `python3 -m unittest` discovers **0 tests**.
   - Running `PYTHONPATH=src python3 -m unittest discover -s tests` correctly discovers all 773 tests.
2. **Brave CDP & Headless Fallback Invariant:**
   - During Phase 4 QA tests (`test_section_kit.py`, `test_p3_demo_loop.py`), tests spawn headless Brave browser processes to capture real screenshots.
   - The recently added offline DOM QA fallback ensures tests pass cleanly even if Brave CDP on port 9222 is down.
3. **Kokoro TTS CUDA Driver Fallback:**
   - Tests in `test_kokoro_voice.py` attempt to initialize Kokoro with CUDA.
   - If the NVIDIA CUDA driver is not directly bound in the execution shell, Kokoro logs: `WARNING: CUDA session failed (CUDA driver library missing); falling back to CPU`, and cleanly passes all audio synthesis assertions on CPU.
