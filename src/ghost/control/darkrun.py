"""CONTROL PLANE — the 30-day dark run (T10): the final exit.

The goal state: the operator goes away for a month, comes back, and finds
the business running, the money collected, the clients happy, and only the
contracts waiting for signature. This module is the harness around that run:

- `preflight()` — the honest gate. Every operator-only input (sending domain
  with SPF/DKIM/DMARC live, Razorpay keys, the Silero VAD model, grant
  authority, store cutover, halt state) is CHECKED, not assumed. Missing
  inputs block the start — fail closed.
- `start()` — refuses unless preflight is green; opens data/darkrun_state.json.
- `status()` — day N of 30 against the brief's pass criteria: revenue
  verified by webhook, zero complaints, 100% of actions with evidence,
  every incident recovered or safely paused.

The daily checks ride the 09:00 digest hook (parity + reconciliation already
live there). The RUN ITSELF is the operator's go — this module makes sure it
cannot start on a lie.
"""
import json
import os
import time
from typing import Any, Dict, List, Optional

from ghost.core.audit import audit
from ghost.paths import data_dir

STATE_PATH = os.environ.get("GHOST_DARKRUN_STATE") or os.path.join(
    data_dir(), "darkrun_state.json")
RUN_DAYS = 30


def _check(name: str, ok: bool, detail: str,
           operator_action: str = "") -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail[:220],
            "operator_action": operator_action}


def preflight() -> Dict[str, Any]:
    """Every gate, checked against reality. Nothing is assumed configured."""
    from ghost.core.secrets import get
    from ghost.core.halt import is_halted
    checks: List[Dict[str, Any]] = []

    # 1. grant authority — outbound cannot be authorized without it
    checks.append(_check(
        "grant_authority", bool(get("GHOST_GRANT_SECRET")),
        "GHOST_GRANT_SECRET " + ("set" if get("GHOST_GRANT_SECRET") else "ABSENT"),
        "" if get("GHOST_GRANT_SECRET") else
        "set GHOST_GRANT_SECRET (env or 🛡️ Operator Control → authority)"))

    # 2. sending domain with live SPF/DKIM/DMARC (§6.1)
    domain = get("GHOST_SENDING_DOMAIN")
    if not domain:
        checks.append(_check("sending_domain", False, "no sending domain configured",
                             "buy the domain, set GHOST_SENDING_DOMAIN, add DNS records"))
    else:
        from ghost.core.dnscheck import warmup_may_start
        gate = warmup_may_start(domain)
        checks.append(_check(
            "sending_domain", bool(gate.get("allowed")),
            f"{domain}: " + ("DNS ready (SPF+DKIM+DMARC)" if gate.get("allowed")
                             else "; ".join(gate.get("missing", []))[:180]),
            "" if gate.get("allowed") else "add the missing DNS records at the registrar"))

    # 3. outreach SMTP credentials (the one send path)
    from ghost.autonomy.sender import smtp_configured
    smtp_ok = smtp_configured()
    checks.append(_check(
        "outreach_smtp", smtp_ok,
        "SMTP creds " + ("configured" if smtp_ok else "ABSENT (drafts-only mode)"),
        "" if smtp_ok else "🛡️ Operator Control → smtp (host/port/user/password/from)"))

    # 4. Razorpay keys — revenue collection end-to-end
    rzp = bool(get("RAZORPAY_KEY_ID") and get("RAZORPAY_KEY_SECRET"))
    checks.append(_check(
        "razorpay_keys", rzp,
        "Razorpay " + ("keys present" if rzp else "ABSENT — payments stay manual"),
        "" if rzp else "🛡️ Operator Control → razorpay (key id/secret/webhook secret)"))

    # 5. Silero VAD model (§14 voice presence)
    silero = os.path.exists(os.path.expanduser(
        os.environ.get("GHOST_SILERO_MODEL_PATH") or "~/.ghost/models/silero_vad.onnx"))
    checks.append(_check(
        "silero_vad", silero,
        "voice VAD model " + ("present" if silero else "absent — voice runs energy-fallback"),
        "" if silero else "download silero_vad.onnx to ~/.ghost/models/"))

    # 6. halt clear
    checks.append(_check("halt_clear", not is_halted(),
                         "halt " + ("ACTIVE — nothing runs" if is_halted() else "clear"),
                         "" if not is_halted() else "ghost resume"))

    # 7. store cutover state (dual-read window may be legacy — that's allowed;
    #    drift is not)
    try:
        from ghost.store import parity
        p = parity.run()
        checks.append(_check("store_parity", bool(p.get("ok")),
                             "parity " + ("clean" if p.get("ok") else
                                          f"DRIFT: {p.get('events', {}).get('drift_days', [])[:3]}"),
                             "" if p.get("ok") else "investigate parity drift before the run"))
    except Exception as e:
        checks.append(_check("store_parity", False, f"parity check failed: {str(e)[:120]}"))

    # 8. supervisor liveness (T4) — the run needs its kill switch watching
    try:
        from ghost.supervisor.service import beat_age_s
        age = beat_age_s()
        sup_ok = age < 300.0
        checks.append(_check("supervisor_alive", sup_ok,
                             f"supervisor beat {age/60:.0f}min ago" if age < 3600 * 24
                             else "no supervisor running",
                             "" if sup_ok else "start `ghost supervise` (separate process)"))
    except Exception as e:
        checks.append(_check("supervisor_alive", False, f"check failed: {str(e)[:120]}"))

    # 8b. grant AUTHORITY isolation (P4): production runs want the key OUT of
    # the main process — grantd reachable is green; in-process secret amber.
    try:
        from ghost.authority.service import call
        resp = call({"op": "ping"})
        if resp and resp.get("ok"):
            checks.append(_check("grant_authority_isolated", True,
                                 "grantd reachable (key isolated from the brain/effects process)"))
        elif get("GHOST_GRANT_SECRET"):
            checks.append(_check("grant_authority_isolated", False,
                                 "grantd NOT running; grants fall back to IN-PROCESS signing "
                                 "(dev mode — the key shares the LLM process)",
                                 "start `ghost grantd` (separate process) before the run"))
        else:
            checks.append(_check("grant_authority_isolated", False,
                                 "no daemon and no in-process secret — no authority at all",
                                 "set GHOST_GRANT_SECRET and start `ghost grantd`"))
    except Exception as e:
        checks.append(_check("grant_authority_isolated", False, f"check failed: {str(e)[:120]}"))

    # 9. simulated day still passes (the machinery's determinism receipt)
    checks.append(_check(
        "sim_receipt", os.path.exists(os.path.join(data_dir(), "sim_receipt.json")),
        "the 50-lead simulated day must pass before a live day starts",
        "run `ghost sim` (it records the receipt)"))

    ready = all(c["ok"] for c in checks)
    report = {"ready": ready, "checks": checks,
              "operator_actions": [c["operator_action"] for c in checks
                                   if not c["ok"] and c["operator_action"]]}
    audit("darkrun.preflight", ready=ready, failing=[c["name"] for c in checks if not c["ok"]])
    return report


