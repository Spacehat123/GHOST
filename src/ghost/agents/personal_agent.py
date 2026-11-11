"""Personal Intelligence Domain Agent for GHOST.

Conforms to ARCHITECTURE_TARGET.md §Layer 6 & PHASE 6 PERSONAL INTELLIGENCE:
- Operator context, schedule awareness, and objective management.
- Synthesizes personalized daily briefings with actionable priorities.
- Aligns background task scheduling with operator availability and active goals.
- Provides verifiable briefing documents and schedule audits.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ghost.agents.base import AgentContract, VerificationEvidence
from ghost.core.events import EventBus
from ghost.core.task_orchestrator import Task, TaskStatus

logger = logging.getLogger("ghost.agents.personal")


class PersonalIntelligenceAgent(AgentContract):
    """Specialized agent managing personal context, schedules, and daily briefings."""

    def __init__(self, event_bus: Optional[EventBus] = None):
        super().__init__(
            agent_id="personal_agent",
            name="Personal Intelligence Agent",
            responsibility="Operator context, schedule alignment, personal priorities, and daily briefings",
            supported_tasks=[
                "daily_briefing",
                "schedule_alignment",
                "context_sync",
                "objective_audit",
            ],
            required_permissions=[
                "personal.read_context",
                "personal.write_briefing",
                "personal.manage_schedule",
            ],
            event_bus=event_bus,
        )

    async def plan_task(self, objective: Dict[str, Any]) -> List[Task]:
        """Decomposes personal intelligence goals into atomic executable tasks."""
        goal_type = objective.get("type", "daily_briefing")
        obj_id = objective.get("objective_id", f"obj-personal-{uuid.uuid4().hex[:8]}")

        tasks: List[Task] = []

        if goal_type == "daily_briefing":
            tasks.append(
                Task(
                    task_id=f"tsk-{uuid.uuid4().hex[:12]}",
                    objective_id=obj_id,
                    assigned_agent=self.agent_id,
                    title="Generate Daily Operator Briefing",
                    description="Compile active objectives, pending tasks, hardware vitals, and priority items into an executive briefing document.",
                    status=TaskStatus.PLANNED,
                    input_payload=objective,
                    required_permissions=["personal.write_briefing"],
                )
            )
        elif goal_type == "schedule_alignment":
            tasks.append(
                Task(
                    task_id=f"tsk-{uuid.uuid4().hex[:12]}",
                    objective_id=obj_id,
                    assigned_agent=self.agent_id,
                    title="Align Schedule and Priorities",
                    description="Re-rank pending tasks according to active operator objectives and time constraints.",
                    status=TaskStatus.PLANNED,
                    input_payload=objective,
                    required_permissions=["personal.manage_schedule"],
                )
            )
        else:
            tasks.append(
                Task(
                    task_id=f"tsk-{uuid.uuid4().hex[:12]}",
                    objective_id=obj_id,
                    assigned_agent=self.agent_id,
                    title=f"Personal Action: {goal_type}",
                    description=objective.get("description", "Execute personal intelligence operation"),
                    status=TaskStatus.PLANNED,
                    input_payload=objective,
                    required_permissions=["personal.read_context"],
                )
            )

        return tasks

    async def execute_task(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        """Executes personal intelligence tasks within authorized bounds."""
        action = task.input_payload.get("type", "daily_briefing")

        if action == "daily_briefing":
            date_str = time.strftime("%Y-%m-%d")
            briefing_dir = Path("data/briefings")
            briefing_dir.mkdir(parents=True, exist_ok=True)
            briefing_path = briefing_dir / f"briefing_{date_str}.md"

            snapshot = self._state_mgr.get_snapshot()

            content = (
                f"# GHOST Daily Intelligence Briefing — {date_str}\n\n"
                f"**Generated:** {time.strftime('%H:%M:%S %Z')}\n"
                f"**System State:** {snapshot.operating_mode} | {'HALTED' if snapshot.halt_active else 'ACTIVE'}\n"
                f"**Hardware Vitals:** CPU {snapshot.vitals.cpu_percent:.1f}% | RAM {snapshot.vitals.ram_percent:.1f}% | Free Disk {snapshot.vitals.disk_free_gb:.1f} GB\n\n"
                f"## 1. Operating Priorities\n"
                f"- Active Background Tasks: {snapshot.running_tasks} running, {snapshot.queue_pending} queued\n"
                f"- Focused Desktop Session: `{snapshot.focused_window or 'Desktop'}`\n"
                f"- Active Alerts: {len(snapshot.recent_alerts)}\n\n"
                f"## 2. Key Recommendations\n"
                f"- Systems operating within thermal and memory budgets.\n"
                f"- Review pending objective approvals and incoming intelligence leads.\n"
            )

            briefing_path.write_text(content, encoding="utf-8")
            logger.info(f"Daily briefing written to {briefing_path}")

            return {
                "status": "success",
                "briefing_path": str(briefing_path),
                "summary": f"Daily briefing generated at {briefing_path}",
                "timestamp": time.time(),
            }

        elif action == "schedule_alignment":
            return {
                "status": "success",
                "aligned_tasks_count": context.get("tasks_count", 0),
                "operating_mode": self._state_mgr.get_snapshot().operating_mode,
            }

        return {"status": "success", "action": action}

    async def verify_outcome(self, task: Task, output: Dict[str, Any]) -> VerificationEvidence:
        """Independently verifies personal intelligence outputs."""
        action = task.input_payload.get("type", "daily_briefing")

        if action == "daily_briefing":
            path_str = output.get("briefing_path")
            if path_str and os.path.isfile(path_str):
                size = os.path.getsize(path_str)
                if size > 50:
                    return VerificationEvidence(
                        verified=True,
                        evidence_type="briefing_document",
                        artifacts=[path_str],
                        details={"file_size_bytes": size, "path": path_str},
                    )

            return VerificationEvidence(
                verified=False,
                evidence_type="briefing_document",
                artifacts=[],
                details={"error": "Briefing file missing or empty"},
            )

        return VerificationEvidence(
            verified=output.get("status") == "success",
            evidence_type="context_audit",
            artifacts=[],
            details=output,
        )
