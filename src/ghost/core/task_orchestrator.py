"""Universal Task Orchestrator for GHOST Intelligence System.

Conforms to INTERFACE_CONTRACTS.md §2:
- 11-state task lifecycle state machine.
- Distributed lease locking with heartbeats and lease reaper.
- Durable persistence in SQLite WAL tasks table.
- Automatic retry ladder and failure tracking.
- EventBus lifecycle broadcasting (task.created, task.status_changed, task.completed).
- SystemStateManager synchronization for live prompt and HUD reporting.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.core.system_state import get_system_state_manager
from ghost.store.facade import ghost_db_path

logger = logging.getLogger("ghost.core.task_orchestrator")


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


# Valid state transitions
VALID_TRANSITIONS: Dict[TaskStatus, List[TaskStatus]] = {
    TaskStatus.CREATED: [TaskStatus.PLANNED, TaskStatus.QUEUED, TaskStatus.CANCELLED],
    TaskStatus.PLANNED: [TaskStatus.QUEUED, TaskStatus.CANCELLED, TaskStatus.BLOCKED],
    TaskStatus.QUEUED: [TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.BLOCKED, TaskStatus.WAITING_FOR_APPROVAL],
    TaskStatus.RUNNING: [TaskStatus.VERIFYING, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.RETRYING, TaskStatus.CANCELLED],
    TaskStatus.VERIFYING: [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.RETRYING],
    TaskStatus.RETRYING: [TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED],
    TaskStatus.WAITING_FOR_APPROVAL: [TaskStatus.QUEUED, TaskStatus.CANCELLED, TaskStatus.BLOCKED],
    TaskStatus.BLOCKED: [TaskStatus.QUEUED, TaskStatus.CANCELLED],
    TaskStatus.COMPLETED: [],
    TaskStatus.FAILED: [],
    TaskStatus.CANCELLED: [],
}


@dataclass
class Task:
    """Canonical model for an atomic unit of execution."""
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
    error_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        return cls(
            task_id=data["task_id"],
            objective_id=data.get("objective_id", "default"),
            assigned_agent=data.get("assigned_agent", "general"),
            title=data.get("title", "Untitled Task"),
            description=data.get("description", ""),
            status=TaskStatus(data.get("status", TaskStatus.CREATED.value)),
            input_payload=data.get("input_payload") or {},
            output_payload=data.get("output_payload"),
            required_permissions=data.get("required_permissions") or [],
            attempts=int(data.get("attempts", 0)),
            max_attempts=int(data.get("max_attempts", 3)),
            timeout_s=float(data.get("timeout_s", 300.0)),
            lease_owner=data.get("lease_owner"),
            lease_until=float(data["lease_until"]) if data.get("lease_until") else None,
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            error_reason=data.get("error_reason"),
        )


class TaskOrchestrator:
    """Manages the full lifecycle, leases, retries, and persistence of tasks."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
    ):
        self.db_path = db_path or ghost_db_path()
        self.event_bus = event_bus or get_event_bus()
        self._lock = threading.Lock()

        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    objective_id TEXT NOT NULL,
                    assigned_agent TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_payload TEXT NOT NULL,
                    output_payload TEXT,
                    required_permissions TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    timeout_s REAL NOT NULL DEFAULT 300.0,
                    lease_owner TEXT,
                    lease_until REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    error_reason TEXT
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_assigned_agent ON tasks (assigned_agent);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_objective ON tasks (objective_id);")
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize tasks table at {self.db_path}: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def submit_task(
        self,
        objective_id: str,
        assigned_agent: str,
        title: str,
        description: str = "",
        input_payload: Optional[Dict[str, Any]] = None,
        required_permissions: Optional[List[str]] = None,
        max_attempts: int = 3,
        timeout_s: float = 300.0,
        initial_status: TaskStatus = TaskStatus.QUEUED,
    ) -> Task:
        """Creates, persists, and queues a new task."""
        now = time.time()
        task = Task(
            task_id=f"tsk-{uuid.uuid4().hex[:12]}",
            objective_id=objective_id,
            assigned_agent=assigned_agent,
            title=title,
            description=description,
            status=initial_status,
            input_payload=input_payload or {},
            output_payload=None,
            required_permissions=required_permissions or [],
            attempts=0,
            max_attempts=max_attempts,
            timeout_s=timeout_s,
            created_at=now,
            updated_at=now,
        )

        with self._lock:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                conn.execute(
                    """
                    INSERT INTO tasks (
                        task_id, objective_id, assigned_agent, title, description,
                        status, input_payload, output_payload, required_permissions,
                        attempts, max_attempts, timeout_s, lease_owner, lease_until,
                        created_at, updated_at, error_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        task.objective_id,
                        task.assigned_agent,
                        task.title,
                        task.description,
                        task.status.value,
                        json.dumps(task.input_payload),
                        None,
                        json.dumps(task.required_permissions),
                        task.attempts,
                        task.max_attempts,
                        task.timeout_s,
                        None,
                        None,
                        task.created_at,
                        task.updated_at,
                        None,
                    ),
                )
                conn.commit()
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

        # Broadcast lifecycle event
        try:
            self.event_bus.publish_sync(
                Event(
                    event_type="task.created",
                    source="core.task_orchestrator",
                    severity=EventSeverity.INFO,
                    payload=task.to_dict(),
                    correlation_id=task.task_id,
                )
            )
        except Exception:
            pass

        self._sync_state_counts()
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Retrieves a single task by ID."""
        with self._lock:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                conn.row_factory = sqlite3.Row
                cur = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return self._row_to_task(row)
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        agent: Optional[str] = None,
        objective_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Task]:
        """Queries tasks with optional filters."""
        clauses = ["1=1"]
        params: List[Any] = []

        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if agent:
            clauses.append("assigned_agent = ?")
            params.append(agent)
        if objective_id:
            clauses.append("objective_id = ?")
            params.append(objective_id)

        sql = f"""
            SELECT * FROM tasks
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT ?
        """
        params.append(limit)

        results: List[Task] = []
        with self._lock:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                conn.row_factory = sqlite3.Row
                cur = conn.execute(sql, params)
                for row in cur.fetchall():
                    results.append(self._row_to_task(row))
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass
        return results

    def acquire_lease(
        self,
        task_id: str,
        worker_id: str,
        lease_duration_s: float = 60.0,
    ) -> bool:
        """Atomically leases a task to a worker and transitions status to RUNNING."""
        now = time.time()
        lease_until = now + lease_duration_s

        with self._lock:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                # Task must be QUEUED or RETRYING, or lease expired
                cur = conn.execute(
                    """
                    UPDATE tasks
                    SET lease_owner = ?,
                        lease_until = ?,
                        status = ?,
                        attempts = attempts + 1,
                        updated_at = ?
                    WHERE task_id = ?
                      AND (
                           status IN ('QUEUED', 'RETRYING')
                           OR (status = 'RUNNING' AND lease_until < ?)
                      )
                    """,
                    (
                        worker_id,
                        lease_until,
                        TaskStatus.RUNNING.value,
                        now,
                        task_id,
                        now,
                    ),
                )
                conn.commit()
                acquired = cur.rowcount > 0
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

        if acquired:
            self._sync_state_counts()
            try:
                self.event_bus.publish_sync(
                    Event(
                        event_type="task.status_changed",
                        source="core.task_orchestrator",
                        severity=EventSeverity.INFO,
                        payload={
                            "task_id": task_id,
                            "new_status": TaskStatus.RUNNING.value,
                            "worker_id": worker_id,
                            "lease_until": lease_until,
                        },
                        correlation_id=task_id,
                    )
                )
            except Exception:
                pass

        return acquired

    def renew_lease(
        self,
        task_id: str,
        worker_id: str,
        lease_duration_s: float = 60.0,
    ) -> bool:
        """Extends an existing active lease held by worker_id."""
        now = time.time()
        new_lease_until = now + lease_duration_s

        with self._lock:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                cur = conn.execute(
                    """
                    UPDATE tasks
                    SET lease_until = ?, updated_at = ?
                    WHERE task_id = ? AND lease_owner = ? AND status = 'RUNNING'
                    """,
                    (new_lease_until, now, task_id, worker_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass
        return False

    def transition(
        self,
        task_id: str,
        new_status: TaskStatus,
        output_payload: Optional[Dict[str, Any]] = None,
        error_reason: Optional[str] = None,
    ) -> Task:
        """Transitions a task to a new lifecycle status."""
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found")

        # Validate transition
        allowed = VALID_TRANSITIONS.get(task.status, [])
        if new_status not in allowed:
            raise ValueError(
                f"Illegal task transition from {task.status.value} to {new_status.value} "
                f"(allowed: {[s.value for s in allowed]})"
            )

        now = time.time()
        actual_status = new_status

        # If failed and attempts remain under max_attempts, route to RETRYING
        if new_status == TaskStatus.FAILED and task.attempts < task.max_attempts:
            actual_status = TaskStatus.RETRYING

        clear_lease = actual_status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.RETRYING,
        )

        with self._lock:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?,
                        output_payload = ?,
                        error_reason = ?,
                        lease_owner = CASE WHEN ? = 1 THEN NULL ELSE lease_owner END,
                        lease_until = CASE WHEN ? = 1 THEN NULL ELSE lease_until END,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        actual_status.value,
                        json.dumps(output_payload) if output_payload is not None else (
                            json.dumps(task.output_payload) if task.output_payload else None
                        ),
                        error_reason or task.error_reason,
                        1 if clear_lease else 0,
                        1 if clear_lease else 0,
                        now,
                        task_id,
                    ),
                )
                conn.commit()
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

        updated_task = self.get_task(task_id)
        assert updated_task is not None

        self._sync_state_counts()

        try:
            self.event_bus.publish_sync(
                Event(
                    event_type=f"task.{actual_status.value.lower()}",
                    source="core.task_orchestrator",
                    severity=EventSeverity.INFO if actual_status != TaskStatus.FAILED else EventSeverity.ERROR,
                    payload=updated_task.to_dict(),
                    correlation_id=task_id,
                )
            )
        except Exception:
            pass

        return updated_task

    def reap_stale_leases(self, now: Optional[float] = None) -> List[str]:
        """Finds running tasks with expired leases and resets them to QUEUED or FAILED."""
        current_time = now or time.time()
        reaped: List[str] = []

        with self._lock:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    """
                    SELECT task_id, attempts, max_attempts FROM tasks
                    WHERE status = 'RUNNING' AND lease_until IS NOT NULL AND lease_until < ?
                    """,
                    (current_time,),
                )
                expired_rows = cur.fetchall()

                for row in expired_rows:
                    t_id = row["task_id"]
                    attempts = row["attempts"]
                    max_attempts = row["max_attempts"]

                    next_status = (
                        TaskStatus.RETRYING.value
                        if attempts < max_attempts
                        else TaskStatus.FAILED.value
                    )
                    conn.execute(
                        """
                        UPDATE tasks
                        SET status = ?, lease_owner = NULL, lease_until = NULL, updated_at = ?,
                            error_reason = 'Worker lease timed out'
                        WHERE task_id = ?
                        """,
                        (next_status, current_time, t_id),
                    )
                    reaped.append(t_id)

                if reaped:
                    conn.commit()
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

        if reaped:
            self._sync_state_counts()

        return reaped

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        return Task(
            task_id=row["task_id"],
            objective_id=row["objective_id"],
            assigned_agent=row["assigned_agent"],
            title=row["title"],
            description=row["description"],
            status=TaskStatus(row["status"]),
            input_payload=json.loads(row["input_payload"]) if row["input_payload"] else {},
            output_payload=json.loads(row["output_payload"]) if row["output_payload"] else None,
            required_permissions=json.loads(row["required_permissions"]) if row["required_permissions"] else [],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            timeout_s=row["timeout_s"],
            lease_owner=row["lease_owner"],
            lease_until=row["lease_until"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            error_reason=row["error_reason"],
        )

    def _sync_state_counts(self) -> None:
        """Updates SystemStateManager running and queue pending counters."""
        try:
            with self._lock:
                conn = None
                try:
                    conn = sqlite3.connect(self.db_path, timeout=2.0)
                    cur = conn.execute(
                        """
                        SELECT
                            SUM(CASE WHEN status = 'RUNNING' THEN 1 ELSE 0 END) as running,
                            SUM(CASE WHEN status IN ('QUEUED', 'RETRYING') THEN 1 ELSE 0 END) as pending
                        FROM tasks
                        """
                    )
                    row = cur.fetchone()
                    running = int(row[0] or 0) if row else 0
                    pending = int(row[1] or 0) if row else 0
                finally:
                    if conn:
                        try:
                            conn.close()
                        except Exception:
                            pass

            state_mgr = get_system_state_manager(bus=self.event_bus)
            state_mgr.update_tasks_count(running=running, pending=pending)
        except Exception:
            pass


# Global Singleton TaskOrchestrator
_GLOBAL_TASK_ORCHESTRATOR: Optional[TaskOrchestrator] = None


def get_task_orchestrator(
    bus: Optional[EventBus] = None,
    event_bus: Optional[EventBus] = None,
) -> TaskOrchestrator:
    """Returns the process-wide global TaskOrchestrator."""
    global _GLOBAL_TASK_ORCHESTRATOR
    actual_bus = event_bus or bus
    if _GLOBAL_TASK_ORCHESTRATOR is None:
        _GLOBAL_TASK_ORCHESTRATOR = TaskOrchestrator(event_bus=actual_bus)
    return _GLOBAL_TASK_ORCHESTRATOR


def reset_task_orchestrator(orchestrator: Optional[TaskOrchestrator] = None) -> None:
    """Testing helper to reset or inject TaskOrchestrator."""
    global _GLOBAL_TASK_ORCHESTRATOR
    _GLOBAL_TASK_ORCHESTRATOR = orchestrator
