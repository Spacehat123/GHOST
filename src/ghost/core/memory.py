"""Three-Layer Memory System for Ghost Autonomous Agency Engine.
Layer 1: WorkingMemory (Fast, task-specific active context)
Layer 2: EpisodicMemoryStore (Persistent SQLite FTS5 BM25 search over past tasks & outcomes)
Layer 3: CoreMemoryStore (Persistent facts & directives with lossless contradiction detection and <=250-token snapshot ceiling)
"""
import os
import json
import time
import sqlite3
import logging
import uuid
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("ghost.memory")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
DATA_DIR = os.path.join(REPO_DIR, "data")
CONFIGS_DIR = os.path.join(REPO_DIR, "configs")
MEMORY_DB_PATH = os.environ.get("GHOST_MEMORY_DB", os.path.join(DATA_DIR, "memory.db"))
DIRECTIVES_PATH = os.environ.get("GHOST_DIRECTIVES_CONFIG", os.path.join(CONFIGS_DIR, "directives.json"))

# -------------------------------------------------------------
# Layer 1: Working Memory
# -------------------------------------------------------------
@dataclass
class WorkingMemory:
    task_id: str
    active_goal: str = ""
    active_constraints: List[str] = field(default_factory=list)
    pending_actions: List[str] = field(default_factory=list)
    recent_tool_results: List[Dict[str, Any]] = field(default_factory=list)
    context_vars: Dict[str, Any] = field(default_factory=dict)
    start_time: float = field(default_factory=time.time)

    def add_constraint(self, constraint: str) -> None:
        if constraint not in self.active_constraints:
            self.active_constraints.append(constraint)

    def add_tool_result(self, tool: str, action: str, result: Dict[str, Any]) -> None:
        self.recent_tool_results.append({
            "tool": tool,
            "action": action,
            "result": result,
            "timestamp": time.time()
        })
        # Keep working memory compact: retain last 10 tool results
        if len(self.recent_tool_results) > 10:
            self.recent_tool_results = self.recent_tool_results[-10:]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "active_goal": self.active_goal,
            "active_constraints": list(self.active_constraints),
            "pending_actions": list(self.pending_actions),
            "recent_tool_results": list(self.recent_tool_results),
            "context_vars": dict(self.context_vars),
            "elapsed_seconds": round(time.time() - self.start_time, 2)
        }

    def clear(self) -> None:
        self.active_goal = ""
        self.active_constraints.clear()
        self.pending_actions.clear()
        self.recent_tool_results.clear()
        self.context_vars.clear()


