"""BRAIN PLANE — typed proposals (T6a): the LLM's only action vocabulary.

The brain never executes. It emits a Proposal; the control plane validates
risk, halt, ladder and grants; the effects plane executes; the verify plane
checks the outcome — and the VERIFIER's result is what the loop sees as its
tool result. This module is the schema + registry only (no behavior change
yet: the strangler wiring lands in T6b behind GHOST_BRAIN_MODE).

Risk classes and the per-action registry live in docs/PROPOSAL_TYPES.md and
are enforced here (one source of truth in code, the doc mirrors it).
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

READ = "READ"
LOCAL_MUTATION = "LOCAL_MUTATION"
SANDBOXED_SHELL = "SANDBOXED_SHELL"
PIPELINE_EFFECT = "PIPELINE_EFFECT"
EXTERNAL_EFFECT = "EXTERNAL_EFFECT"

RISK_CLASSES = (READ, LOCAL_MUTATION, SANDBOXED_SHELL, PIPELINE_EFFECT, EXTERNAL_EFFECT)

# Grant capability per effect class (control plane mints; effects verify).
RISK_GRANT_CAPABILITY = {
    PIPELINE_EFFECT: "pipeline",
    EXTERNAL_EFFECT: None,   # per-action capability: demo.deploy -> "deploy", message.* -> per channel
}


@dataclass
class Proposal:
    """One typed action the brain is REQUESTING. Never self-executing."""
    action: str
    args: Dict[str, Any] = field(default_factory=dict)
    risk_class: str = READ
    personalization_facts: List[str] = field(default_factory=list)
    # the decision_id / work item this proposal serves (traceability)
    serving_decision: Optional[str] = None

    def validated(self) -> "Proposal":
        """Fail-closed coercion: unknown action, missing risk class or a
        risk class that contradicts the registry is refused here — the loop
        gets a refusal dict, not an execution."""
        reg = PROPOSAL_REGISTRY.get(self.action)
        if reg is None:
            raise ValueError(f"unknown proposal action: {self.action!r}")
        if self.risk_class != reg["risk_class"]:
            raise ValueError(
                f"risk class mismatch for {self.action}: declared {self.risk_class}, "
                f"registry says {reg['risk_class']}")
        missing = [k for k in reg.get("required_args", []) if self.args.get(k) in (None, "")]
        if missing:
            raise ValueError(f"{self.action}: missing required args {missing}")
        if reg.get("requires_operator_flag") and not self.args.get("operator_approved"):
            raise ValueError(f"{self.action}: requires operator_approved=true (operator-only)")
        return self


# --------------------------------------------------------------- the registry
# executor/verifier are dotted paths (imported lazily by the T6b dispatcher —
# the brain plane must not import effects directly, so no imports here).

def _r(action: str, risk_class: str, executor: str, verifier: str,
       required_args: Optional[List[str]] = None, **extra) -> Dict[str, Any]:
    d = {"risk_class": risk_class, "executor": executor, "verifier": verifier,
         "required_args": required_args or []}
    d.update(extra)
    return d


PROPOSAL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ---- revenue cycle (convert first, T6b) --------------------------------
    "leads.scout": _r("leads.scout", PIPELINE_EFFECT,
                      "ghost.workers.lead_sources.run_all_sources",
                      "ghost.workers.lead_hunter.audit_candidates",
                      required_args=["city"]),
    "leads.qualify": _r("leads.qualify", PIPELINE_EFFECT,
                        "ghost.workers.lead_qualifier.qualify",
                        "ghost.workers.lead_qualifier.verify_scores",
                        required_args=["lead_slug"]),
    "leads.choose_flow": _r("leads.choose_flow", PIPELINE_EFFECT,
                            "ghost.workers.flow_router.route",
                            "ghost.workers.flow_router.verify_route",
                            required_args=["lead_slug", "flow"]),
    "demo.build": _r("demo.build", PIPELINE_EFFECT,
                     "ghost.workers.demo.build",
                     "ghost.core.three_qa.verify_passed",
                     required_args=["slug"],
                     grant_capability="pipeline",
                     grant_bounds={"cost_class": "demo_build"}),
    "demo.deploy": _r("demo.deploy", EXTERNAL_EFFECT,
                      "ghost.workers.deploy.VercelDeployer.deploy_demo",
                      "ghost.verify.reconciliation.reconcile_deploys",
                      required_args=["slug"],
                      grant_capability="deploy"),
    "pipeline.update_stage": _r("pipeline.update_stage", PIPELINE_EFFECT,
                                "ghost.crm.db.CRMDatabase.update_lead",
                                "ghost.crm.db.CRMDatabase.get_lead",
                                required_args=["lead_slug", "status"],
                                allowed_statuses=("unscouted", "scouted", "demo_built",
                                                  "pitch_ready", "pitch_sent", "negotiating",
                                                  "closed_won", "closed_lost")),
    "message.send_email": _r("message.send_email", EXTERNAL_EFFECT,
                             "ghost.autonomy.sender.ColdOutreachSender.send",
                             "ghost.verify.reconciliation.reconcile_sends",
                             required_args=["lead_slug", "to_email", "subject", "body"],
                             grant_capability="outbound_email"),
    # ---- intentionally NOT auto-executable ---------------------------------
    "message.send_whatsapp": _r("message.send_whatsapp", EXTERNAL_EFFECT,
                                "NOT_CONVERTED_drafts_only",
                                "ghost.crm.db.CRMDatabase.add_or_update_draft",
                                required_args=["lead_slug", "body"],
                                note="operator identity channel — drafts until T9 "
                                     "operator-approval packages exist"),
    # ---- sandboxed shell ----------------------------------------------------
    "system.run": _r("system.run", SANDBOXED_SHELL,
                     "ghost.core.sandbox.launch",
                     "ghost.core.sandbox.verify_result_shape",
                     required_args=["command"]),
}


def to_proposal(tool_name: str, arguments: Dict[str, Any],
                serving_decision: Optional[str] = None) -> Proposal:
    """Translate a legacy GHOST_TOOLS call into a typed proposal (the strangler
    adapter). Unknown legacy tools raise — the caller stays on the legacy path
    until this registry knows the capability."""
    mapping = {
        "scout_expensive_venues_without_website": ("leads.scout", {"city": arguments.get("city", "Bengaluru")}),
        "qualify_venue_lead": ("leads.qualify", {"lead_slug": arguments.get("name")}),
        "choose_flow": ("leads.choose_flow", {"lead_slug": arguments.get("lead_slug"),
                                              "flow": arguments.get("operator_choice")}),
        "build_luxury_demo": ("demo.build", {"slug": arguments.get("restaurant_name"),
                                             "archetype": arguments.get("archetype")}),
        "build_3d_site": ("demo.build", {"slug": arguments.get("slug"),
                                         "operator_approved": arguments.get("operator_approved")}),
        "build_motion_site": ("demo.build", {"slug": arguments.get("slug"),
                                             "operator_approved": arguments.get("operator_approved")}),
        "deploy_demo_vercel": ("demo.deploy", {"slug": arguments.get("slug")}),
        "crm_update_client": ("pipeline.update_stage", {"lead_slug": arguments.get("slug"),
                                                        "status": arguments.get("status")}),
        "whatsapp_action": ("message.send_whatsapp", {"lead_slug": arguments.get("target"),
                                                      "body": arguments.get("message", "")}),
    }
    if tool_name not in mapping:
        raise ValueError(f"no proposal mapping for legacy tool {tool_name!r}")
    action, args = mapping[tool_name]
    reg = PROPOSAL_REGISTRY[action]
    risk = reg["risk_class"]
    facts = arguments.get("personalization_facts") or []
    return Proposal(action=action, args=args, risk_class=risk,
                    personalization_facts=list(facts),
                    serving_decision=serving_decision)
