# GHOST INTERFACE CONTRACTS & SCHEMAS
**Document:** `INTERFACE_CONTRACTS.md`  
**Status:** CANONICAL INTERFACE SPECIFICATION  
**Version:** 1.0  
**Date:** 2026-11-10  
**Working Directory:** `/home/pranc/projects/ghost`  

---

## 1. Event Infrastructure Contract (`src/ghost/core/events.py`)

Every event flowing through GHOST's perception and intelligence layers must conform to the `Event` schema:

```python
from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
import time
import uuid

class EventSeverity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

@dataclass(frozen=True)
class Event:
    event_id: str                      # Globally unique UUIDv7 (or UUIDv4)
    event_type: str                    # Dot-delimited taxonomy: domain.category.action
    source: str                        # Subsystem origin (e.g., "collector.hardware")
    timestamp: float                   # Unix epoch timestamp in seconds
    severity: EventSeverity            # Importance classification
    payload: Dict[str, Any]            # Structured validated dictionary
    correlation_id: Optional[str] = None # Trace ID linking multi-stage flows
    provenance: Optional[str] = None   # Source URI, file path, or physical device ID
    dedup_id: Optional[str] = None     # Content hash for idempotent deduplication
```

### Event Taxonomy Standards:
- `system.telemetry.sample`: Periodic CPU/RAM/GPU metrics.
- `system.resource.alert`: Threshold exceeded (CPU > 90%, VRAM > 95%).
- `dev.git.change`: File or branch modification detected in active repo.
- `dev.test.failure`: Automated test suite run failure.
- `research.source.discovered`: Verified prospect or library found.
- `business.lead.qualified`: New lead meets ICP ticket criteria.
- `physical.device.state_changed`: Verified smart-home state transition.

---

## 2. Task Orchestrator Contract (`src/ghost/core/task_orchestrator.py`)

All work executed by specialized agents or background workers is governed by the 11-state task lifecycle:

```python
class TaskStatus(str, Enum):
    CREATED = "CREATED"
    PLANNED = "PLANNED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    RETRYING = "RETRYING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"

@dataclass
class Task:
    task_id: str
    objective_id: str
    assigned_agent: str
    title: str
    description: str
    status: TaskStatus
    input_payload: Dict[str, Any]
    output_payload: Optional[Dict[str, Any]] = None
    required_permissions: List[str] = field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 3
    timeout_s: float = 300.0
    lease_owner: Optional[str] = None
    lease_until: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
```

---

## 3. Tool Definition & Registry Contract (`src/ghost/core/tool_registry.py`)

Tools must be declaratively registered with JSON schemas, permission gates, and verification hooks:

```python
from typing import Callable, Awaitable

@dataclass(frozen=True)
class ToolDefinition:
    name: str                          # Unique identifier (e.g., "build_luxury_demo")
    description: str                   # Prompt-facing explanation
    parameters_schema: Dict[str, Any]  # Valid JSON Schema object
    required_permission: str           # e.g., "demo.build", "outbound.send"
    is_side_effect: bool               # True if modifies external world state
    timeout_s: float                   # Hard execution timeout
    handler: Callable[[Dict[str, Any], Optional[str]], Awaitable[Dict[str, Any]]]
    verification_handler: Optional[Callable[[Dict[str, Any]], Awaitable[bool]]] = None
```

---

## 4. Specialized Agent Contract (`src/ghost/agents/base.py`)

Every domain intelligence agent must subclass `AgentContract`:

```python
from abc import ABC, abstractmethod

@dataclass
class VerificationEvidence:
    verified: bool
    evidence_type: str                 # "screenshot", "test_report", "dom_element", "readback"
    artifacts: List[str]               # Local file paths or URLs
    details: Dict[str, Any]

class AgentContract(ABC):
    agent_id: str
    name: str
    responsibility: str
    supported_tasks: List[str]
    required_permissions: List[str]

    @abstractmethod
    async def plan_task(self, objective: Dict[str, Any]) -> List[Task]:
        """Decomposes high-level objective into atomic tasks."""
        ...

    @abstractmethod
    async def execute_task(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        """Executes task using registered tools within authorized bounds."""
        ...

    @abstractmethod
    async def verify_outcome(self, task: Task, output: Dict[str, Any]) -> VerificationEvidence:
        """Independently verifies that the outcome exists and satisfies acceptance criteria."""
        ...
```

---

## 5. System State Contract (`src/ghost/core/system_state.py`)

Thread-safe snapshot dictionary format returned by `SystemStateManager.get_snapshot()`:

```python
@dataclass(frozen=True)
class HardwareVitals:
    cpu_percent: float
    ram_used_mb: float
    ram_total_mb: float
    ram_percent: float
    disk_free_gb: float
    gpu_utilization_percent: Optional[float]
    gpu_temp_celsius: Optional[float]
    gpu_vram_used_mb: Optional[float]
    gpu_vram_total_mb: Optional[float]

@dataclass(frozen=True)
class StateSnapshot:
    timestamp: float
    vitals: HardwareVitals
    focused_window: Optional[str]
    active_workspace: Optional[int]
    active_agents: List[str]
    running_tasks: int
    queue_pending: int
    halt_active: bool
    integrations_healthy: Dict[str, bool]
```

---

## 6. Frontend / Backend API & WebSocket Contracts (`src/ghost/server/app.py`)

### 6.1 WebSocket Event Stream (`/ws/events`)
All connected desktop GUI clients receive real-time updates via JSON envelopes:

```json
{
  "type": "universal_event",
  "event": {
    "event_id": "018f4a21-7000-7000-8000-000000000001",
    "event_type": "system.telemetry.sample",
    "source": "collector.system",
    "timestamp": 1731234567.89,
    "severity": "INFO",
    "payload": {
      "cpu_percent": 12.4,
      "ram_percent": 48.2,
      "gpu_percent": 5.0,
      "vram_used_mb": 412
    },
    "correlation_id": null,
    "provenance": null,
    "dedup_id": null
  }
}
```

### 6.2 REST Endpoints

1. **`GET /api/awareness/telemetry`**
   - Returns: `{ "vitals": HardwareVitals, "git": { "branch": str, "uncommitted": int }, "active_window": str }`
2. **`GET /api/agents`**
   - Returns: `{ "status": "ok", "agents": [ { "agent_id": str, "name": str, "responsibility": str, "supported_tasks": [...], "required_permissions": [...] } ] }`
3. **`POST /api/agents/{id}/plan`**
   - Payload: `{ "goal": str, "params": { ... } }`
   - Returns: `{ "status": "ok", "agent_id": str, "plan": [ Task, ... ] }`
4. **`POST /api/agents/{id}/execute`**
   - Payload: `{ "task": Task, "context": { ... } }`
   - Returns: `{ "status": "ok", "agent_id": str, "result": { ... }, "evidence": VerificationEvidence }`
5. **`GET /api/tasks` & `POST /api/tasks`**
   - Returns list of tasks in 11-state lifecycle or submits a new task to `TaskOrchestrator`.
6. **`POST /api/tasks/{id}/cancel`**
   - Transitions task immediately to `CANCELLED` and emits `task.cancelled` event.
7. **`GET /api/tools`**
   - Returns: `{ "status": "ok", "count": 49, "tools": [ { "name": str, "description": str, "is_side_effect": bool, "timeout_s": float, "schema": { ... } } ] }`