# -------------------------------------------------------------
# Layer 2: Episodic Memory (SQLite FTS5)
# -------------------------------------------------------------
class EpisodicMemoryStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or MEMORY_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id TEXT PRIMARY KEY,
                    task_id TEXT,
                    query TEXT,
                    summary TEXT,
                    observation TEXT,
                    outcome TEXT,
                    error TEXT,
                    lessons_learned TEXT,
                    importance REAL DEFAULT 0.5,
                    created_at REAL,
                    metadata_json TEXT
                )
                """)
                # Create FTS5 virtual table for full-text BM25 ranking
                conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                    id, query, summary, observation, outcome, lessons_learned,
                    content='episodes', content_rowid='rowid'
                )
                """)
                # Triggers to keep FTS index synchronized
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
                    INSERT INTO episodes_fts(rowid, id, query, summary, observation, outcome, lessons_learned)
                    VALUES (new.rowid, new.id, new.query, new.summary, new.observation, new.outcome, new.lessons_learned);
                END;
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
                    INSERT INTO episodes_fts(episodes_fts, rowid, id, query, summary, observation, outcome, lessons_learned)
                    VALUES('delete', old.rowid, old.id, old.query, old.summary, old.observation, old.outcome, old.lessons_learned);
                END;
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes BEGIN
                    INSERT INTO episodes_fts(episodes_fts, rowid, id, query, summary, observation, outcome, lessons_learned)
                    VALUES('delete', old.rowid, old.id, old.query, old.summary, old.observation, old.outcome, old.lessons_learned);
                    INSERT INTO episodes_fts(rowid, id, query, summary, observation, outcome, lessons_learned)
                    VALUES (new.rowid, new.id, new.query, new.summary, new.observation, new.outcome, new.lessons_learned);
                END;
                """)
        finally:
            conn.close()

    def record_episode(
        self,
        task_id: str,
        query: str,
        summary: str,
        observation: str = "",
        outcome: str = "",
        error: str = "",
        lessons_learned: str = "",
        importance: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
        episode_id: Optional[str] = None
    ) -> str:
        ep_id = episode_id or f"ep_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        now = time.time()
        meta_str = json.dumps(metadata or {})
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                INSERT OR REPLACE INTO episodes (
                    id, task_id, query, summary, observation, outcome, error, lessons_learned, importance, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ep_id, task_id, query, summary, observation, outcome, error, lessons_learned, importance, now, meta_str
                ))
        finally:
            conn.close()
        return ep_id

    def retrieve_relevant(self, query: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Retrieve relevant past episodes using FTS5 BM25 text relevance weighted by importance."""
        clean_query = "".join(c if c.isalnum() or c.isspace() else " " for c in query).strip()
        conn = self._get_connection()
        try:
            if not clean_query:
                # Fallback to recent high-importance episodes
                rows = conn.execute("""
                SELECT * FROM episodes ORDER BY importance DESC, created_at DESC LIMIT ?
                """, (limit,)).fetchall()
                return [dict(r) for r in rows]

            # Use FTS5 MATCH with tokens joined by OR
            tokens = [t for t in clean_query.split() if len(t) > 2]
            if not tokens:
                tokens = clean_query.split()

            fts_query = " OR ".join(tokens)
            try:
                rows = conn.execute("""
                SELECT e.*, bm25(episodes_fts) AS rank
                FROM episodes e
                JOIN episodes_fts ON e.rowid = episodes_fts.rowid
                WHERE episodes_fts MATCH ?
                ORDER BY (bm25(episodes_fts) * (1.5 - e.importance)) ASC
                LIMIT ?
                """, (fts_query, limit)).fetchall()
                if rows:
                    return [dict(r) for r in rows]
            except Exception as e:
                logger.warning(f"FTS5 query failed for '{clean_query}': {e}")

            # Fallback query if FTS yields nothing
            rows = conn.execute("""
            SELECT * FROM episodes ORDER BY importance DESC, created_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# -------------------------------------------------------------
# Layer 3: Core Memory Store (Lossless Contradiction & <=250 Token Budget)
# -------------------------------------------------------------
@dataclass
class CoreMemoryItem:
    id: str
    type: str  # "directive", "fact", "preference"
    content: str
    source: str = "user_confirmed"
    confidence: float = 1.0
    importance: float = 0.5
    conflict_detected: bool = False
    conflict_with_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    last_verified: float = field(default_factory=time.time)
    expires_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "content": self.content,
            "source": self.source,
            "confidence": self.confidence,
            "importance": self.importance,
            "conflict_detected": self.conflict_detected,
            "conflict_with_id": self.conflict_with_id,
            "created_at": self.created_at,
            "last_verified": self.last_verified,
            "expires_at": self.expires_at
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CoreMemoryItem":
        # Support both legacy "text" and new "content"
        content = d.get("content") or d.get("text") or ""
        mem_type = d.get("type", "fact")
        return cls(
            id=d.get("id", f"mem_{int(time.time() * 1000)}"),
            type=mem_type,
            content=content,
            source=d.get("source", "user_confirmed"),
            confidence=float(d.get("confidence", 1.0)),
            importance=float(d.get("importance", 0.8 if mem_type == "directive" else 0.5)),
            conflict_detected=bool(d.get("conflict_detected", False)),
            conflict_with_id=d.get("conflict_with_id"),
            created_at=float(d.get("created_at", time.time())),
            last_verified=float(d.get("last_verified", time.time())),
            expires_at=d.get("expires_at")
        )


class CoreMemoryStore:
    """
    Manages stable directives, preferences, and verified facts.
    Guarantees:
    1. Lossless contradiction handling (never silently delete or overwrite conflicts).
    2. Strict snapshot token limit (<= 250 tokens).
    """

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or DIRECTIVES_PATH
        self._cached_data: Optional[Dict[str, Any]] = None
        self._last_mtime: float = 0.0
        self._compiled_snapshot: str = ""

    def load(self, force_reload: bool = False) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            data = self._get_initial_data()
            self.save(data)
            return data

        try:
            mtime = os.path.getmtime(self.config_path)
            if not force_reload and self._cached_data is not None and mtime <= self._last_mtime:
                return self._cached_data

            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._cached_data = data
            self._last_mtime = mtime
            self._recompile_snapshot(data)
            return data
        except Exception as e:
            logger.error(f"Error loading core memory from {self.config_path}: {e}")
            if self._cached_data is not None:
                return self._cached_data
            data = self._get_initial_data()
            return data

    def _get_initial_data(self) -> Dict[str, Any]:
        return {
            "version": 2,
            "user_name": "Pranc",
            "environment": {
                "os": "Arch Linux",
                "wm": "Hyprland",
                "browser": "Brave (port 9222)"
            },
            "memories": [
                {
                    "id": "dir_001",
                    "type": "directive",
                    "content": "Deliver spoken replies in 1-3 crisp conversational sentences max.",
                    "source": "system_bootstrap",
                    "confidence": 1.0,
                    "importance": 1.0,
                    "conflict_detected": False,
                    "conflict_with_id": None,
                    "created_at": time.time(),
                    "last_verified": time.time(),
                    "expires_at": None
                },
                {
                    "id": "dir_002",
                    "type": "directive",
                    "content": "Zero pleasantries, preamble, or fluff. Jump straight to the point.",
                    "source": "system_bootstrap",
                    "confidence": 1.0,
                    "importance": 0.95,
                    "conflict_detected": False,
                    "conflict_with_id": None,
                    "created_at": time.time(),
                    "last_verified": time.time(),
                    "expires_at": None
                },
                {
                    "id": "dir_003",
                    "type": "directive",
                    "content": "Never output markdown asterisks, backticks, or code blocks in spoken dialogue.",
                    "source": "system_bootstrap",
                    "confidence": 1.0,
                    "importance": 0.95,
                    "conflict_detected": False,
                    "conflict_with_id": None,
                    "created_at": time.time(),
                    "last_verified": time.time(),
                    "expires_at": None
                },
                {
                    "id": "fact_001",
                    "type": "fact",
                    "content": "User name is Pranc.",
                    "source": "user_confirmed",
                    "confidence": 1.0,
                    "importance": 0.9,
                    "conflict_detected": False,
                    "conflict_with_id": None,
                    "created_at": time.time(),
                    "last_verified": time.time(),
                    "expires_at": None
                },
                {
                    "id": "fact_002",
                    "type": "fact",
                    "content": "Primary workspace is /home/pranc/projects.",
                    "source": "user_confirmed",
                    "confidence": 1.0,
                    "importance": 0.9,
                    "conflict_detected": False,
                    "conflict_with_id": None,
                    "created_at": time.time(),
                    "last_verified": time.time(),
                    "expires_at": None
                }
            ]
        }

    def _extract_memories(self, data: Dict[str, Any]) -> List[CoreMemoryItem]:
        items: List[CoreMemoryItem] = []
        # If new version 2 format with "memories" list
        if "memories" in data and isinstance(data["memories"], list):
            for d in data["memories"]:
                items.append(CoreMemoryItem.from_dict(d))
            return items

        # Backwards compatibility: extract from legacy "directives" and "facts"
        for d in data.get("directives", []):
            item = CoreMemoryItem.from_dict({
                "id": d.get("id"),
                "type": "directive",
                "content": d.get("text", ""),
                "importance": 0.9,
                "confidence": 1.0,
                "created_at": d.get("created_at", time.time())
            })
            items.append(item)

        for f in data.get("facts", []):
            item = CoreMemoryItem.from_dict({
                "id": f.get("id"),
                "type": "fact",
                "content": f.get("text", ""),
                "importance": 0.6,
                "confidence": 1.0,
                "created_at": f.get("created_at", time.time())
            })
            items.append(item)

        return items

    def save(self, data: Dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._cached_data = data
            self._last_mtime = os.path.getmtime(self.config_path)
            self._recompile_snapshot(data)
            logger.info("CoreMemoryStore: Directives and memories saved successfully.")
        except Exception as e:
            logger.error(f"CoreMemoryStore: Failed saving to {self.config_path}: {e}")
            raise

    def add_memory(
        self,
        content: str,
        mem_type: str = "fact",
        source: str = "user_confirmed",
        importance: float = 0.5,
        confidence: float = 1.0,
        expires_at: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Add a memory item with lossless contradiction detection.
        Never silently overwrites or deletes contradictory items.
        """
        content_clean = content.strip()
        data = self.load()
        memories = self._extract_memories(data)

        # Check exact duplicate
        for m in memories:
            if m.content.lower() == content_clean.lower() and m.type == mem_type:
                return {"success": True, "action": "already_exists", "item": m.to_dict()}

        new_id = f"mem_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        new_item = CoreMemoryItem(
            id=new_id,
            type=mem_type,
            content=content_clean,
            source=source,
            confidence=confidence,
            importance=importance,
            conflict_detected=False,
            conflict_with_id=None,
            created_at=time.time(),
            last_verified=time.time(),
            expires_at=expires_at
        )

        # Contradiction Detection:
        # Check against existing memories for conflicting assertions on the same subject
        conflict_item = self._detect_contradiction(new_item, memories)
        if conflict_item:
            logger.warning(
                f"CoreMemoryStore: Contradiction detected between new '{new_item.content}' "
                f"and existing '{conflict_item.content}' ({conflict_item.id}). Preserving both."
            )
            new_item.conflict_detected = True
            new_item.conflict_with_id = conflict_item.id
            conflict_item.conflict_detected = True
            conflict_item.conflict_with_id = new_item.id

        memories.append(new_item)

        # Save back to data
        data["memories"] = [m.to_dict() for m in memories]
        # Sync legacy keys for backward compat
        data["directives"] = [{"id": m.id, "text": m.content, "created_at": m.created_at} for m in memories if m.type == "directive"]
        data["facts"] = [{"id": m.id, "text": m.content, "created_at": m.created_at} for m in memories if m.type != "directive"]

        self.save(data)
        return {
            "success": True,
            "action": "added",
            "item": new_item.to_dict(),
            "conflict_detected": new_item.conflict_detected,
            "conflict_with_id": new_item.conflict_with_id
        }

    def _detect_contradiction(self, candidate: CoreMemoryItem, existing: List[CoreMemoryItem]) -> Optional[CoreMemoryItem]:
        """
        Semantic and substantive contradiction heuristic:
        Detects opposing assertions on the SAME subject topic while ignoring generic qualifiers.
        """
        candidate_lower = candidate.content.lower()
        stopwords = {
            "is", "are", "the", "a", "an", "in", "at", "for", "of", "to",
            "and", "or", "user", "primary", "default", "our", "my", "current"
        }
        cand_tokens = set(candidate_lower.split())
        cand_substantive = cand_tokens - stopwords

        for item in existing:
            if item.type != candidate.type:
                continue
            item_lower = item.content.lower()
            item_tokens = set(item_lower.split())
            item_substantive = item_tokens - stopwords

            overlap_substantive = cand_substantive.intersection(item_substantive)
            difference = cand_tokens.symmetric_difference(item_tokens)

            # Must share substantive topic domain words and differ in complement
            if len(overlap_substantive) >= 1 and len(difference) > 0:
                total_unique = len(cand_tokens.union(item_tokens))
                similarity = len(cand_tokens.intersection(item_tokens)) / max(1, total_unique)
                if similarity >= 0.5 or len(overlap_substantive) >= 2:
                    return item

        return None

    def resolve_conflict(self, accepted_id: str, retired_id: str, resolution_evidence: str) -> bool:
        """
        Explicitly resolves a conflict when evidence is provided.
        Never silently deletes: archives the retired memory with resolution note.
        """
        data = self.load()
        memories = self._extract_memories(data)
        accepted = next((m for m in memories if m.id == accepted_id), None)
        retired = next((m for m in memories if m.id == retired_id), None)

        if not accepted or not retired:
            return False

        accepted.conflict_detected = False
        accepted.conflict_with_id = None
        accepted.last_verified = time.time()
        accepted.source = f"resolved_via_evidence: {resolution_evidence}"

        # Retire conflicting memory safely without deletion
        retired.conflict_detected = False
        retired.conflict_with_id = None
        retired.importance = 0.0  # drop importance to 0 so it drops out of active snapshot
        retired.content = f"[ARCHIVED/SUPERSEDED by {accepted_id}]: {retired.content}"

        data["memories"] = [m.to_dict() for m in memories]
        data["directives"] = [{"id": m.id, "text": m.content, "created_at": m.created_at} for m in memories if m.type == "directive" and not m.content.startswith("[ARCHIVED")]
        data["facts"] = [{"id": m.id, "text": m.content, "created_at": m.created_at} for m in memories if m.type != "directive" and not m.content.startswith("[ARCHIVED")]
        self.save(data)
        return True

    def _recompile_snapshot(self, data: Dict[str, Any]) -> None:
        """
        Compiles directives and facts into a compact prompt snapshot.
        STRICT CEILING: Snapshot must be <= 250 tokens!
        Estimates tokens using `len(text) // 4` (conservative standard).
        Omit unresolved conflicting items from active injection to prevent model confusion.
        """
        user_name = data.get("user_name", "Pranc")
        env_items = [f"{k}: {v}" for k, v in data.get("environment", {}).items()]
        env_str = ", ".join(env_items)

        header_lines = [
            "[CORE MEMORY & ACTIVE DIRECTIVES]",
            f"User: {user_name} | Environment: {env_str}",
        ]

        memories = self._extract_memories(data)

        # Separate into directives and facts; filter out conflicted or expired items
        now = time.time()
        valid_items = [
            m for m in memories
            if not m.conflict_detected
            and (m.expires_at is None or m.expires_at > now)
            and not m.content.startswith("[ARCHIVED")
        ]

        # Prioritize strictly by importance * confidence
        valid_items.sort(key=lambda m: (m.importance * m.confidence), reverse=True)

        directives = [m for m in valid_items if m.type == "directive"]
        facts = [m for m in valid_items if m.type != "directive"]

        lines = list(header_lines)

        # Enforce strict <= 250 token limit
        MAX_TOKENS = 250

        def current_tokens(current_lines: List[str]) -> int:
            return max(1, len("\n".join(current_lines)) // 4)

        if directives:
            lines.append("Directives:")
            for d in directives:
                candidate_line = f"- {d.content}"
                if current_tokens(lines + [candidate_line]) <= MAX_TOKENS:
                    lines.append(candidate_line)
                else:
                    break

        if facts:
            lines.append("Facts:")
            for f in facts:
                candidate_line = f"- {f.content}"
                if current_tokens(lines + [candidate_line]) <= MAX_TOKENS:
                    lines.append(candidate_line)
                else:
                    break

        snapshot_str = "\n".join(lines)
        # Final strict truncate assert
        if (len(snapshot_str) // 4) > MAX_TOKENS:
            snapshot_str = snapshot_str[:(MAX_TOKENS * 4)]

        self._compiled_snapshot = snapshot_str

    def get_snapshot(self) -> str:
        if self._cached_data is None:
            self.load()
        return self._compiled_snapshot
