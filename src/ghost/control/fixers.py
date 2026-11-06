"""CONTROL PLANE — the customer-report fixer (Operator's 11 P7, goal 7).

A customer reports a break (or a monitor flags one): incident → sidecar fix
→ INDEPENDENT verification → STAGED for the operator → approval applies it.

The tier rule is structural, not configurable: an LLM-produced patch is
NEVER "mechanical", so it never auto-applies — whatever the autonomy ladder
says, the verified patch is STAGED (T3: staged + operator approval). What
the ladder gates is whether the fixer may try at all (spawn refusals still
run through the sidecar gate, halt included).

Model honesty: with no local model reachable, the fixer escalates the
incident to the operator instead of pretending to try. The control
machinery is fully testable with injected workers (no model needed).
"""
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ghost.core.audit import audit
from ghost.paths import data_dir

WORKER_SCRIPT = os.path.join(os.path.dirname(__file__), "fixer_worker.py")
DEFAULT_MODEL = "qwen2.5:7b"


@dataclass
class FixIssue:
    key: str
    site_slug: str
    description: str
    scope_files: List[str]
    check_command: str                    # exact pass/fail; {file} substituted
    source: str = "customer_report"       # customer_report | monitor | operator


def _staged_dir() -> str:
    return os.environ.get("GHOST_FIXER_STAGED") or os.path.join(data_dir(), "fixer_staged")


def model_available() -> bool:
    try:
        from ghost.core.model_routing import check_local_model
        return check_local_model().get("status") == "available"
    except Exception:
        return False


def real_worker(spec, ctx: Dict[str, Any], is_cancelled) -> Dict[str, Any]:
    """The SidecarManager worker callable that runs the STANDALONE script as
    a real subprocess under the sandbox env — no ghost module is importable
    where it runs, and the only network call it can make is local Ollama."""
    from ghost.core.model_routing import load_routing
    brief_in = {
        "worktree": ctx["worktree"],
        "files": {k: v for k, v in ctx["files"].items()},
        "brief": spec.brief,
        "model": os.environ.get("GHOST_FIXER_MODEL", DEFAULT_MODEL),
        "ollama_url": os.environ.get("OLLAMA_BASE_URL")
                      or load_routing().get("local_ollama_url", "http://127.0.0.1:11434"),
        "budget_s": max(5.0, spec.budget.max_seconds - (time.time() - spec.created_at)),
    }
    brief_path = os.path.join(spec.worktree, "brief.json")
    with open(brief_path, "w", encoding="utf-8") as f:
        json.dump(brief_in, f)
    proc = subprocess.run(
        [sys.executable, WORKER_SCRIPT, brief_path],
        env=ctx["env"], capture_output=True, text=True,
        timeout=max(10.0, brief_in["budget_s"]))
    if proc.returncode != 0:
        return {"patch": {}, "evidence": f"worker exited {proc.returncode}: "
                                         f"{proc.stderr[:200]}", "tool_calls": 1, "tokens": 0}
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    return {"patch": out.get("patch", {}), "evidence": out.get("evidence", ""),
            "tool_calls": int(out.get("tool_calls", 1)),
            "tokens": int(out.get("tokens", 0))}


def diagnose_and_fix(issue: FixIssue, manager=None, worker=None,
                     ladder=None) -> Dict[str, Any]:
    """The full pipeline for one reported issue. Returns an honest status:
    escalated | unresolved | staged. NEVER auto-applies to a live site."""
    audit("fixer.report_received", subject=issue.key, source=issue.source,
          site=issue.site_slug, description=issue.description[:200])
    from ghost.control.sidecars import SidecarManager, SidecarBudget
    manager = manager if manager is not None else SidecarManager(ladder=ladder)

    if worker is None and not model_available():
        audit("fixer.escalated", subject=issue.key, reason="no_model_available",
              site=issue.site_slug,
              ping="(c) unverifiable: no local model reachable — the reported "
                   "issue needs the operator (or: start Ollama / set GHOST_FIXER_MODEL)")
        return {"status": "escalated", "reason": "no_model_available"}

    spawn = manager.spawn(
        brief=f"[{issue.site_slug}] {issue.description}",
        scope_files=list(issue.scope_files),
        check_command=issue.check_command,
        issue_key=issue.key,
        budget=SidecarBudget(max_seconds=float(os.environ.get("GHOST_FIXER_BUDGET_S", "180"))))
    if "refused" in spawn:
        audit("fixer.escalated", subject=issue.key, reason=spawn["refused"][:120],
              site=issue.site_slug,
              ping="(c) unverifiable: fixer refused to spawn — operator attention needed")
        return {"status": "escalated", "reason": spawn["refused"]}

    spec = spawn["spec"]
    result = manager.run(spec, worker or real_worker)
    if result.status != "completed":
        audit("fixer.unresolved", subject=issue.key, sidecar_status=result.status,
              notes=result.notes[:3], site=issue.site_slug,
              ping="(c) unverifiable: sidecar fix did not complete — issue stays open")
        return {"status": "unresolved", "sidecar_status": result.status,
                "notes": result.notes}

    verification = manager.merge(spec, result, apply=False)   # verify, NEVER apply
    if not verification.get("verified"):
        audit("fixer.unresolved", subject=issue.key, reason="verifier_failed",
              evidence=str(verification.get("evidence", ""))[:150], site=issue.site_slug,
              ping="(c) unverifiable: the sidecar's patch FAILED verification — discarded")
        return {"status": "unresolved", "reason": "verifier_failed",
                "evidence": verification.get("evidence")}

    staged = stage_patch(issue, verification["patch"], verification["evidence"])
    audit("fixer.staged", subject=issue.key, staged_id=staged["staged_id"],
          site=issue.site_slug, files=sorted(verification["patch"].keys()),
          ping="(c) unverifiable: a VERIFIED fix is staged — your approval applies it "
               "(ghost fixer-approve " + staged["staged_id"] + ")")
    return {"status": "staged", "staged_id": staged["staged_id"],
            "dir": staged["dir"], "evidence": verification["evidence"]}


