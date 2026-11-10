"""Git Repository Collector for GHOST Development Intelligence.

Conforms to ARCHITECTURE_TARGET.md §Layer 1 & §2 Development Intelligence:
- Passively monitors active repository branches, commits, and uncommitted working-tree changes.
- Emits dev.git.change events into EventBus when code modifications or commit updates occur.
- Non-blocking execution with subprocess timeouts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.paths import repo_root

logger = logging.getLogger("ghost.collectors.git")


@dataclass(frozen=True)
class GitRepoState:
    """Snapshot of a Git repository's working tree and HEAD state."""
    repo_path: str
    branch: str
    head_commit: str
    is_dirty: bool
    modified_files: List[str] = field(default_factory=list)
    untracked_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GitCollector:
    """Passively samples Git repository state and publishes change events."""

    def __init__(
        self,
        repo_paths: Optional[List[str]] = None,
        event_bus: Optional[EventBus] = None,
    ):
        self.repo_paths = repo_paths or [repo_root()]
        self.event_bus = event_bus or get_event_bus()
        self._last_states: Dict[str, GitRepoState] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def sample_repo(self, path: str) -> Optional[GitRepoState]:
        """Samples the branch, commit, and porcelain status for a single repository."""
        if not os.path.exists(os.path.join(path, ".git")):
            return None

        try:
            # 1. Branch
            res_br = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            branch = res_br.stdout.strip() or "HEAD (detached)"

            # 2. HEAD commit
            res_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            head_commit = res_head.stdout.strip()[:10]

            # 3. Porcelain status
            res_st = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            modified = []
            untracked = []
            for line in res_st.stdout.splitlines():
                if not line.strip():
                    continue
                status_code = line[:2]
                file_path = line[3:].strip()
                if status_code.startswith("?") or status_code.endswith("?"):
                    untracked.append(file_path)
                else:
                    modified.append(file_path)

            is_dirty = len(modified) > 0 or len(untracked) > 0

            return GitRepoState(
                repo_path=os.path.abspath(path),
                branch=branch,
                head_commit=head_commit,
                is_dirty=is_dirty,
                modified_files=sorted(modified),
                untracked_files=sorted(untracked),
            )
        except Exception as e:
            logger.debug(f"Git inspection failed for {path}: {e}")
            return None

    async def sample_and_publish(self) -> List[Event]:
        """Samples all watched repositories and emits dev.git.change when state differs."""
        events_emitted: List[Event] = []
        loop = asyncio.get_running_loop()

        for path in self.repo_paths:
            state = await loop.run_in_executor(None, self.sample_repo, path)
            if not state:
                continue

            last = self._last_states.get(state.repo_path)
            self._last_states[state.repo_path] = state

            # If this is the first observation or state changed
            if last is None or last != state:
                # Determine delta
                is_initial = last is None
                event = Event(
                    event_type="dev.git.change",
                    source="collector.git",
                    severity=EventSeverity.INFO,
                    payload={
                        "initial_sample": is_initial,
                        "repo_path": state.repo_path,
                        "branch": state.branch,
                        "head_commit": state.head_commit,
                        "is_dirty": state.is_dirty,
                        "modified_count": len(state.modified_files),
                        "untracked_count": len(state.untracked_files),
                        "modified_files": state.modified_files[:20],  # capped preview
                        "untracked_files": state.untracked_files[:20],
                    },
                    provenance=state.repo_path,
                )
                await self.event_bus.publish(event)
                events_emitted.append(event)

        return events_emitted

    async def run_loop(self, interval_s: float = 10.0) -> None:
        """Asynchronous background polling loop."""
        self._running = True
        logger.info(f"GitCollector started watching {len(self.repo_paths)} repos (interval {interval_s}s)")
        try:
            while self._running:
                try:
                    await self.sample_and_publish()
                except Exception as e:
                    logger.error(f"Error in GitCollector cycle: {e}")
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            logger.info("GitCollector background task cancelled")
        finally:
            self._running = False

    def start(self, interval_s: float = 10.0) -> asyncio.Task:
        """Spawns run_loop in active event loop."""
        if self._task and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run_loop(interval_s=interval_s))
        return self._task

    def stop(self) -> None:
        """Stops background loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
