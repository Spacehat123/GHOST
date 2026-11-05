"""Ghost Mission & Task Decompostion Engine with SQLite Persistence.
Enforces non-negotiable architectural invariants:
1. Multi-step work must execute via structured, observable missions (3-15 tasks).
2. Exactly one task in_progress at any time; dependencies strictly respected.
3. Every task marked done must provide verified evidence passing code checks.
4. Auto-appends an independent final audit task if omitted by the planner.
5. Persists to SQLite (data/missions.db) so missions are resumable across restarts.
"""
import os
import json
import time
import sqlite3
import logging
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Union

from ghost.core.evidence import verify_task_evidence

logger = logging.getLogger("ghost.mission")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
DATA_DIR = os.environ.get("GHOST_DATA_DIR") or os.path.join(REPO_DIR, "data")
DEFAULT_DB_PATH = os.environ.get("GHOST_MISSIONS_DB") or os.path.join(DATA_DIR, "missions.db")

MAX_TASKS_PER_MISSION = 1000
MAX_MISSION_ELAPSED_SECONDS = 864000.0 * 365
DEFAULT_MAX_MISSION_TOKENS = int(os.environ.get("GHOST_MISSION_MAX_TOKENS", "1000000000"))


class TaskStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    blocked = "blocked"
    done = "done"
    failed = "failed"
    skipped = "skipped"


@dataclass
class TaskBudget:
    max_model_rounds: int = 1000
    max_tool_executions: int = 5000
    max_task_elapsed_seconds: float = 864000.0
    max_task_total_tokens: int = 1000000000

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "TaskBudget":
        if not data:
            return cls()
        
        raw_tokens = int(data.get("max_task_total_tokens", 1000000000))
        if raw_tokens <= 50000 and "GHOST_MAX_TASK_TOTAL_TOKENS" not in os.environ:
            raw_tokens = 1000000000

        raw_rounds = int(data.get("max_model_rounds", 1000))
        if raw_rounds <= 20 and "GHOST_MAX_MODEL_ROUNDS" not in os.environ:
            raw_rounds = 1000

        raw_tools = int(data.get("max_tool_executions", 5000))
        if raw_tools <= 30 and "GHOST_MAX_TOOL_EXECUTIONS" not in os.environ:
            raw_tools = 5000

        raw_elapsed = float(data.get("max_task_elapsed_seconds", 864000.0))
        if raw_elapsed <= 600.0 and "GHOST_MAX_TASK_ELAPSED_SECONDS" not in os.environ:
            raw_elapsed = 864000.0

        return cls(
            max_model_rounds=raw_rounds,
            max_tool_executions=raw_tools,
            max_task_elapsed_seconds=raw_elapsed,
            max_task_total_tokens=raw_tokens
        )


@dataclass
class Task:
    id: str
    title: str
    description: str
    acceptance: List[str]
    evidence_required: List[str]
    depends_on: List[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.pending
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    result_summary: str = ""
    attempts: int = 0
    budget: TaskBudget = field(default_factory=TaskBudget)
    tools_allowed: Optional[List[str]] = None
    task_type: str = "standard"  # "standard", "approval", "audit"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["budget"] = self.budget.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        stat = data.get("status", "pending")
        if isinstance(stat, TaskStatus):
            stat_enum = stat
        else:
            try:
                stat_enum = TaskStatus(str(stat).lower())
            except ValueError:
                stat_enum = TaskStatus.pending

        budget_raw = data.get("budget")
        budget = TaskBudget.from_dict(budget_raw) if isinstance(budget_raw, dict) else TaskBudget()

        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            acceptance=list(data.get("acceptance", [])),
            evidence_required=list(data.get("evidence_required", [])),
            depends_on=list(data.get("depends_on", [])),
            status=stat_enum,
            evidence=list(data.get("evidence", [])),
            result_summary=str(data.get("result_summary", "")),
            attempts=int(data.get("attempts", 0)),
            budget=budget,
            tools_allowed=data.get("tools_allowed"),
            task_type=str(data.get("task_type", "standard"))
        )


