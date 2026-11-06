"""CONTROL PLANE — sidecar fixers (T8, brief §3b): short-lived sub-agents for
SMALL, bounded fixes — spawned by the control plane, never by the brain.

The contract, enforced here (not in prompts):
  - spawn record: brief + scope lock (file allowlist, lock vs main/other
    sidecars) + isolated workspace (a copy — never the live files) + budget
    (seconds / tool calls) + depth 1 (a sidecar cannot spawn sidecars)
  - NO credentials: the worker's environment is the agy sandbox's
    build_safe_env() — credential-shaped vars are stripped at the source,
    PYTHONPATH is not allowlisted so ghost's own modules (effects plane
    included) are not importable from the worker at all
  - returns a patch + evidence (the check's output); it NEVER merges
  - merge path: the VERIFIER independently re-runs the check on the patched
    copy; pass + scope-respected → control applies the patch and audits;
    fail/timeout/out-of-scope → discarded, logged, retried once, escalated
  - halt kills every sidecar within the watchdog tick and revokes its locks

What counts as small (the spawn gate): a bounded fix with an exact pass/fail
check. A request needing a decision, a new feature, or a human-reserved act
is refused and escalated instead.

The LLM fixer integration (a sandboxed agy call producing the patch) lands
with the dark run; this module IS the control machinery the fixer must pass
through — tests inject worker callables.
"""
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ghost.core.audit import audit

MAX_CONCURRENT = 3
MAX_PER_ISSUE = 2
HALT_POLL_S = 0.25          # halt is honored within <=2 * this + worker tick
SMALL_FIX_HINTS = ("broken link", "failing test", "typo", "stale cache",
                   "broken image", "wrong path", "missing alt")


@dataclass
class SidecarBudget:
    max_seconds: float = 120.0
    max_tool_calls: int = 20
    tokens_used: int = 0          # reported by the worker, trusted for cap only


@dataclass
class SidecarSpec:
    sidecar_id: str
    issue_key: str
    brief: str
    scope_files: List[str]        # absolute paths the patch may touch
    check_command: str            # the exact pass/fail check (exit 0 = fixed)
    worktree: str                 # isolated copies of the scoped files
    budget: SidecarBudget
    created_at: float = field(default_factory=time.time)


@dataclass
class SidecarResult:
    sidecar_id: str
    status: str                   # completed | discarded | timeout | blocked | halted | escalated
    patch: Dict[str, str]         # relpath -> new content (EMPTY unless completed)
    evidence: str                 # the check's output
    notes: List[str] = field(default_factory=list)


def _worktree_root() -> str:
    return os.environ.get("GHOST_SIDECAR_WORKROOT") or os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
        "data", "sidecars")


