"""Universal Event Bus for GHOST Universal Intelligence System.

Conforms to INTERFACE_CONTRACTS.md §1:
- Universal Event schema with UUID, dot-delimited taxonomy, severity classification.
- High-throughput asynchronous pub/sub with exact and prefix wildcard filtering.
- Persistent SQLite WAL backing (events_v2 table).
- Idempotent deduplication via dedup_id / content hash.
- Subscriber fault isolation (one subscriber exception never crashes the bus or peers).
- Dead-letter logging and inspection.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Union

from ghost.store.facade import ghost_db_path

logger = logging.getLogger("ghost.core.events")


class EventSeverity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @classmethod
    def from_str(cls, val: str) -> "EventSeverity":
        try:
            return cls(val.upper())
        except (ValueError, KeyError, AttributeError):
            return cls.INFO

    @property
    def level_number(self) -> int:
        levels = {
            self.DEBUG: 10,
            self.INFO: 20,
            self.WARNING: 30,
            self.ERROR: 40,
            self.CRITICAL: 50,
        }
        return levels.get(self, 20)


@dataclass(frozen=True)
class Event:
    """Universal immutable event representation across all 7 intelligence domains."""
    event_type: str
    source: str
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    severity: EventSeverity = EventSeverity.INFO
    correlation_id: Optional[str] = None
    provenance: Optional[str] = None
    dedup_id: Optional[str] = None

    def __post_init__(self):
        # Guarantee severity is an EventSeverity instance
        if isinstance(self.severity, str):
            object.__setattr__(self, "severity", EventSeverity.from_str(self.severity))
        # Compute automatic dedup_id if not provided
        if not self.dedup_id:
            computed = self.compute_dedup_id()
            object.__setattr__(self, "dedup_id", computed)

    def compute_dedup_id(self) -> str:
        """Deterministic SHA-256 fingerprint of event_type + source + sorted payload."""
        try:
            payload_str = json.dumps(self.payload, sort_keys=True, default=str)
        except Exception:
            payload_str = str(self.payload)
        raw = f"{self.event_type}:{self.source}:{payload_str}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "timestamp": self.timestamp,
            "severity": self.severity.value,
            "payload": self.payload,
            "correlation_id": self.correlation_id,
            "provenance": self.provenance,
            "dedup_id": self.dedup_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        payload = data.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"raw": payload}
        return cls(
            event_id=data.get("event_id") or str(uuid.uuid4()),
            event_type=data.get("event_type", "system.unknown"),
            source=data.get("source", "unknown"),
            timestamp=float(data.get("timestamp", time.time())),
            severity=EventSeverity.from_str(data.get("severity", "INFO")),
            payload=payload,
            correlation_id=data.get("correlation_id"),
            provenance=data.get("provenance"),
            dedup_id=data.get("dedup_id"),
        )


SubscriptionCallback = Union[
    Callable[[Event], Awaitable[None]],
    Callable[[Event], None],
]


@dataclass
class _Subscription:
    sub_id: str
    pattern: str
    callback: SubscriptionCallback
    is_async: bool


class EventBus:
    """Universal asynchronous pub/sub event bus with SQLite backing and fault isolation."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        enable_persistence: bool = True,
        dedup_window_s: float = 60.0,
    ):
        self.db_path = db_path or ghost_db_path()
        self.enable_persistence = enable_persistence if self.db_path != ":memory:" else False
        self.dedup_window_s = dedup_window_s

        self._subscriptions: Dict[str, _Subscription] = {}
        self._pattern_cache: Dict[str, List[_Subscription]] = {}
        self._recent_dedup: Dict[str, float] = {}  # dedup_id -> timestamp
        self._dead_letters: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._async_lock: Optional[asyncio.Lock] = None

        if self.enable_persistence and self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
            self._init_db()

    def _init_db(self) -> None:
        """Initializes events_v2 schema in WAL mode."""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events_v2 (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    severity TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    correlation_id TEXT,
                    provenance TEXT,
                    dedup_id TEXT,
                    created_at REAL NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_v2_type_ts ON events_v2 (event_type, timestamp DESC);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_v2_dedup ON events_v2 (dedup_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_v2_correlation ON events_v2 (correlation_id);"
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize events_v2 database at {self.db_path}: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def subscribe(self, pattern: str, callback: SubscriptionCallback) -> str:
        """Subscribe to an event topic pattern.
        
        Patterns:
        - "*" matches all events
        - "system.*" matches any event starting with "system."
        - "system.telemetry.sample" exact match
        """
        sub_id = str(uuid.uuid4())
        is_async = inspect.iscoroutinefunction(callback)
        sub = _Subscription(
            sub_id=sub_id,
            pattern=pattern.strip(),
            callback=callback,
            is_async=is_async,
        )
        with self._lock:
            self._subscriptions[sub_id] = sub
            self._rebuild_pattern_cache()
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Remove a subscription by ID."""
        with self._lock:
            if sub_id in self._subscriptions:
                del self._subscriptions[sub_id]
                self._rebuild_pattern_cache()
                return True
        return False

    def _rebuild_pattern_cache(self) -> None:
        cache: Dict[str, List[_Subscription]] = {}
        for sub in self._subscriptions.values():
            cache.setdefault(sub.pattern, []).append(sub)
        self._pattern_cache = cache

    def _matches_pattern(self, pattern: str, event_type: str) -> bool:
        if pattern == "*" or pattern == event_type:
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return event_type == prefix or event_type.startswith(prefix + ".")
        return False

    def _get_matching_subscriptions(self, event_type: str) -> List[_Subscription]:
        with self._lock:
            matches: List[_Subscription] = []
            for pattern, subs in self._pattern_cache.items():
                if self._matches_pattern(pattern, event_type):
                    matches.extend(subs)
            return matches

    def _check_dedup(self, dedup_id: Optional[str], now: float) -> bool:
        """Returns True if duplicate (should drop), False if unique."""
        if not dedup_id:
            return False
        with self._lock:
            # Purge expired dedup entries
            cutoff = now - self.dedup_window_s
            expired = [k for k, ts in self._recent_dedup.items() if ts < cutoff]
            for k in expired:
                del self._recent_dedup[k]

            if dedup_id in self._recent_dedup:
                return True  # Duplicate
            self._recent_dedup[dedup_id] = now
            return False

    def _persist_event(self, event: Event) -> None:
        if not self.enable_persistence:
            return
        conn = None
        try:
            payload_str = json.dumps(event.payload, default=str)
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.execute(
                """
                INSERT OR IGNORE INTO events_v2 (
                    event_id, event_type, source, timestamp, severity,
                    payload, correlation_id, provenance, dedup_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.source,
                    event.timestamp,
                    event.severity.value,
                    payload_str,
                    event.correlation_id,
                    event.provenance,
                    event.dedup_id,
                    time.time(),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to persist event {event.event_id}: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    async def publish(self, event: Event) -> bool:
        """Asynchronously publish an event to subscribers and persist to database.
        
        Returns True if delivered, False if dropped as duplicate.
        """
        now = time.time()
        if self._check_dedup(event.dedup_id, now):
            logger.debug(f"Event dropped by deduplication filter: {event.dedup_id} ({event.event_type})")
            return False

        # SQLite write
        if self.enable_persistence:
            # Offload SQLite blocking I/O to thread pool
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._persist_event, event)

        # Dispatch to subscribers
        subs = self._get_matching_subscriptions(event.event_type)
        for sub in subs:
            try:
                if sub.is_async:
                    await sub.callback(event)  # type: ignore
                else:
                    sub.callback(event)  # type: ignore
            except Exception as e:
                logger.error(
                    f"Subscriber exception in {sub.pattern} handler for event {event.event_id}: {e}",
                    exc_info=True,
                )
                with self._lock:
                    self._dead_letters.append({
                        "sub_id": sub.sub_id,
                        "pattern": sub.pattern,
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "error": str(e),
                        "timestamp": now,
                    })
                    # Keep dead letter buffer capped at 200
                    if len(self._dead_letters) > 200:
                        self._dead_letters = self._dead_letters[-200:]
        return True

    def publish_sync(self, event: Event) -> bool:
        """Synchronously publish an event.
        
        If an event loop is running in the current thread, schedules publication.
        Otherwise executes synchronously.
        """
        now = time.time()
        if self._check_dedup(event.dedup_id, now):
            return False

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self.publish(event))
                return True
        except RuntimeError:
            pass

        # Pure synchronous execution
        if self.enable_persistence:
            self._persist_event(event)

        subs = self._get_matching_subscriptions(event.event_type)
        for sub in subs:
            try:
                if sub.is_async:
                    # Run async callback synchronously via a new event loop
                    asyncio.run(sub.callback(event))  # type: ignore
                else:
                    sub.callback(event)  # type: ignore
            except Exception as e:
                logger.error(f"Sync subscriber error for {event.event_id}: {e}")
                with self._lock:
                    self._dead_letters.append({
                        "sub_id": sub.sub_id,
                        "pattern": sub.pattern,
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "error": str(e),
                        "timestamp": now,
                    })
        return True

    def query(
        self,
        limit: int = 50,
        event_type_prefix: Optional[str] = None,
        min_severity: Optional[EventSeverity] = None,
        since_timestamp: Optional[float] = None,
    ) -> List[Event]:
        """Query persisted events with filtering."""
        if not self.enable_persistence:
            return []

        clauses = ["1=1"]
        params: List[Any] = []

        if event_type_prefix:
            clauses.append("event_type LIKE ?")
            params.append(f"{event_type_prefix}%")

        if since_timestamp is not None:
            clauses.append("timestamp >= ?")
            params.append(since_timestamp)

        query_sql = f"""
            SELECT event_id, event_type, source, timestamp, severity, payload,
                   correlation_id, provenance, dedup_id
            FROM events_v2
            WHERE {' AND '.join(clauses)}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params.append(limit)

        results: List[Event] = []
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(query_sql, params)
            for row in cur.fetchall():
                ev = Event(
                    event_id=row["event_id"],
                    event_type=row["event_type"],
                    source=row["source"],
                    timestamp=row["timestamp"],
                    severity=EventSeverity.from_str(row["severity"]),
                    payload=json.loads(row["payload"]),
                    correlation_id=row["correlation_id"],
                    provenance=row["provenance"],
                    dedup_id=row["dedup_id"],
                )
                if min_severity and ev.severity.level_number < min_severity.level_number:
                    continue
                results.append(ev)
        except Exception as e:
            logger.error(f"Failed to query events: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        return results

    def get_dead_letters(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._dead_letters)

    def clear_dead_letters(self) -> None:
        with self._lock:
            self._dead_letters.clear()


# Global Singleton EventBus Instance
_GLOBAL_EVENT_BUS: Optional[EventBus] = None
_GLOBAL_LOCK = threading.Lock()


def get_event_bus() -> EventBus:
    """Returns the process-wide global EventBus instance."""
    global _GLOBAL_EVENT_BUS
    if _GLOBAL_EVENT_BUS is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_EVENT_BUS is None:
                _GLOBAL_EVENT_BUS = EventBus()
    return _GLOBAL_EVENT_BUS


def reset_event_bus(bus: Optional[EventBus] = None) -> None:
    """Testing helper to reset or inject custom EventBus instance."""
    global _GLOBAL_EVENT_BUS
    with _GLOBAL_LOCK:
        _GLOBAL_EVENT_BUS = bus
