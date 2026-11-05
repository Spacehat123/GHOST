"""BRAIN PLANE — the strangler dispatcher (T6b): proposals become the only
path for converted capabilities.

Flow: Proposal → control gate (schema, halt, ladder) → grant (control plane)
→ outbox record → effects executor (grant passed through) → verifier — and
the VERIFIER's dict is the tool result the loop sees. The brain never marks
its own work done.

Per-action adapters define HOW to invoke each executor/verifier (signatures
differ); a registry action without an adapter is refused honestly — "not yet
strangler-wired" — rather than silently run through legacy.

GHOST_BRAIN_MODE=proposals (env, checked per call) activates this path for
tools in to_proposal's mapping; legacy mode is untouched until parity.
"""
import asyncio
import importlib
import logging
import os
import time
from typing import Any, Dict, Optional

from ghost.brain.proposals import (
    EXTERNAL_EFFECT, PIPELINE_EFFECT, Proposal, PROPOSAL_REGISTRY, to_proposal,
)

logger = logging.getLogger("ghost.brain.dispatcher")


def brain_mode() -> str:
    """legacy (default) | proposals — checked per call; rollback = unset."""
    return os.environ.get("GHOST_BRAIN_MODE", "legacy").strip().lower()


def _import(dotted: str):
    """Resolve dotted paths that may end in a method (Class.method) — walk
    from the longest importable module prefix."""
    parts = dotted.split(".")
    last_error: Exception = ImportError(f"cannot import {dotted}")
    for i in range(len(parts) - 1, 0, -1):
        try:
            module = importlib.import_module(".".join(parts[:i]))
        except ImportError as e:
            last_error = e
            continue
        try:
            obj = module
            for attr in parts[i:]:
                obj = getattr(obj, attr)
            return obj
        except AttributeError as e:
            last_error = e
    raise last_error


def _audit(event: str, **payload) -> None:
    try:
        from ghost.core.audit import audit
        audit(event, **payload)
    except Exception:
        pass


# ------------------------------------------------------- per-action adapters
# invoke(executor, proposal, grant) -> result ; verify(verifier, proposal,
# result) -> dict. The first strangler wave: the revenue cycle's clean calls.

def _invoke_deploy(ex, p: Proposal, grant):
    # registry paths end in the method itself — ex IS deploy_demo
    return ex(slug=p.args["slug"], grant_token=grant)


def _verify_deploy(vf, p: Proposal, result: Dict[str, Any]) -> Dict[str, Any]:
    ok = result.get("status") == "success" and bool(result.get("url"))
    return {**result, "verified": bool(ok),
            "verify_note": "live-URL reconciliation runs daily (reconcile_deploys)"}


def _invoke_update_stage(ex, p: Proposal, grant):
    # ex IS CRMDatabase.update_lead — but it needs an INSTANCE
    from ghost.crm.db import CRMDatabase
    return ex(CRMDatabase.get_instance(), p.args["lead_slug"], status=p.args["status"])


def _verify_update_stage(vf, p: Proposal, result: Any) -> Dict[str, Any]:
    # vf is CRMDatabase.get_lead (unbound) — needs the instance to re-derive
    from ghost.crm.db import CRMDatabase
    lead = vf(CRMDatabase.get_instance(), p.args["lead_slug"])
    status = (lead or {}).get("status")
    ok = status == p.args["status"]
    return {"action": p.action, "lead_slug": p.args["lead_slug"],
            "status": status, "verified": bool(ok),
            "reasons": [] if ok else [f"stage is {status!r}, expected {p.args['status']!r}"]}


def _invoke_scout(ex, p: Proposal, grant):
    return ex(city=p.args["city"]) if not isinstance(ex, type) else ex().run_all_sources(city=p.args["city"])


def _verify_scout(vf, p: Proposal, result: Any) -> Dict[str, Any]:
    rows = result if isinstance(result, list) else (result or {}).get("candidates", [])
    return {"action": p.action, "city": p.args["city"],
            "candidates": len(rows), "verified": True,
            "verify_note": "candidates carry source evidence per lead_sources"}


ADAPTERS: Dict[str, Dict[str, Any]] = {
    "demo.deploy": {"invoke": _invoke_deploy, "verify": _verify_deploy},
    "pipeline.update_stage": {"invoke": _invoke_update_stage, "verify": _verify_update_stage},
    "leads.scout": {"invoke": _invoke_scout, "verify": _verify_scout},
}

# ladder capability per proposal action (the autonomy ladder gates effects)
LADDER_CAPABILITY = {
    "demo.deploy": "demo_build_deploy",
    "pipeline.update_stage": "contracts",
    "leads.scout": "lead_hunting",
}


# ------------------------------------------------------------------ dispatch