def stage_patch(issue: FixIssue, patch: Dict[str, str], evidence: str) -> Dict[str, Any]:
    """Persist the verified patch as an approval package: the patched file
    copies + EVIDENCE.json (what broke, what was tried, the verifier's own
    output, and the EXACT live paths approval would write to)."""
    staged_id = f"fix-{uuid.uuid4().hex[:10]}"
    d = os.path.join(_staged_dir(), staged_id)
    os.makedirs(d, exist_ok=True)
    files_dir = os.path.join(d, "files")
    os.makedirs(files_dir, exist_ok=True)
    for name, content in patch.items():
        with open(os.path.join(files_dir, name), "w", encoding="utf-8") as f:
            f.write(content)
    doc = {
        "staged_id": staged_id, "created_at": time.time(), "status": "staged",
        "issue": {"key": issue.key, "site_slug": issue.site_slug,
                  "description": issue.description[:1000], "source": issue.source,
                  "scope_files": list(issue.scope_files),
                  "check_command": issue.check_command},
        "evidence": evidence[:2000],
        "patched_files": sorted(patch.keys()),
    }
    with open(os.path.join(d, "EVIDENCE.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    return {"staged_id": staged_id, "dir": d}


def approve_staged(staged_id: str, by: str = "operator") -> Dict[str, Any]:
    """Operator-only: apply a VERIFIED staged patch to the live files.
    Refuses while halted (even operator clicks do nothing while frozen),
    refuses if a target file changed identity under us, audits everything."""
    from ghost.core.halt import is_halted
    d = os.path.join(_staged_dir(), staged_id.strip())
    doc_path = os.path.join(d, "EVIDENCE.json")
    try:
        with open(doc_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {"applied": False, "reason": f"staged fix {staged_id!r} not found"}
    if doc.get("status") == "applied":
        return {"applied": False, "reason": "already applied (one action per package)"}
    if is_halted():
        audit("fixer.approve_refused", subject=staged_id, reason="halt")
        return {"applied": False, "reason": "halt active — nothing applies while frozen"}

    applied: List[str] = []
    for name in doc.get("patched_files", []):
        target = None
        for p in doc["issue"]["scope_files"]:
            if os.path.basename(p) == name:
                target = p
                break
        if not target or not os.path.exists(target):
            return {"applied": False,
                    "reason": f"target for {name} is gone — re-diagnose instead of applying stale"}
        with open(os.path.join(d, "files", name), "r", encoding="utf-8") as f:
            content = f.read()
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        applied.append(target)
    doc["status"] = "applied"
    doc["applied_at"] = time.time()
    doc["applied_by"] = by
    with open(doc_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    audit("fixer.applied", subject=staged_id, by=by, files=[os.path.basename(p) for p in applied])
    return {"applied": True, "staged_id": staged_id, "files": applied}


def list_staged() -> List[Dict[str, Any]]:
    """The approval queue for the deck (P9 renders it)."""
    out: List[Dict[str, Any]] = []
    base = _staged_dir()
    if not os.path.isdir(base):
        return out
    for sid in sorted(os.listdir(base)):
        try:
            with open(os.path.join(base, sid, "EVIDENCE.json"), "r", encoding="utf-8") as f:
                doc = json.load(f)
            out.append({"staged_id": doc["staged_id"], "status": doc.get("status"),
                        "site": doc["issue"].get("site_slug"),
                        "description": doc["issue"].get("description", "")[:120],
                        "files": doc.get("patched_files", []),
                        "created_at": doc.get("created_at")})
        except (OSError, ValueError, KeyError):
            continue
    return out
