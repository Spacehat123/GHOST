"""Objective Management for GHOST Proactive Intelligence System.

Conforms to ARCHITECTURE_TARGET.md §Layer 4 & PHASE 5 PROACTIVE INTELLIGENCE:
- Manages high-level long-term operator objectives across the 7 intelligence domains.
- Persists objectives to SQLite WAL database (data/ghost.db).
- Tracks criteria satisfaction, progress percentage, assigned domain agents, and status.
- Evaluates incoming environmental events against active objective criteria.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ghost.intelligence.objectives")


@dataclass
class Objective:
    objective_id: str
    title: str
    domain: str  # system, dev, research, personal, physical, business, creative
    description: str = ""
    status: str = "ACTIVE"  # ACTIVE, PAUSED, COMPLETED, FAILED
    priority: int = 3  # 1 (lowest) to 5 (highest)
    target_agent: str = "system_agent"
    criteria: List[str] = field(default_factory=list)
    progress: float = 0.0  # 0.0 to 1.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Objective":
        import json
        criteria_raw = row["criteria"]
        criteria = json.loads(criteria_raw) if criteria_raw else []
        return cls(
            objective_id=row["objective_id"],
            title=row["title"],
            domain=row["domain"],
            description=row["description"],
            status=row["status"],
            priority=row["priority"],
            target_agent=row["target_agent"],
            criteria=criteria,
            progress=row["progress"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class ObjectiveManager:
    """Persistent manager for high-level system and operator objectives."""

    def __init__(self, db_path: str = "data/ghost.db"):
        self.db_path = db_path
        self._mem_conn: Optional[sqlite3.Connection] = None
        if self.db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:" and self._mem_conn:
            return self._mem_conn
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _close_if_not_mem(self, conn: sqlite3.Connection) -> None:
        if self.db_path != ":memory:":
            conn.close()

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS objectives (
                        objective_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        description TEXT,
                        status TEXT NOT NULL DEFAULT 'ACTIVE',
                        priority INTEGER NOT NULL DEFAULT 3,
                        target_agent TEXT NOT NULL,
                        criteria TEXT NOT NULL DEFAULT '[]',
                        progress REAL NOT NULL DEFAULT 0.0,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_obj_status ON objectives(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_obj_domain ON objectives(domain)")
        finally:
            self._close_if_not_mem(conn)

    def create_objective(
        self,
        title: str,
        domain: str,
        target_agent: str,
        description: str = "",
        priority: int = 3,
        criteria: Optional[List[str]] = None,
        objective_id: Optional[str] = None,
    ) -> Objective:
        import json
        obj_id = objective_id or f"obj-{uuid.uuid4().hex[:10]}"
        now = time.time()
        crit = criteria or []
        obj = Objective(
            objective_id=obj_id,
            title=title,
            domain=domain,
            description=description,
            status="ACTIVE",
            priority=priority,
            target_agent=target_agent,
            criteria=crit,
            progress=0.0,
            created_at=now,
            updated_at=now,
        )

        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO objectives (
                        objective_id, title, domain, description, status,
                        priority, target_agent, criteria, progress, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        obj.objective_id,
                        obj.title,
                        obj.domain,
                        obj.description,
                        obj.status,
                        obj.priority,
                        obj.target_agent,
                        json.dumps(obj.criteria),
                        obj.progress,
                        obj.created_at,
                        obj.updated_at,
                    ),
                )
            logger.info(f"Created objective {obj_id}: '{title}' in domain '{domain}'")
            return obj
        finally:
            self._close_if_not_mem(conn)

    def get_objective(self, objective_id: str) -> Optional[Objective]:
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT * FROM objectives WHERE objective_id = ?", (objective_id,))
            row = cur.fetchone()
            return Objective.from_row(row) if row else None
        finally:
            self._close_if_not_mem(conn)

    def list_objectives(self, status: Optional[str] = None, domain: Optional[str] = None) -> List[Objective]:
        conn = self._get_connection()
        try:
            query = "SELECT * FROM objectives WHERE 1=1"
            params: List[Any] = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if domain:
                query += " AND domain = ?"
                params.append(domain)
            query += " ORDER BY priority DESC, created_at DESC"
            cur = conn.execute(query, params)
            return [Objective.from_row(r) for r in cur.fetchall()]
        finally:
            self._close_if_not_mem(conn)

    def update_progress(self, objective_id: str, progress: float, status: Optional[str] = None) -> Optional[Objective]:
        conn = self._get_connection()
        try:
            now = time.time()
            with conn:
                if status:
                    conn.execute(
                        "UPDATE objectives SET progress = ?, status = ?, updated_at = ? WHERE objective_id = ?",
                        (max(0.0, min(1.0, progress)), status, now, objective_id),
                    )
                else:
                    conn.execute(
                        "UPDATE objectives SET progress = ?, updated_at = ? WHERE objective_id = ?",
                        (max(0.0, min(1.0, progress)), now, objective_id),
                    )
            return self.get_objective(objective_id)
        finally:
            self._close_if_not_mem(conn)


# Global Singleton ObjectiveManager
_GLOBAL_OBJ_MGR: Optional[ObjectiveManager] = None

def get_objective_manager(db_path: str = "data/ghost.db") -> ObjectiveManager:
    global _GLOBAL_OBJ_MGR
    if _GLOBAL_OBJ_MGR is None:
        _GLOBAL_OBJ_MGR = ObjectiveManager(db_path=db_path)
    return _GLOBAL_OBJ_MGR
