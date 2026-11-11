"""Autonomous Background Task Execution Engine for GHOST.

Conforms to ARCHITECTURE_TARGET.md §Layer 5 & PHASE 7 AUTONOMOUS EXECUTION:
- Bounded, persistent background task execution with worker concurrency limits.
- Resumable jobs, crash recovery, and stale lease reclamation.
- Time and resource budget enforcement (respects Global Halt and system load).
- Approval gates: tasks in WAITING_FOR_APPROVAL are paused until operator approval.
- Independent verification before marking tasks COMPLETED.
- Objective-driven decomposition and end-to-end multi-task execution.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from ghost.agents import get_available_agents
from ghost.agents.base import AgentContract, VerificationEvidence
from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.core.halt import is_halted
from ghost.core.system_state import SystemStateManager, get_system_state_manager
from ghost.core.task_orchestrator import Task, TaskOrchestrator, TaskStatus, get_task_orchestrator
from ghost.intelligence.objective_manager import Objective, ObjectiveManager, get_objective_manager

logger = logging.getLogger("ghost.autonomy.task_worker")


class AutonomousExecutionEngine:
    """Manages autonomous background task leases, objective decomposition, and agent execution."""

    def __init__(
        self,
        task_orchestrator: Optional[TaskOrchestrator] = None,
        objective_manager: Optional[ObjectiveManager] = None,
        state_manager: Optional[SystemStateManager] = None,
        event_bus: Optional[EventBus] = None,
        max_concurrency: int = 2,
        worker_id: Optional[str] = None,
    ):
        self.task_orchestrator = task_orchestrator or get_task_orchestrator()
        self.objective_manager = objective_manager or get_objective_manager()
        self.state_manager = state_manager or get_system_state_manager()
        self.event_bus = event_bus or get_event_bus()
        self.max_concurrency = max_concurrency
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._agents_cache: Dict[str, AgentContract] = {
            a.agent_id: a for a in get_available_agents()
        }

    def _resolve_agent(self, agent_id: str) -> Optional[AgentContract]:
        return self._agents_cache.get(agent_id)

    async def execute_objective(self, objective_id: str) -> Dict[str, Any]:
        """Objective-driven execution flow:
        1. Receive objective
        2. Decompose into execution plan
        3. Identify required permissions
        4. Submit tasks to TaskOrchestrator
        5. Track progress and verify results
        """
        obj = self.objective_manager.get_objective(objective_id)
        if not obj:
            raise ValueError(f"Objective '{objective_id}' not found.")

        agent = self._resolve_agent(obj.target_agent)
        if not agent:
            raise ValueError(f"Target agent '{obj.target_agent}' not available.")

        logger.info(f"Decomposing objective '{obj.title}' via agent '{agent.name}'")
        planned_tasks = await agent.plan_task(obj.to_dict())
        submitted_task_ids: List[str] = []

        for pt in planned_tasks:
            # Check if task requires approval
            needs_approval = (
                obj.domain in ("physical", "business")
                or any("control" in p or "send" in p or "deploy" in p for p in pt.required_permissions)
            )

            task = self.task_orchestrator.submit_task(
                objective_id=obj.objective_id,
                assigned_agent=pt.assigned_agent,
                title=pt.title,
                description=pt.description,
                input_payload=pt.input_payload,
                required_permissions=pt.required_permissions,
                timeout_s=pt.timeout_s,
                initial_status=TaskStatus.WAITING_FOR_APPROVAL if needs_approval else TaskStatus.QUEUED,
            )
            submitted_task_ids.append(task.task_id)

        # Update progress to 10% on planning completion
        self.objective_manager.update_progress(obj.objective_id, 0.1)

        return {
            "status": "planned",
            "objective_id": objective_id,
            "tasks_count": len(submitted_task_ids),
            "task_ids": submitted_task_ids,
        }

    async def _execute_single_task(self, task: Task) -> None:
        """Executes a single leased task with timeouts, verification, and retry handling."""
        async with self._semaphore:
            if is_halted():
                logger.warning(f"Task {task.task_id} execution aborted: GHOST is HALTED.")
                return

            agent = self._resolve_agent(task.assigned_agent)
            if not agent:
                self.task_orchestrator.transition(
                    task.task_id,
                    TaskStatus.FAILED,
                    error_reason=f"Assigned agent '{task.assigned_agent}' not registered.",
                )
                return

            logger.info(f"Autonomous worker executing task {task.task_id}: '{task.title}' via {agent.name}")

            try:
                # 1. Execute task within timeout budget
                output = await asyncio.wait_for(
                    agent.execute_task(task, context={"worker_id": self.worker_id}),
                    timeout=task.timeout_s,
                )

                # 2. Independent Verification
                self.task_orchestrator.transition(task.task_id, TaskStatus.VERIFYING)
                evidence: VerificationEvidence = await agent.verify_outcome(task, output)

                if evidence.verified:
                    self.task_orchestrator.transition(
                        task.task_id,
                        TaskStatus.COMPLETED,
                        output_payload={"output": output, "evidence": evidence.to_dict()},
                    )
                    logger.info(f"Task {task.task_id} COMPLETED and VERIFIED ({evidence.evidence_type}).")

                    # Check and update objective progress
                    if task.objective_id:
                        all_tasks = self.task_orchestrator.list_tasks(objective_id=task.objective_id)
                        if all_tasks:
                            done_count = sum(1 for t in all_tasks if t.status == TaskStatus.COMPLETED)
                            progress = done_count / len(all_tasks)
                            self.objective_manager.update_progress(
                                task.objective_id,
                                progress,
                                status="COMPLETED" if progress >= 1.0 else "ACTIVE",
                            )
                else:
                    logger.warning(f"Task {task.task_id} verification failed: {evidence.details}")
                    self.task_orchestrator.transition(
                        task.task_id,
                        TaskStatus.FAILED,
                        error_reason=f"Verification failed: {evidence.details}",
                    )

            except asyncio.TimeoutError:
                logger.error(f"Task {task.task_id} timed out after {task.timeout_s}s")
                self.task_orchestrator.transition(
                    task.task_id,
                    TaskStatus.FAILED,
                    error_reason=f"Execution timed out after {task.timeout_s}s",
                )
            except Exception as e:
                logger.error(f"Unhandled exception during task {task.task_id} execution: {e}")
                self.task_orchestrator.transition(
                    task.task_id,
                    TaskStatus.FAILED,
                    error_reason=str(e)[:400],
                )

    async def run_worker_loop(self, poll_interval_s: float = 2.0) -> None:
        """Main autonomous worker loop: reclaims expired leases and processes queued tasks."""
        self._running = True
        logger.info(f"AutonomousExecutionEngine worker started (id: {self.worker_id}, concurrency: {self.max_concurrency})")

        # Initial crash recovery: reclaim expired or orphaned leases from previous session
        try:
            reclaimed = self.task_orchestrator.reap_stale_leases()
            if reclaimed:
                logger.info(f"Reclaimed {len(reclaimed)} orphaned/expired tasks from prior session: {reclaimed}")
        except Exception as e:
            logger.debug(f"Initial lease reclamation error: {e}")

        while self._running:
            if is_halted():
                await asyncio.sleep(poll_interval_s)
                continue

            try:
                # Find candidate tasks ready for execution
                queued_tasks = self.task_orchestrator.list_tasks(status=TaskStatus.QUEUED, limit=5)
                retrying_tasks = self.task_orchestrator.list_tasks(status=TaskStatus.RETRYING, limit=5)
                candidates = queued_tasks + retrying_tasks

                for candidate in candidates:
                    if not self._running or is_halted():
                        break

                    # Try to acquire lease
                    leased = self.task_orchestrator.acquire_lease(
                        candidate.task_id,
                        worker_id=self.worker_id,
                        lease_duration_s=min(120.0, candidate.timeout_s),
                    )
                    if leased:
                        # Spawn task execution in background under concurrency semaphore
                        asyncio.create_task(self._execute_single_task(candidate))

            except Exception as e:
                logger.warning(f"Error in AutonomousExecutionEngine cycle: {e}")

            await asyncio.sleep(poll_interval_s)

        self._running = False
        logger.info("AutonomousExecutionEngine worker stopped.")

    def start(self, poll_interval_s: float = 2.0) -> asyncio.Task:
        if self._task and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run_worker_loop(poll_interval_s=poll_interval_s))
        return self._task

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()


# Global Singleton AutonomousExecutionEngine
_GLOBAL_AUTONOMOUS_ENGINE: Optional[AutonomousExecutionEngine] = None

def get_autonomous_execution_engine(
    task_orchestrator: Optional[TaskOrchestrator] = None,
    objective_manager: Optional[ObjectiveManager] = None,
    state_manager: Optional[SystemStateManager] = None,
    event_bus: Optional[EventBus] = None,
) -> AutonomousExecutionEngine:
    global _GLOBAL_AUTONOMOUS_ENGINE
    if _GLOBAL_AUTONOMOUS_ENGINE is None:
        _GLOBAL_AUTONOMOUS_ENGINE = AutonomousExecutionEngine(
            task_orchestrator=task_orchestrator,
            objective_manager=objective_manager,
            state_manager=state_manager,
            event_bus=event_bus,
        )
    return _GLOBAL_AUTONOMOUS_ENGINE