class SidecarManager:
    """The control-plane spawn authority. One per process; state in memory —
    every action is audited so 'what did it change and why' stays answerable."""

    def __init__(self, ladder=None):
        self.active: Dict[str, SidecarSpec] = {}
        self.locks: Dict[str, str] = {}          # abs file path -> sidecar_id
        self.issue_counts: Dict[str, int] = {}
        self._depth = 0                          # depth-1 enforcement
        self.ladder = ladder

    # ------------------------------------------------------------------ spawn

    def spawn(self, brief: str, scope_files: List[str], check_command: str,
              issue_key: str, budget: Optional[SidecarBudget] = None) -> Dict[str, Any]:
        """Control-plane spawn gate. Returns {"spec": ...} or {"refused": reason}."""
        from ghost.core.halt import is_halted
        from ghost.core.sandbox import path_allowed
        if is_halted():
            audit("sidecar.spawn_refused", reason="halt")
            return {"refused": "halt active — nothing spawns"}
        if self._depth > 0:
            audit("sidecar.spawn_refused", reason="depth")
            return {"refused": "depth 1: sidecars cannot spawn sidecars"}
        if len(self.active) >= MAX_CONCURRENT:
            audit("sidecar.spawn_refused", reason="concurrency_cap")
            return {"refused": f"concurrent sidecar cap ({MAX_CONCURRENT}) reached"}
        if self.issue_counts.get(issue_key, 0) >= MAX_PER_ISSUE:
            audit("sidecar.spawn_refused", reason="per_issue_cap", issue=issue_key)
            return {"refused": f"per-issue cap ({MAX_PER_ISSUE}) reached — escalate to operator"}
        if not brief or not check_command:
            return {"refused": "brief and an exact check are mandatory"}
        if not scope_files:
            return {"refused": "empty scope lock — a sidecar without scope is unbounded"}
        basenames = [os.path.basename(f) for f in scope_files]
        if len(set(basenames)) != len(basenames):
            return {"refused": "ambiguous scope: duplicate basenames would make the "
                               "patch unattributable"}
        for f in scope_files:
            if not path_allowed(f):
                audit("sidecar.spawn_refused", reason="scope_outside_sandbox", path=f[:200])
                return {"refused": f"scope file outside sandbox allowlist: {f}"}
        colliding = [f for f in scope_files if f in self.locks]
        if colliding:
            return {"refused": f"scope locked by another task/sidecar: {colliding}"}

        # isolated workspace: copies, never the live files
        sid = f"side_{uuid.uuid4().hex[:10]}"
        worktree = os.path.join(_worktree_root(), sid)
        os.makedirs(worktree, exist_ok=True)
        for f in scope_files:
            dest = os.path.join(worktree, os.path.basename(f))
            try:
                shutil.copy2(f, dest)
            except OSError as e:
                shutil.rmtree(worktree, ignore_errors=True)
                return {"refused": f"cannot snapshot {f}: {e}"}
            self.locks[f] = sid

        spec = SidecarSpec(sidecar_id=sid, issue_key=issue_key, brief=brief[:1000],
                           scope_files=list(scope_files), check_command=check_command,
                           worktree=worktree, budget=budget or SidecarBudget())
        self.active[sid] = spec
        self.issue_counts[issue_key] = self.issue_counts.get(issue_key, 0) + 1
        audit("sidecar.spawned", subject=sid, issue=issue_key,
              scope=[os.path.basename(f) for f in scope_files],
              brief=brief[:200], budget_seconds=spec.budget.max_seconds)
        return {"spec": spec}

    def release_locks(self, spec: SidecarSpec) -> None:
        for f, sid in list(self.locks.items()):
            if sid == spec.sidecar_id:
                del self.locks[f]
        self.active.pop(spec.sidecar_id, None)

    # -------------------------------------------------------------- worker env

    def worker_env(self) -> Dict[str, str]:
        """The sidecar's ENTIRE environment: sandbox allowlist, credentials
        stripped, no PYTHONPATH — ghost's own modules are not importable, so
        the effects plane is structurally unreachable from the worker."""
        from ghost.core.sandbox import build_safe_env
        env = build_safe_env()
        env.pop("PYTHONPATH", None)
        env.pop("GHOST_GRANT_SECRET", None)      # never, even if allowlisted later
        return env

    # --------------------------------------------------------------------- run

    def run(self, spec: SidecarSpec,
            worker: Callable[[SidecarSpec, Dict[str, str], Callable[[], bool]], Dict[str, Any]]
            ) -> SidecarResult:
        """Run the fixer. `worker(spec, worktree_files, is_cancelled)` returns
        {"patch": {relpath: content}, "evidence": str, "tool_calls": n,
        "tokens": n}. The manager owns the clock, the budget, the halt watch
        and the scope check. NEVER merges."""
        from ghost.core.halt import is_halted

        def is_cancelled() -> bool:
            return is_halted() or time.time() > spec.created_at + spec.budget.max_seconds

        env = self.worker_env()
        worktree_files = {os.path.basename(f): f for f in spec.scope_files}
        try:
            out = worker(spec, {"worktree": spec.worktree, "files": worktree_files,
                                "env": env}, is_cancelled)
        except Exception as e:
            self._discard(spec, f"worker crashed: {str(e)[:200]}")
            return SidecarResult(spec.sidecar_id, "discarded", {}, "",
                                 notes=[f"worker crashed: {str(e)[:200]}"])
        if is_halted():
            self._discard(spec, "halt tripped during sidecar run")
            return SidecarResult(spec.sidecar_id, "halted", {}, "", notes=["halt"])
        if time.time() > spec.created_at + spec.budget.max_seconds:
            self._discard(spec, "budget exhausted (time)")
            return SidecarResult(spec.sidecar_id, "timeout", {}, "", notes=["budget: time"])
        if int((out or {}).get("tool_calls", 0)) > spec.budget.max_tool_calls:
            self._discard(spec, "budget exhausted (tool calls)")
            return SidecarResult(spec.sidecar_id, "timeout", {}, "", notes=["budget: tool_calls"])

        patch = {rel: content for rel, content in (out or {}).get("patch", {}).items()}
        # SCOPE CHECK: a patch touching anything outside its lock is discarded
        scoped_basenames = {os.path.basename(f) for f in spec.scope_files}
        out_of_scope = [rel for rel in patch if rel not in scoped_basenames]
        if out_of_scope:
            self._discard(spec, f"out-of-scope edits: {out_of_scope[:3]}")
            return SidecarResult(spec.sidecar_id, "discarded", {}, "",
                                 notes=[f"out-of-scope edits refused: {out_of_scope[:3]}"])
        evidence = str((out or {}).get("evidence", ""))[:2000]
        audit("sidecar.patch_returned", subject=spec.sidecar_id,
              files=sorted(patch.keys()), evidence=evidence[:200])
        return SidecarResult(spec.sidecar_id, "completed", patch, evidence)

    def _discard(self, spec: SidecarSpec, reason: str) -> None:
        shutil.rmtree(spec.worktree, ignore_errors=True)
        self.release_locks(spec)
        audit("sidecar.discarded", subject=spec.sidecar_id, reason=reason[:200])

    # ------------------------------------------------------------------- merge

    def merge(self, spec: SidecarSpec, result: SidecarResult,
              verifier: Optional[Callable[[str], Dict[str, Any]]] = None,
              apply: bool = True) -> Dict[str, Any]:
        # note: `verifier` is vestigial — the INDEPENDENT check is the spec's
        # own check_command, re-run by THIS method on a scratch copy below.
        """Control-plane merge: the VERIFIER re-runs the check INDEPENDENTLY on
        the patched copy. Pass + in-scope → apply to the live files + audit.
        Anything else → discard (retry-once bookkeeping lives with the caller
        via issue_counts). Halt during merge refuses.

        apply=False (Operator's 11 P7): verify WITHOUT touching the live
        files — the customer-report fixer stages LLM patches for operator
        approval (an LLM patch is never 'mechanical', so it never
        auto-applies). Returns the verified patch for the staging package."""
        from ghost.core.halt import is_halted
        if result.status != "completed":
            return {"merged": False, "reason": f"sidecar not completed: {result.status}"}
        if is_halted():
            self._discard(spec, "halt tripped before merge")
            return {"merged": False, "reason": "halt active — patch discarded"}
        # apply to a scratch copy of the live files and verify THERE
        scratch = spec.worktree + "-verify"
        shutil.rmtree(scratch, ignore_errors=True)
        os.makedirs(scratch, exist_ok=True)
        try:
            for f in spec.scope_files:
                name = os.path.basename(f)
                content = result.patch.get(name)
                shutil.copy2(f, os.path.join(scratch, name))
                if content is not None:
                    with open(os.path.join(scratch, name), "w", encoding="utf-8") as fh:
                        fh.write(content)
            check = spec.check_command.replace("{file}", os.path.join(scratch, os.path.basename(spec.scope_files[0]))) \
                if "{file}" in spec.check_command else spec.check_command
            import subprocess
            proc = subprocess.run(check, shell=True, capture_output=True, text=True,
                                  timeout=30, cwd=scratch)
            evidence = f"exit={proc.returncode} out={proc.stdout[:400]} err={proc.stderr[:200]}"
        except subprocess.TimeoutExpired:
            self._discard(spec, "verifier timeout")
            return {"merged": False, "reason": "verifier timeout — discarded"}
        except Exception as e:
            self._discard(spec, f"verifier crashed: {str(e)[:150]}")
            return {"merged": False, "reason": f"verifier crashed: {str(e)[:150]}"}
        if proc.returncode != 0:
            self._discard(spec, f"verifier failed: {evidence[:150]}")
            return {"merged": False, "reason": "verifier failed — patch discarded",
                    "evidence": evidence}
        if not apply:
            shutil.rmtree(spec.worktree, ignore_errors=True)
            shutil.rmtree(scratch, ignore_errors=True)
            self.release_locks(spec)
            audit("sidecar.verified_not_applied", subject=spec.sidecar_id,
                  issue=spec.issue_key, evidence=evidence[:200])
            return {"verified": True, "applied": False, "evidence": evidence,
                    "patch": dict(result.patch)}
        # verified on the scratch copy → control applies to the live files
        try:
            for f in spec.scope_files:
                name = os.path.basename(f)
                if name in result.patch:
                    with open(f, "w", encoding="utf-8") as fh:
                        fh.write(result.patch[name])
        except OSError as e:
            self._discard(spec, f"apply failed: {e}")
            return {"merged": False, "reason": f"apply failed: {e}"}
        shutil.rmtree(spec.worktree, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)
        self.release_locks(spec)
        audit("sidecar.merged", subject=spec.sidecar_id, issue=spec.issue_key,
              files=[os.path.basename(f) for f in spec.scope_files],
              evidence=evidence[:200])
        return {"merged": True, "evidence": evidence}