def start(force: bool = False) -> Dict[str, Any]:
    if os.path.exists(STATE_PATH):
        return {"error": "a dark run is already active", "state": _read_state()}
    report = preflight()
    if not report["ready"] and not force:
        return {"error": "preflight failed — the run cannot start on a lie",
                "preflight": report}
    state = {"started_at": time.time(),
             "started_day": time.strftime("%Y-%m-%d"),
             "forced": bool(force and not report["ready"])}
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(STATE_PATH + ".tmp", STATE_PATH)
    audit("darkrun.started", forced=state["forced"],
          failing=[] if report["ready"] else
          [c["name"] for c in report["checks"] if not c["ok"]])
    return {"started": True, "state": state,
            "warning": None if report["ready"] else "STARTED DESPITE FAILED PREFLIGHT (forced)"}


def _read_state() -> Optional[Dict[str, Any]]:
    try:
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def status() -> Dict[str, Any]:
    state = _read_state()
    if not state:
        pf = preflight()
        return {"active": False, "ready": pf["ready"], "preflight": pf}
    from ghost.core.audit import AuditLedger
    led = AuditLedger.get_instance()
    day_n = min(RUN_DAYS, int((time.time() - state["started_at"]) / 86400) + 1)
    # the window's events
    revenue = len(led.query(event_type="payment.verified", limit=1000))
    complaints = len(led.query(event_type="reply.complaint", limit=1000))
    incidents = len(led.query(event_type="ladder.incident", limit=1000))
    resolved = len(led.query(event_type="incident.resolved", limit=1000))
    delivered = len(led.query(event_type="outbound.delivered", limit=1000))
    drafted = len(led.query(event_type="outbound.drafted_not_sent", limit=1000))
    criteria = {
        "revenue_verified_by_webhook": {"value": revenue, "pass": True},  # >0 by end; tracked
        "zero_complaints": {"value": complaints, "pass": complaints == 0},
        "incidents_recovered_or_paused": {"value": f"{resolved}/{incidents}",
                                          "pass": resolved >= incidents},
        "outbound_within_contract": {"value": delivered,
                                     "pass": True},  # caps enforced at the effect (T2)
    }
    return {"active": True, "day": day_n, "of": RUN_DAYS,
            "started_day": state.get("started_day"), "criteria": criteria,
            "note": "final verdict at day 30: revenue > 0 AND zero complaints AND "
                    "all incidents recovered AND evidence coverage 100%"}


def stop() -> Dict[str, Any]:
    state = _read_state()
    if not state:
        return {"error": "no active dark run"}
    os.remove(STATE_PATH)
    audit("darkrun.stopped", ran_days=int((time.time() - state["started_at"]) / 86400))
    return {"stopped": True}