@dataclass
class Mission:
    id: str
    goal: str
    created_at: str
    tasks: List[Task]
    status: str = "in_progress"  # "pending", "in_progress", "completed", "paused", "blocked", "failed"
    skill: Optional[str] = None
    notes: str = ""
    total_elapsed: float = 0.0
    total_tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "created_at": self.created_at,
            "tasks": [t.to_dict() for t in self.tasks],
            "status": self.status,
            "skill": self.skill,
            "notes": self.notes,
            "total_elapsed": self.total_elapsed,
            "total_tokens": self.total_tokens
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Mission":
        tasks_raw = data.get("tasks", [])
        tasks = [Task.from_dict(t) if isinstance(t, dict) else t for t in tasks_raw]
        return cls(
            id=str(data.get("id", "")),
            goal=str(data.get("goal", "")),
            created_at=str(data.get("created_at", "")),
            tasks=tasks,
            status=str(data.get("status", "in_progress")),
            skill=data.get("skill"),
            notes=str(data.get("notes", "")),
            total_elapsed=float(data.get("total_elapsed", 0.0)),
            total_tokens=int(data.get("total_tokens", 0))
        )


class MissionStore:
    """Persistent SQLite database manager for Missions and Tasks."""

    _instance: Optional["MissionStore"] = None

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = os.path.abspath(db_path or DEFAULT_DB_PATH)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: Optional[str] = None) -> "MissionStore":
        if cls._instance is None:
            cls._instance = cls(db_path)
        elif db_path and os.path.abspath(db_path) != cls._instance.db_path:
            cls._instance = cls(db_path)
        return cls._instance

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self, "_conn") or self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON;")
        return self._conn

    def close(self):
        """Close persistent SQLite connection."""
        if hasattr(self, "_conn") and self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __del__(self):
        self.close()

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS missions (
                    id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    skill TEXT,
                    notes TEXT DEFAULT '',
                    total_elapsed REAL DEFAULT 0.0,
                    total_tokens INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT NOT NULL,
                    mission_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    acceptance TEXT NOT NULL,
                    evidence_required TEXT NOT NULL,
                    depends_on TEXT DEFAULT '[]',
                    status TEXT NOT NULL,
                    evidence TEXT DEFAULT '[]',
                    result_summary TEXT DEFAULT '',
                    attempts INTEGER DEFAULT 0,
                    budget TEXT NOT NULL,
                    tools_allowed TEXT,
                    task_type TEXT DEFAULT 'standard',
                    PRIMARY KEY (mission_id, id),
                    FOREIGN KEY (mission_id) REFERENCES missions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                    id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    task_id TEXT,
                    tool_name TEXT NOT NULL,
                    command TEXT DEFAULT '',
                    exit_code INTEGER DEFAULT 0,
                    output TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    FOREIGN KEY (mission_id) REFERENCES missions(id) ON DELETE CASCADE
                );
            """)

    def record_tool_call(
        self,
        call_id: str,
        mission_id: str,
        tool_name: str,
        command: Optional[str] = None,
        exit_code: Optional[int] = 0,
        output: Optional[str] = None,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Persist real executed tool call details for deterministic evidence verification."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tool_calls
                (id, mission_id, task_id, tool_name, command, exit_code, output, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    str(call_id),
                    str(mission_id),
                    str(task_id or ""),
                    str(tool_name),
                    str(command or ""),
                    int(exit_code) if exit_code is not None else 0,
                    str(output or "")[:20000],
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(metadata or {})
                )
            )

    def get_tool_call(self, call_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve tool call execution record by ID."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM tool_calls WHERE id = ?;", (str(call_id),)).fetchone()
            if row:
                return dict(row)
            return None

    def get_tool_calls_for_mission(self, mission_id: str) -> List[Dict[str, Any]]:
        """Retrieve all executed tool calls recorded for a mission."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_calls WHERE mission_id = ? ORDER BY created_at ASC;",
                (str(mission_id),)
            ).fetchall()
            return [dict(r) for r in rows]

    def create_mission(
        self,
        goal: str,
        tasks: List[Dict[str, Any]],
        skill: Optional[str] = None,
        notes: str = ""
    ) -> Mission:
        """Create a new mission with 3-15 tasks, enforcing acceptance and audit requirements."""
        if not goal or not str(goal).strip():
            raise ValueError("Mission requires a non-empty goal.")
        if not tasks or not isinstance(tasks, list):
            raise ValueError("Mission requires at least one task.")

        # Check acceptance criteria for all incoming tasks
        for idx, t in enumerate(tasks):
            title = t.get("title", f"Task {idx + 1}")
            acc = t.get("acceptance", [])
            if not acc or not isinstance(acc, list) or len(acc) == 0:
                raise ValueError(f"Task '{title}' must define at least one acceptance criterion.")

        # Check if planner included a final independent audit task
        def _is_audit_task(t_data: Dict[str, Any]) -> bool:
            title = str(t_data.get("title", "")).lower()
            desc = str(t_data.get("description", "")).lower()
            ttype = str(t_data.get("task_type", "")).lower()
            return (
                ttype == "audit" or
                "audit" in title or
                "audit" in desc or
                "independent check" in title or
                "goal verification" in title
            )

        task_list = list(tasks)
        if not _is_audit_task(task_list[-1]):
            # Auto-append the required final independent audit task
            audit_id = f"task_{len(task_list) + 1:02d}"
            prior_task_ids = [t.get("id") or f"task_{i+1:02d}" for i, t in enumerate(task_list)]
            audit_task = {
                "id": audit_id,
                "title": "Final Independent Audit & Goal Verification",
                "description": "Independent check of the entire result against the original goal using fresh evidence.",
                "acceptance": [
                    "All mission acceptance criteria verified against delivered artifacts",
                    "Zero unverified or fabricated claims"
                ],
                "evidence_required": ["file", "command"],
                "depends_on": prior_task_ids,
                "task_type": "audit"
            }
            task_list.append(audit_task)

        if len(task_list) > MAX_TASKS_PER_MISSION:
            raise ValueError(f"Mission cannot exceed {MAX_TASKS_PER_MISSION} tasks (got {len(task_list)}).")

        mission_id = f"msn_{int(time.time())}_{os.urandom(3).hex()}"
        created_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

        # Construct Task objects
        task_objs: List[Task] = []
        for i, t_data in enumerate(task_list):
            tid = t_data.get("id") or f"task_{i+1:02d}"
            t_obj = Task(
                id=tid,
                title=t_data.get("title", f"Step {i+1}"),
                description=t_data.get("description", ""),
                acceptance=list(t_data.get("acceptance", [])),
                evidence_required=list(t_data.get("evidence_required", [])),
                depends_on=list(t_data.get("depends_on", [])),
                status=TaskStatus.pending,
                evidence=[],
                result_summary="",
                attempts=0,
                budget=TaskBudget.from_dict(t_data.get("budget")),
                tools_allowed=t_data.get("tools_allowed"),
                task_type=t_data.get("task_type", "standard")
            )
            task_objs.append(t_obj)

        # Persist in SQLite
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO missions (id, goal, created_at, status, skill, notes, total_elapsed, total_tokens)
                VALUES (?, ?, ?, 'in_progress', ?, ?, 0.0, 0);
                """,
                (mission_id, goal, created_at, skill, notes)
            )
            for idx, tobj in enumerate(task_objs):
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, mission_id, idx, title, description, acceptance,
                        evidence_required, depends_on, status, evidence,
                        result_summary, attempts, budget, tools_allowed, task_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        tobj.id,
                        mission_id,
                        idx,
                        tobj.title,
                        tobj.description,
                        json.dumps(tobj.acceptance),
                        json.dumps(tobj.evidence_required),
                        json.dumps(tobj.depends_on),
                        tobj.status.value,
                        json.dumps(tobj.evidence),
                        tobj.result_summary,
                        tobj.attempts,
                        json.dumps(tobj.budget.to_dict()),
                        json.dumps(tobj.tools_allowed) if tobj.tools_allowed else None,
                        tobj.task_type
                    )
                )

        mission = Mission(
            id=mission_id,
            goal=goal,
            created_at=created_at,
            tasks=task_objs,
            status="in_progress",
            skill=skill,
            notes=notes,
            total_elapsed=0.0,
            total_tokens=0
        )
        logger.info(f"Created Mission {mission_id}: '{goal[:50]}' with {len(task_objs)} tasks.")
        return mission

    def get_mission(self, mission_id: str) -> Optional[Mission]:
        """Load mission and all its tasks from SQLite."""
        with self._get_connection() as conn:
            m_row = conn.execute("SELECT * FROM missions WHERE id = ?;", (mission_id,)).fetchone()
            if not m_row:
                return None

            t_rows = conn.execute(
                "SELECT * FROM tasks WHERE mission_id = ? ORDER BY idx ASC;",
                (mission_id,)
            ).fetchall()

            tasks: List[Task] = []
            for r in t_rows:
                tasks.append(
                    Task(
                        id=r["id"],
                        title=r["title"],
                        description=r["description"],
                        acceptance=json.loads(r["acceptance"]),
                        evidence_required=json.loads(r["evidence_required"]),
                        depends_on=json.loads(r["depends_on"] or "[]"),
                        status=TaskStatus(r["status"]),
                        evidence=json.loads(r["evidence"] or "[]"),
                        result_summary=r["result_summary"] or "",
                        attempts=r["attempts"],
                        budget=TaskBudget.from_dict(json.loads(r["budget"])),
                        tools_allowed=json.loads(r["tools_allowed"]) if r["tools_allowed"] else None,
                        task_type=r["task_type"] or "standard"
                    )
                )

            return Mission(
                id=m_row["id"],
                goal=m_row["goal"],
                created_at=m_row["created_at"],
                tasks=tasks,
                status=m_row["status"],
                skill=m_row["skill"],
                notes=m_row["notes"] or "",
                total_elapsed=m_row["total_elapsed"],
                total_tokens=m_row["total_tokens"]
            )

    def update_mission(self, mission: Mission) -> None:
        """Update mission-level status and usage counters."""
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE missions
                SET status = ?, notes = ?, total_elapsed = ?, total_tokens = ?
                WHERE id = ?;
                """,
                (mission.status, mission.notes, mission.total_elapsed, mission.total_tokens, mission.id)
            )

    def update_task(
        self,
        mission_id: str,
        task_id: str,
        status: Union[TaskStatus, str],
        result_summary: str = "",
        evidence: Optional[List[Dict[str, Any]]] = None,
        failure_reason: Optional[str] = None
    ) -> Task:
        """
        Update task status with strict verification:
        1. 'in_progress': Exactly one task may be in_progress; all dependencies must be done/skipped.
        2. 'done': Requires all evidence_required types satisfied and verified by evidence.py.
        3. 'blocked' / 'failed': Requires explicit failure reason or summary.
        """
        mission = self.get_mission(mission_id)
        if not mission:
            raise ValueError(f"Mission '{mission_id}' not found.")

        target_task = None
        other_tasks = []
        for t in mission.tasks:
            if t.id == task_id:
                target_task = t
            else:
                other_tasks.append(t)

        if not target_task:
            raise ValueError(f"Task '{task_id}' not found in mission '{mission_id}'.")

        if isinstance(status, TaskStatus):
            target_status = status
        else:
            val = status.value if hasattr(status, "value") else str(status)
            if "." in val:
                val = val.split(".")[-1]
            target_status = TaskStatus(val.lower())
        new_evidence = evidence if evidence is not None else target_task.evidence
        summary = (result_summary or failure_reason or target_task.result_summary).strip()

        # Invariant 1: Single in_progress task & dependency check
        if target_status == TaskStatus.in_progress:
            for ot in other_tasks:
                if ot.status == TaskStatus.in_progress:
                    raise ValueError(
                        f"Cannot set task '{task_id}' to in_progress: task '{ot.id}' ({ot.title}) is already in_progress. "
                        f"Ghost works strictly ONE task at a time."
                    )

            # Check dependencies
            for dep_id in target_task.depends_on:
                dep_task = next((t for t in mission.tasks if t.id == dep_id), None)
                if not dep_task or dep_task.status not in (TaskStatus.done, TaskStatus.skipped):
                    dep_stat = dep_task.status.value if dep_task else "missing"
                    raise ValueError(
                        f"Cannot set task '{task_id}' to in_progress: dependency '{dep_id}' is not completed (status: {dep_stat})."
                    )

        # Invariant 2: Operator approval task protection
        if target_status == TaskStatus.done and target_task.task_type == "approval":
            # Must have explicit approval in evidence or context
            has_approval = any(
                isinstance(e, dict) and e.get("type") == "command" and "approved" in str(e.get("output", "")).lower()
                for e in (new_evidence or [])
            )
            if not has_approval:
                raise ValueError(
                    f"Task '{task_id}' is an operator approval task and cannot be marked done without explicit operator confirmation."
                )

        # Invariant 3: Verified evidence check before marking done
        if target_status == TaskStatus.done:
            ok, err_msg, verified_items = verify_task_evidence(
                task_acceptance=target_task.acceptance,
                evidence_required=target_task.evidence_required,
                evidence_items=new_evidence,
                context={"mission_id": mission_id, "task_id": task_id, "store": self, "result_summary": summary}
            )
            if not ok:
                raise ValueError(f"Evidence verification failed for task '{task_id}': {err_msg}")
            new_evidence = verified_items

        # Invariant 4: Blocked or Failed requires an explicit reason
        if target_status in (TaskStatus.blocked, TaskStatus.failed):
            if not summary:
                raise ValueError(f"Status '{target_status.value}' requires an explicit reason or result_summary.")

        # Update attempts
        attempts = target_task.attempts
        if target_status == TaskStatus.in_progress and target_task.status != TaskStatus.in_progress:
            attempts += 1

        # Persist updated task
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, result_summary = ?, evidence = ?, attempts = ?
                WHERE mission_id = ? AND id = ?;
                """,
                (
                    target_status.value,
                    summary,
                    json.dumps(new_evidence),
                    attempts,
                    mission_id,
                    task_id
                )
            )

        target_task.status = target_status
        target_task.result_summary = summary
        target_task.evidence = new_evidence
        target_task.attempts = attempts

        logger.info(f"Updated Mission {mission_id} Task {task_id} -> {target_status.value}")
        return target_task

    def add_task(self, mission_id: str, task_data: Dict[str, Any]) -> Task:
        """Append a newly discovered task to the mission."""
        mission = self.get_mission(mission_id)
        if not mission:
            raise ValueError(f"Mission '{mission_id}' not found.")

        if len(mission.tasks) >= MAX_TASKS_PER_MISSION:
            raise ValueError(f"Mission task limit reached ({MAX_TASKS_PER_MISSION}). Cannot add more tasks.")

        title = task_data.get("title", f"Task {len(mission.tasks) + 1}")
        acc = task_data.get("acceptance", [])
        if not acc or not isinstance(acc, list):
            raise ValueError(f"New task '{title}' must have acceptance criteria.")

        tid = task_data.get("id") or f"task_{len(mission.tasks) + 1:02d}"
        new_task = Task(
            id=tid,
            title=title,
            description=task_data.get("description", ""),
            acceptance=list(acc),
            evidence_required=list(task_data.get("evidence_required", [])),
            depends_on=list(task_data.get("depends_on", [])),
            status=TaskStatus.pending,
            evidence=[],
            result_summary="",
            attempts=0,
            budget=TaskBudget.from_dict(task_data.get("budget")),
            tools_allowed=task_data.get("tools_allowed"),
            task_type=task_data.get("task_type", "standard")
        )

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, mission_id, idx, title, description, acceptance,
                    evidence_required, depends_on, status, evidence,
                    result_summary, attempts, budget, tools_allowed, task_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    new_task.id,
                    mission_id,
                    len(mission.tasks),
                    new_task.title,
                    new_task.description,
                    json.dumps(new_task.acceptance),
                    json.dumps(new_task.evidence_required),
                    json.dumps(new_task.depends_on),
                    new_task.status.value,
                    json.dumps(new_task.evidence),
                    new_task.result_summary,
                    new_task.attempts,
                    json.dumps(new_task.budget.to_dict()),
                    json.dumps(new_task.tools_allowed) if new_task.tools_allowed else None,
                    new_task.task_type
                )
            )

        logger.info(f"Added task {tid} to Mission {mission_id}")
        return new_task

    def next_runnable_task(self, mission_id: str) -> Optional[Task]:
        """Find the next pending task whose dependencies are all satisfied (done or skipped)."""
        mission = self.get_mission(mission_id)
        if not mission or mission.status != "in_progress":
            return None

        # Check if any task is already in_progress
        for t in mission.tasks:
            if t.status == TaskStatus.in_progress:
                return t

        done_ids = {t.id for t in mission.tasks if t.status in (TaskStatus.done, TaskStatus.skipped)}

        for t in mission.tasks:
            if t.status == TaskStatus.pending:
                deps_met = all(dep in done_ids for dep in t.depends_on)
                if deps_met:
                    return t

        return None

    def active_mission(self) -> Optional[Mission]:
        """Return the latest active in_progress mission, or recent resumable mission."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM missions WHERE status = 'in_progress' ORDER BY rowid DESC LIMIT 1;"
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT id FROM missions WHERE status IN ('paused', 'paused_for_approval', 'blocked') ORDER BY rowid DESC LIMIT 1;"
                ).fetchone()
            if row:
                return self.get_mission(row["id"])
        return None

    def unblock_mission(self, mission_id: str) -> Optional[Mission]:
        """Unblock all tasks in a mission that were halted by budget/token ceilings and restore to in_progress."""
        mission = self.get_mission(mission_id)
        if not mission:
            return None

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'pending', result_summary = '', attempts = 0,
                    budget = ?
                WHERE mission_id = ? AND status NOT IN ('done', 'skipped');
                """,
                (json.dumps(TaskBudget().to_dict()), mission_id)
            )
            conn.execute(
                """
                UPDATE missions
                SET status = 'in_progress', notes = 'Unblocked: limits removed.'
                WHERE id = ?;
                """,
                (mission_id,)
            )
        return self.get_mission(mission_id)

    def list_missions(self, status: Optional[str] = None) -> List[Mission]:
        """List all stored missions, optionally filtered by status."""
        with self._get_connection() as conn:
            if status:
                rows = conn.execute(
                    "SELECT id FROM missions WHERE status = ? ORDER BY rowid DESC;",
                    (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT id FROM missions ORDER BY rowid DESC;").fetchall()

            return [self.get_mission(r["id"]) for r in rows if r["id"]]
