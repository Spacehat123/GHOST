"""Personal Intelligence & Persistent Memory System for GHOST.

Implements 6 connected memory categories:
1. Personal Profile: relatively stable facts and preferences (hardware, communication style, long-term goals).
2. Episodic Memory: significant previous interactions and experiences.
3. Project Memory: objectives, decisions, artifacts, progress and blockers.
4. Procedural Memory: how user prefers tasks to be performed (e.g. stock analysis depth, coding styles).
5. Knowledge Memory: information and research accumulated over time.
6. Goal Memory: short-term and long-term objectives.

Each memory supports:
- Source and provenance tracking
- Creation and last-confirmed timestamps
- Confidence and relevance scoring
- Inferred vs confirmed distinction
- Expiration or review where appropriate
- Full correction, confirmation, and deletion
- Retrieval for central LLM reasoning with influence attribution
"""

import os
import json
import time
import uuid
import sqlite3
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("ghost.personal_intelligence")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
DATA_DIR = os.path.join(REPO_DIR, "data")
DEFAULT_DB_PATH = os.environ.get("GHOST_PERSONAL_DB", os.path.join(DATA_DIR, "personal_intelligence.db"))

VALID_CATEGORIES = ("profile", "episodic", "project", "procedural", "knowledge", "goal")


@dataclass
class PersonalMemory:
    id: str
    category: str  # profile | episodic | project | procedural | knowledge | goal
    subject: str
    content: str
    source: str = "user_confirmed"  # user_confirmed | system_inferred | user_prompt | observation
    confidence: float = 1.0  # 0.0 to 1.0
    created_at: float = field(default_factory=time.time)
    last_confirmed: float = field(default_factory=time.time)
    relevance: float = 1.0
    expires_at: Optional[float] = None
    is_inferred: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    influence_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "subject": self.subject,
            "content": self.content,
            "source": self.source,
            "confidence": round(self.confidence, 3),
            "created_at": self.created_at,
            "last_confirmed": self.last_confirmed,
            "relevance": round(self.relevance, 3),
            "expires_at": self.expires_at,
            "is_inferred": self.is_inferred,
            "metadata": self.metadata,
            "influence_count": self.influence_count,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PersonalMemory":
        meta = {}
        if row["metadata_json"]:
            try:
                meta = json.loads(row["metadata_json"])
            except Exception:
                meta = {}
        return cls(
            id=row["id"],
            category=row["category"],
            subject=row["subject"],
            content=row["content"],
            source=row["source"],
            confidence=float(row["confidence"]),
            created_at=float(row["created_at"]),
            last_confirmed=float(row["last_confirmed"]),
            relevance=float(row["relevance"]),
            expires_at=float(row["expires_at"]) if row["expires_at"] is not None else None,
            is_inferred=bool(row["is_inferred"]),
            metadata=meta,
            influence_count=int(row["influence_count"] or 0),
        )


