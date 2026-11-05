"""Durable Job Queue — P0 skeleton (Roadmap §3.1).

SQLite-backed with the semantics the roadmap demands:
- idempotency keys (a job re-enqueued with the same key never double-runs)
- exponential backoff retries with jitter
- poison-pill quarantine (max_attempts exceeded -> dead-letter + ping)
- crash-safe: a job claimed by a dead worker is re-claimed after lease expiry
- every transition lands in the audit ledger

Async-first (workers are coroutines) with a synchronous enqueue so any
caller can drop work on the queue without ceremony.
"""
import asyncio
import json
import logging
import os
import random
import sqlite3
from contextlib import closing
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ghost.core.audit import audit

logger = logging.getLogger("ghost.jobqueue")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
QUEUE_DB_PATH = os.environ.get("GHOST_QUEUE_DB_PATH") or os.path.join(REPO_DIR, "data", "queue.db")

JOB_STATES = ("pending", "claimed", "done", "failed", "dead")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT UNIQUE,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 4,
    next_run_at REAL NOT NULL DEFAULT 0,
    lease_until REAL,
    lease_owner TEXT,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_ready ON jobs(state, next_run_at);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
"""


class DeadLetter(Exception):
    """Raised to the ping callback when a job exhausts its attempts."""


class JobQueue:
    def __init__(self, db_path: str = QUEUE_DB_PATH, lease_seconds: float = 300.0):
        self.db_path = db_path
        self.lease_seconds = lease_seconds
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with closing(self._conn()) as conn, conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------- enqueue

    def enqueue(self, kind: str, payload: Optional[Dict[str, Any]] = None,
                idempotency_key: Optional[str] = None, max_attempts: int = 4) -> Optional[str]:
        """Add a job. With an idempotency_key, a duplicate enqueue is a no-op
        that returns the existing job_id (silently — restarts are normal)."""
        payload = payload or {}
        job_id = str(uuid.uuid4())
        now = time.time()
        try:
            with closing(self._conn()) as conn, conn:
                conn.execute(
                    """INSERT INTO jobs (job_id, idempotency_key, kind, payload, max_attempts,
                       next_run_at, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (job_id, idempotency_key, kind, json.dumps(payload, default=str),
                     max_attempts, now, now, now)
                )
            audit("job.enqueued", subject=job_id, kind=kind, idempotency_key=idempotency_key)
            return job_id
        except sqlite3.IntegrityError:
            # duplicate idempotency key — the exact resilience we want
            if not idempotency_key:
                raise
            with closing(self._conn()) as conn, conn:
                row = conn.execute(
                    "SELECT job_id FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
            return row["job_id"] if row else None

    # ------------------------------------------------------------- claiming

    def _claim_next(self, owner: str) -> Optional[sqlite3.Row]:
        """Atomically claim the next runnable job (pending & due, or lease-expired).
        Uses a transaction so two workers can never claim the same job."""
        now = time.time()
        with closing(self._conn()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """SELECT * FROM jobs
                       WHERE (state = 'pending' AND next_run_at <= ?)
                          OR (state = 'claimed' AND lease_until IS NOT NULL AND lease_until < ?)
                       ORDER BY next_run_at ASC LIMIT 1""",
                    (now, now)
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                conn.execute(
                    """UPDATE jobs SET state = 'claimed', lease_owner = ?, lease_until = ?,
                       attempts = attempts + 1, updated_at = ? WHERE id = ?""",
                    (owner, now + self.lease_seconds, now, row["id"])
                )
                conn.execute("COMMIT")
                return row
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _has_future_work(self) -> bool:
        """True if any job is pending/claimed (including retry backoff waits)."""
        with closing(self._conn()) as conn, conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE state IN ('pending', 'claimed')"
            ).fetchone()
        return row["n"] > 0

    def _heartbeat(self, job_row_id: int, owner: str) -> None:
        with closing(self._conn()) as conn, conn:
            conn.execute(
                "UPDATE jobs SET lease_until = ? WHERE id = ? AND lease_owner = ?",
                (time.time() + self.lease_seconds, job_row_id, owner)
            )

    def _finish(self, job_row_id: int, state: str, error: Optional[str] = None,
                backoff: float = 0.0, count_attempt: bool = True) -> None:
        with closing(self._conn()) as conn, conn:
            if count_attempt:
                conn.execute(
                    """UPDATE jobs SET state = ?, last_error = ?, lease_owner = NULL, lease_until = NULL,
                       next_run_at = ?, updated_at = ? WHERE id = ?""",
                    (state, error, time.time() + backoff, time.time(), job_row_id)
                )
            else:
                # Halt-pause: the claim's attempt increment is rolled back so a
                # paused job resumes with its full retry budget intact.
                conn.execute(
                    """UPDATE jobs SET state = ?, last_error = ?, lease_owner = NULL, lease_until = NULL,
                       next_run_at = ?, updated_at = ?, attempts = MAX(0, attempts - 1) WHERE id = ?""",
                    (state, error, time.time() + backoff, time.time(), job_row_id)
                )

    # ------------------------------------------------------------- worker

    async def run_worker(self, handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Any]]],
                         owner: Optional[str] = None, poll_interval: float = 1.0,
                         idle_exit_after: Optional[float] = None,
                         dead_letter_ping: Optional[Callable[[Dict[str, Any]], Any]] = None,
                         halt_check: Optional[Callable[[], bool]] = None) -> int:
        """Process jobs until the queue is drained (or forever if idle_exit_after is None).

        handlers: kind -> async fn(payload). Fn raising = retryable failure.
        halt_check: if provided and returns True, worker stops cleanly at the
        next poll (jobs remain pending — the throat-cut handle, §0.5).
        Returns number of jobs completed.
        """
        owner = owner or f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        completed = 0
        idle_since = time.time()

        while True:
            if halt_check and halt_check():
                logger.info("halt signal — worker %s stopping at safe point", owner)
                return completed

            job = self._claim_next(owner)
            if job is None:
                # CRITICAL: only idle-exit when the queue is TRULY empty.
                # Jobs sitting in retry backoff (pending, future next_run_at)
                # must keep the worker alive — exiting here would be a silent
                # failure of exactly the kind P0 exists to eliminate.
                if not self._has_future_work():
                    if idle_exit_after is not None and time.time() - idle_since > idle_exit_after:
                        return completed
                await asyncio.sleep(poll_interval)
                continue
            idle_since = time.time()

            handler = handlers.get(job["kind"])
            if handler is None:
                # Unregistered kind: no handler will ever appear; dead-letter fast.
                self._finish(job["id"], "dead", f"no handler for kind '{job['kind']}'")
                audit("job.dead", subject=job["job_id"], kind=job["kind"],
                      reason="no_handler", attempts=job["attempts"])
                if dead_letter_ping:
                    dead_letter_ping({"job_id": job["job_id"], "kind": job["kind"],
                                      "error": "no handler", "payload": json.loads(job["payload"])})
                continue

            try:
                payload = json.loads(job["payload"])
            except Exception:
                self._finish(job["id"], "dead", "payload not valid JSON")
                continue

            try:
                result = await handler(payload)
                self._finish(job["id"], "done")
                completed += 1
                audit("job.done", subject=job["job_id"], kind=job["kind"], attempts=job["attempts"])
            except asyncio.CancelledError:
                # Halt/interrupt mid-flight: release the lease, job stays retryable.
                self._finish(job["id"], "pending", "cancelled", backoff=1.0)
                audit("job.cancelled", subject=job["job_id"], kind=job["kind"])
                raise
            except Exception as e:
                from ghost.core.halt import HaltActive
                if isinstance(e, HaltActive):
                    # The throat-cut handle: stop the worker AT this safe point.
                    # The job is paused with its retry budget intact — a resumed
                    # run picks up exactly here. Never burn attempts on halts.
                    self._finish(job["id"], "pending", f"halt-paused: {e}", backoff=0.5,
                                 count_attempt=False)
                    audit("job.halt_paused", subject=job["job_id"], kind=job["kind"])
                    return completed
                attempts = job["attempts"]
                if attempts >= job["max_attempts"]:
                    self._finish(job["id"], "dead", str(e))
                    audit("job.dead", subject=job["job_id"], kind=job["kind"],
                          attempts=attempts, error=str(e)[:500])
                    if dead_letter_ping:
                        dead_letter_ping({"job_id": job["job_id"], "kind": job["kind"],
                                          "error": str(e), "payload": payload})
                else:
                    backoff = min(300.0, (2 ** attempts) + random.uniform(0, 1))
                    self._finish(job["id"], "pending", str(e), backoff=backoff)
                    audit("job.retry", subject=job["job_id"], kind=job["kind"],
                          attempt=attempts, backoff_s=round(backoff, 1), error=str(e)[:300])

    # ------------------------------------------------------------- introspection

    def stats(self) -> Dict[str, int]:
        with closing(self._conn()) as conn, conn:
            rows = conn.execute("SELECT state, COUNT(*) as n FROM jobs GROUP BY state").fetchall()
        counts = {r["state"]: r["n"] for r in rows}
        # Always report the full picture — missing keys read as zero failures.
        return {state: counts.get(state, 0) for state in JOB_STATES}

    def dead_letters(self, limit: int = 50) -> List[Dict[str, Any]]:
        with closing(self._conn()) as conn, conn:
            rows = conn.execute(
                "SELECT job_id, kind, attempts, last_error, updated_at FROM jobs "
                "WHERE state = 'dead' ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