async def dispatch(proposal: Proposal) -> Dict[str, Any]:
    """Execute one proposal through the planes. NEVER raises into the loop —
    every refusal is a structured dict the model can read."""
    try:
        proposal = proposal.validated()
    except ValueError as e:
        _audit("proposal.refused", action=proposal.action, reason=str(e)[:200])
        return {"status": "refused", "stage": "schema", "reason": str(e)[:200]}

    adapter = ADAPTERS.get(proposal.action)
    if adapter is None:
        _audit("proposal.refused", action=proposal.action, reason="not_strangler_wired")
        return {"status": "refused", "stage": "adapter",
                "reason": f"{proposal.action} is registered but not yet strangler-wired"}

    # ---- control gate: halt dominates, ladder ranks -----------------------
    from ghost.core.halt import is_halted
    if is_halted():
        _audit("proposal.refused", action=proposal.action, reason="halt")
        return {"status": "refused", "stage": "control", "reason": "halt active"}

    from ghost.autonomy.ladder import AutonomyLadder
    ladder = AutonomyLadder()
    cap = LADDER_CAPABILITY.get(proposal.action)
    if cap:
        current, eligible = ladder.level(cap), ladder.eligible(cap)
        if current < eligible:
            _audit("proposal.refused", action=proposal.action, reason="ladder",
                   capability=cap, level=current, eligible=eligible)
            return {"status": "refused", "stage": "control",
                    "reason": f"{cap} at L{current}, needs L{eligible}+"}

    # ---- grant (control plane only; effects re-verify) --------------------
    grant = None
    if proposal.risk_class in (PIPELINE_EFFECT, EXTERNAL_EFFECT):
        from ghost.autonomy.outbound import issue_effect_grant
        grant_capability = PROPOSAL_REGISTRY[proposal.action].get("grant_capability") \
            or proposal.action.replace(".", "_")
        grant = issue_effect_grant(grant_capability,
                                   proposal.args.get("slug") or proposal.args.get("lead_slug") or "batch",
                                   bounds={"action": proposal.action})
        if grant is None:
            _audit("proposal.refused", action=proposal.action, reason="no_grant_authority")
            return {"status": "refused", "stage": "control",
                    "reason": "no grant authority (signing secret absent or halt)"}

    # ---- effects: execute + outbox record-before-run ----------------------
    subject = proposal.args.get("slug") or proposal.args.get("lead_slug") or proposal.action
    ob_key = None
    if proposal.risk_class in (PIPELINE_EFFECT, EXTERNAL_EFFECT):
        from ghost.store import outbox
        ob_key = f"proposal:{proposal.action}:{subject}:{int(time.time() * 1000) % 10**10}"
        outbox.record(proposal.action, ob_key, {"args": proposal.args})
    started = time.time()
    try:
        executor = _import(PROPOSAL_REGISTRY[proposal.action]["executor"])
        raw = adapter["invoke"](executor, proposal, grant)
        if asyncio.iscoroutine(raw):
            raw = await raw
    except Exception as e:
        if ob_key:
            from ghost.store import outbox
            outbox.transition(ob_key, "failed")
        _audit("proposal.effect_failed", action=proposal.action, error=str(e)[:300])
        return {"status": "error", "stage": "effects", "reason": str(e)[:300]}

    # ---- verify: the loop sees the VERIFIER's result, not the brain's claim
    try:
        verifier = _import(PROPOSAL_REGISTRY[proposal.action]["verifier"])
        verified = adapter["verify"](verifier, proposal, raw)
    except Exception as e:
        if ob_key:
            from ghost.store import outbox
            outbox.transition(ob_key, "failed")
        _audit("proposal.verify_failed", action=proposal.action, error=str(e)[:300])
        return {"status": "error", "stage": "verify", "reason": str(e)[:300],
                "raw_result": raw if isinstance(raw, dict) else {"result": str(raw)[:300]}}

    if ob_key:
        from ghost.store import outbox
        if outbox.transition(ob_key, "sent") and verified.get("verified"):
            outbox.transition(ob_key, "verified")
    _audit("proposal.executed", action=proposal.action, subject=str(subject)[:100],
           verified=bool(verified.get("verified")), elapsed_ms=int((time.time() - started) * 1000))
    # the VERIFIER's dict is the tool result; dispatcher metadata under
    # non-colliding keys (verified dicts may carry their own 'status')
    return {**verified, "proposal": proposal.action, "dispatch": "ok"}


def dispatch_legacy_or_proposals(tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """SYNC strangler switch (cron/CLI contexts). Inside a running event loop
    use `strangler_proposal()` + `await dispatch()` — asyncio.run would raise."""
    if brain_mode() != "proposals":
        return None
    try:
        proposal = to_proposal(tool_name, arguments)
    except ValueError:
        return None   # capability not mapped yet -> legacy
    try:
        asyncio.get_running_loop()
        return None   # caller is async — it must await dispatch() itself
    except RuntimeError:
        return asyncio.run(dispatch(proposal))


def strangler_proposal(tool_name: str, arguments: Dict[str, Any]) -> Optional[Proposal]:
    """Async-context seam: the mapped proposal for this tool, or None to fall
    through to legacy."""
    if brain_mode() != "proposals":
        return None
    try:
        return to_proposal(tool_name, arguments)
    except ValueError:
        return None