class PersonalIntelligenceStore:
    """Persistent SQLite-backed Personal Intelligence Store with FTS5 search."""

    _instance: Optional["PersonalIntelligenceStore"] = None

    @classmethod
    def get_instance(cls, db_path: Optional[str] = None) -> "PersonalIntelligenceStore":
        if cls._instance is None or (db_path and cls._instance.db_path != db_path):
            cls._instance = cls(db_path=db_path)
        return cls._instance

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        self._seed_initial_intelligence()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS personal_memories (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    created_at REAL NOT NULL,
                    last_confirmed REAL NOT NULL,
                    relevance REAL NOT NULL DEFAULT 1.0,
                    expires_at REAL,
                    is_inferred INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT,
                    influence_count INTEGER NOT NULL DEFAULT 0
                )
                """)
                # FTS5 virtual table for full-text BM25 ranking across subject and content
                conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS personal_memories_fts USING fts5(
                    id, category, subject, content,
                    content='personal_memories', content_rowid='rowid'
                )
                """)
                # Triggers to keep FTS index synchronized
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS pm_ai AFTER INSERT ON personal_memories BEGIN
                    INSERT INTO personal_memories_fts(rowid, id, category, subject, content)
                    VALUES (new.rowid, new.id, new.category, new.subject, new.content);
                END;
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS pm_ad AFTER DELETE ON personal_memories BEGIN
                    INSERT INTO personal_memories_fts(personal_memories_fts, rowid, id, category, subject, content)
                    VALUES('delete', old.rowid, old.id, old.category, old.subject, old.content);
                END;
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS pm_au AFTER UPDATE ON personal_memories BEGIN
                    INSERT INTO personal_memories_fts(personal_memories_fts, rowid, id, category, subject, content)
                    VALUES('delete', old.rowid, old.id, old.category, old.subject, old.content);
                    INSERT INTO personal_memories_fts(rowid, id, category, subject, content)
                    VALUES (new.rowid, new.id, new.category, new.subject, new.content);
                END;
                """)
        finally:
            conn.close()

    def _seed_initial_intelligence(self) -> None:
        """Seed initial confirmed user facts, procedural preferences, and goals if table is empty."""
        conn = self._get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) as c FROM personal_memories").fetchone()["c"]
            if count > 0:
                return

            now = time.time()
            seeds = [
                # 1. Profile
                PersonalMemory(
                    id="pi_prof_user",
                    category="profile",
                    subject="User Identity",
                    content="User name is Pranc. Principal operator of GHOST.",
                    source="user_confirmed",
                    confidence=1.0,
                    is_inferred=False,
                    created_at=now,
                    last_confirmed=now,
                ),
                PersonalMemory(
                    id="pi_prof_env",
                    category="profile",
                    subject="Hardware & OS Environment",
                    content="Operating System: Arch Linux with Hyprland WM, Brave Browser (CDP debug port 9222), NVIDIA GTX 1650 GPU (4GB VRAM), 8GB System RAM.",
                    source="user_confirmed",
                    confidence=1.0,
                    is_inferred=False,
                    created_at=now,
                    last_confirmed=now,
                ),
                PersonalMemory(
                    id="pi_prof_style",
                    category="profile",
                    subject="Communication Style",
                    content="Prefers direct, crisp, conversational replies (1-3 sentences max spoken). Zero fluff, no empty pleasantries, no conversational filler.",
                    source="user_confirmed",
                    confidence=1.0,
                    is_inferred=False,
                    created_at=now,
                    last_confirmed=now,
                ),

                # 2. Procedural
                PersonalMemory(
                    id="pi_proc_stocks",
                    category="procedural",
                    subject="Stock & Market Research Requirements",
                    content="When conducting financial research or stock analysis, deliver genuine fundamental and technical analysis: directional scenarios (bull/bear), actionable catalysts, precise support/resistance price levels, and risk/reward profiles. Monitor NSE, BSE, NIFTY 50, BANK NIFTY, SENSEX, and US-listed ADRs (INFY, WIT, HDB).",
                    source="user_confirmed",
                    confidence=1.0,
                    is_inferred=False,
                    created_at=now,
                    last_confirmed=now,
                ),
                PersonalMemory(
                    id="pi_proc_workspace",
                    category="procedural",
                    subject="Native Workspace Interaction",
                    content="Prefer opening documents, PDFs, text/code, images, camera, audio, and charts directly in GHOST internal native viewers instead of spawning external desktop applications.",
                    source="user_confirmed",
                    confidence=1.0,
                    is_inferred=False,
                    created_at=now,
                    last_confirmed=now,
                ),

                # 3. Project
                PersonalMemory(
                    id="pi_proj_grasshopper",
                    category="project",
                    subject="Project Grasshopper",
                    content="Modular agency component architecture for verified business reconnaissance, CRM, and Obsidian ledger synchronizations.",
                    source="user_confirmed",
                    confidence=1.0,
                    is_inferred=False,
                    created_at=now,
                    last_confirmed=now,
                ),
                PersonalMemory(
                    id="pi_proj_planter",
                    category="project",
                    subject="Project Planter",
                    content="Differentiated, commercially viable self-watering planter concept with parametric OpenSCAD CAD model, 3D mesh inspection, and full engineering dossier.",
                    source="user_confirmed",
                    confidence=1.0,
                    is_inferred=False,
                    created_at=now,
                    last_confirmed=now,
                ),

                # 4. Goal
                PersonalMemory(
                    id="pi_goal_personal_env",
                    category="goal",
                    subject="Personal Intelligence Environment",
                    content="Evolve GHOST into a unified personal intelligence environment featuring persistent structured memory, internal workspace modules, and adaptive orbital UI.",
                    source="user_confirmed",
                    confidence=1.0,
                    is_inferred=False,
                    created_at=now,
                    last_confirmed=now,
                ),

                # 5. Knowledge
                PersonalMemory(
                    id="pi_know_cad",
                    category="knowledge",
                    subject="Parametric CAD Standards",
                    content="OpenSCAD is installed natively on Arch Linux at /usr/bin/openscad. Mesh outputs are validated via manifold volume and surface geometry checks.",
                    source="user_confirmed",
                    confidence=1.0,
                    is_inferred=False,
                    created_at=now,
                    last_confirmed=now,
                ),

                # 6. Episodic (Example)
                PersonalMemory(
                    id="pi_ep_routing_sync",
                    category="episodic",
                    subject="Deterministic Routing Upgrade",
                    content="Successfully decoupled central assistant from giant prompt by implementing TaskRouter, DelegationContracts, and domain agent operational indicators (IDLE / WORKING).",
                    source="user_confirmed",
                    confidence=0.95,
                    is_inferred=False,
                    created_at=now,
                    last_confirmed=now,
                ),
            ]

            with conn:
                for s in seeds:
                    conn.execute("""
                    INSERT OR REPLACE INTO personal_memories (
                        id, category, subject, content, source, confidence,
                        created_at, last_confirmed, relevance, expires_at,
                        is_inferred, metadata_json, influence_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        s.id, s.category, s.subject, s.content, s.source, s.confidence,
                        s.created_at, s.last_confirmed, s.relevance, s.expires_at,
                        1 if s.is_inferred else 0, json.dumps(s.metadata), s.influence_count
                    ))
            logger.info("PersonalIntelligenceStore: Initial intelligence seeded successfully.")
        finally:
            conn.close()

    def add_memory(
        self,
        category: str,
        subject: str,
        content: str,
        source: str = "user_confirmed",
        confidence: float = 1.0,
        relevance: float = 1.0,
        is_inferred: bool = False,
        expires_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        memory_id: Optional[str] = None
    ) -> PersonalMemory:
        if category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{category}'. Must be one of {VALID_CATEGORIES}")

        mem_id = memory_id or f"pi_{category}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        now = time.time()
        mem = PersonalMemory(
            id=mem_id,
            category=category,
            subject=subject.strip(),
            content=content.strip(),
            source=source,
            confidence=confidence,
            created_at=now,
            last_confirmed=now,
            relevance=relevance,
            expires_at=expires_at,
            is_inferred=is_inferred,
            metadata=metadata or {},
            influence_count=0
        )

        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                INSERT OR REPLACE INTO personal_memories (
                    id, category, subject, content, source, confidence,
                    created_at, last_confirmed, relevance, expires_at,
                    is_inferred, metadata_json, influence_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    mem.id, mem.category, mem.subject, mem.content, mem.source, mem.confidence,
                    mem.created_at, mem.last_confirmed, mem.relevance, mem.expires_at,
                    1 if mem.is_inferred else 0, json.dumps(mem.metadata), mem.influence_count
                ))
        finally:
            conn.close()
        return mem

    def update_memory(
        self,
        memory_id: str,
        content: Optional[str] = None,
        subject: Optional[str] = None,
        category: Optional[str] = None,
        confidence: Optional[float] = None,
        is_inferred: Optional[bool] = None,
        source: Optional[str] = None,
        relevance: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[PersonalMemory]:
        """User correction or update of an existing memory."""
        mem = self.get_memory(memory_id)
        if not mem:
            return None

        if category is not None:
            if category not in VALID_CATEGORIES:
                raise ValueError(f"Invalid category '{category}'")
            mem.category = category
        if subject is not None:
            mem.subject = subject.strip()
        if content is not None:
            mem.content = content.strip()
        if confidence is not None:
            mem.confidence = confidence
        if is_inferred is not None:
            mem.is_inferred = is_inferred
        if source is not None:
            mem.source = source
        else:
            # Updating a memory automatically promotes it to user_confirmed
            mem.source = "user_confirmed"
            mem.is_inferred = False
            mem.confidence = 1.0

        if relevance is not None:
            mem.relevance = relevance
        if metadata is not None:
            mem.metadata = metadata

        mem.last_confirmed = time.time()

        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                UPDATE personal_memories SET
                    category = ?, subject = ?, content = ?, source = ?, confidence = ?,
                    last_confirmed = ?, relevance = ?, is_inferred = ?, metadata_json = ?
                WHERE id = ?
                """, (
                    mem.category, mem.subject, mem.content, mem.source, mem.confidence,
                    mem.last_confirmed, mem.relevance, 1 if mem.is_inferred else 0,
                    json.dumps(mem.metadata), mem.id
                ))
        finally:
            conn.close()
        return mem

    def confirm_memory(self, memory_id: str) -> Optional[PersonalMemory]:
        """Explicitly confirm an inferred memory item into a verified fact."""
        return self.update_memory(
            memory_id=memory_id,
            is_inferred=False,
            source="user_confirmed",
            confidence=1.0
        )

    def delete_memory(self, memory_id: str) -> bool:
        conn = self._get_connection()
        try:
            with conn:
                cur = conn.execute("DELETE FROM personal_memories WHERE id = ?", (memory_id,))
                return cur.rowcount > 0
        finally:
            conn.close()

    def get_memory(self, memory_id: str) -> Optional[PersonalMemory]:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT * FROM personal_memories WHERE id = ?", (memory_id,)).fetchone()
            if not row:
                return None
            return PersonalMemory.from_row(row)
        finally:
            conn.close()

    def list_memories(
        self,
        category: Optional[str] = None,
        is_inferred: Optional[bool] = None,
        search_query: Optional[str] = None,
        limit: int = 100
    ) -> List[PersonalMemory]:
        conn = self._get_connection()
        try:
            clauses = []
            params: List[Any] = []

            if category and category in VALID_CATEGORIES:
                clauses.append("category = ?")
                params.append(category)

            if is_inferred is not None:
                clauses.append("is_inferred = ?")
                params.append(1 if is_inferred else 0)

            where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""

            if search_query and search_query.strip():
                clean_query = "".join(c if c.isalnum() or c.isspace() else " " for c in search_query).strip()
                tokens = [t for t in clean_query.split() if len(t) > 2]
                if not tokens:
                    tokens = clean_query.split()

                if tokens:
                    fts_match = " OR ".join(tokens)
                    try:
                        q_clauses = list(clauses)
                        q_params = [fts_match] + list(params)
                        extra_where = (" AND " + " AND ".join(q_clauses)) if q_clauses else ""
                        sql = f"""
                        SELECT pm.* FROM personal_memories pm
                        JOIN personal_memories_fts fts ON pm.rowid = fts.rowid
                        WHERE personal_memories_fts MATCH ? {extra_where}
                        ORDER BY bm25(personal_memories_fts) ASC, pm.confidence DESC
                        LIMIT ?
                        """
                        q_params.append(limit)
                        rows = conn.execute(sql, q_params).fetchall()
                        return [PersonalMemory.from_row(r) for r in rows]
                    except Exception as e:
                        logger.warning(f"FTS query failed: {e}")

            # Standard order by relevance * confidence
            sql = f"""
            SELECT * FROM personal_memories {where_sql}
            ORDER BY (relevance * confidence) DESC, created_at DESC
            LIMIT ?
            """
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [PersonalMemory.from_row(r) for r in rows]
        finally:
            conn.close()

    def get_categories_summary(self) -> Dict[str, Any]:
        conn = self._get_connection()
        try:
            rows = conn.execute("""
            SELECT category, COUNT(*) as count,
                   SUM(CASE WHEN is_inferred = 1 THEN 1 ELSE 0 END) as inferred_count,
                   SUM(CASE WHEN is_inferred = 0 THEN 1 ELSE 0 END) as confirmed_count
            FROM personal_memories
            GROUP BY category
            """).fetchall()

            summary = {cat: {"count": 0, "inferred": 0, "confirmed": 0} for cat in VALID_CATEGORIES}
            total = 0
            for r in rows:
                c = r["category"]
                if c in summary:
                    summary[c] = {
                        "count": r["count"],
                        "inferred": r["inferred_count"],
                        "confirmed": r["confirmed_count"],
                    }
                    total += r["count"]

            return {
                "categories": summary,
                "total_memories": total,
                "valid_categories": list(VALID_CATEGORIES)
            }
        finally:
            conn.close()

    def retrieve_context(
        self,
        query: str = "",
        category: Optional[str] = None,
        limit: int = 5
    ) -> Dict[str, Any]:
        """
        Retrieves relevant personal memories for a user query.
        Returns formatted prompt string and list of memory IDs used for attribution.
        """
        memories = self.list_memories(category=category, search_query=query, limit=limit)
        if not memories and query:
            # Fallback to general high-relevance memories
            memories = self.list_memories(category=category, limit=limit)

        retrieved_ids = [m.id for m in memories]
        self.record_influence(retrieved_ids)

        # Build prompt snapshot (strictly scoped and structured)
        lines = ["[PERSONAL INTELLIGENCE CONTEXT]"]
        for m in memories:
            inferred_tag = " (Inferred)" if m.is_inferred else ""
            lines.append(f"- [{m.category.upper()}] {m.subject}{inferred_tag}: {m.content}")

        return {
            "snapshot": "\n".join(lines),
            "memories": [m.to_dict() for m in memories],
            "retrieved_ids": retrieved_ids,
        }

    def record_influence(self, memory_ids: List[str]) -> None:
        if not memory_ids:
            return
        conn = self._get_connection()
        try:
            with conn:
                placeholders = ",".join("?" for _ in memory_ids)
                conn.execute(f"""
                UPDATE personal_memories
                SET influence_count = influence_count + 1
                WHERE id IN ({placeholders})
                """, memory_ids)
        except Exception as e:
            logger.warning(f"Failed to record influence: {e}")
        finally:
            conn.close()
