"""Typed Delegation Task Contract and Structured Result Protocol for GHOST.

Conforms to DETERMINISTIC AGENT ROUTING & CONTEXT ISOLATION §5 and §6:
- Formal DelegationTaskContract enforcing boundaries, permissions, tool scope, and budgets.
- Structured DelegationResult encapsulating findings, evidence, artifacts, and concise summary.
- Context budget accounting and isolation ensuring intermediate logs stay out of Central GHOST.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DelegationTaskContract:
    """Formal contract passed to a specialized agent upon task delegation."""
    task_id: str
    assigned_agent: str
    domain: str
    objective: str
    inputs: Dict[str, Any]
    parent_task_id: Optional[str] = None
    constraints: Dict[str, Any] = field(default_factory=dict)
    permissions: List[str] = field(default_factory=list)
    tool_scope: List[str] = field(default_factory=list)
    deadline_seconds: float = 180.0
    budget: Dict[str, Any] = field(default_factory=lambda: {"max_steps": 10, "max_tokens": 10000})
    expected_output_schema: Dict[str, Any] = field(default_factory=dict)
    verification_criteria: List[str] = field(default_factory=list)
    execution_order: int = 1
    dependencies: List[str] = field(default_factory=list)
    raw_user_request: str = ""
    input_integrity: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DelegationTaskContract:
        return cls(**data)


@dataclass
class DelegationResult:
    """Structured result returned by a specialist agent to Central GHOST."""
    task_id: str
    agent_id: str
    status: str                         # "COMPLETED", "FAILED", "BLOCKED"
    findings: Dict[str, Any]
    summary: str                        # Concise, high-signal summary for central context (< 300 words)
    parent_task_id: Optional[str] = None
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    source_references: List[str] = field(default_factory=list)
    actions_performed: List[str] = field(default_factory=list)
    files_or_records_created: List[str] = field(default_factory=list)
    verification_results: Dict[str, Any] = field(default_factory=dict)
    remaining_work: Optional[str] = None
    failure_details: Optional[str] = None
    raw_execution_logs: List[str] = field(default_factory=list)  # Retained in store, omitted from central context
    completed_at: float = field(default_factory=time.time)
    duration_s: float = 0.0

    def to_dict(self, include_logs: bool = False) -> Dict[str, Any]:
        res = asdict(self)
        if not include_logs:
            res.pop("raw_execution_logs", None)
        return res

    def to_central_summary(self) -> str:
        """Returns clean markdown block suitable for injection into Central GHOST context."""
        status_badge = f"[{self.status}]"
        lines = [
            f"### Agent Report: {self.agent_id} {status_badge}",
            f"**Objective Task:** `{self.task_id}`",
            f"**Summary:** {self.summary}",
        ]
        if self.files_or_records_created:
            lines.append(f"**Records/Files:** {', '.join(self.files_or_records_created)}")
        if self.actions_performed:
            lines.append(f"**Actions:** {', '.join(self.actions_performed)}")
        if self.source_references:
            lines.append(f"**Sources:** {', '.join(self.source_references[:5])}")
        if self.verification_results:
            v_status = "VERIFIED" if self.verification_results.get("verified", False) else "UNVERIFIED"
            lines.append(f"**Verification:** {v_status} ({self.verification_results.get('evidence_type', 'none')})")
        if self.failure_details:
            lines.append(f"**Failure/Blocker:** {self.failure_details}")
        if self.remaining_work:
            lines.append(f"**Remaining Work:** {self.remaining_work}")
        return "\n".join(lines)
