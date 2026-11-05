"""Daily Digest — the 09:00 harvest (Roadmap §10).

One screen answering: what did Ghost do, what did it earn, what needs me.
Built from the audit ledger (evidence) + queue state + policy decision log.
Rendered as markdown; delivery (notify-send / email / GUI) is injected so
the content is testable pure.
"""
import time
from typing import Any, Dict, List, Optional

from ghost.core.audit import AuditLedger


def build_digest(day: Optional[str] = None, ledger: Optional[AuditLedger] = None,
                 queue=None, policy_events_limit: int = 20) -> Dict[str, Any]:
    """Assemble digest data for a day. Returns structured content + markdown."""
    ledger = ledger or AuditLedger.get_instance()
    day = day or time.strftime("%Y-%m-%d")
    summary = ledger.day_summary(day)
    events = summary.get("events", {})

    def n(kind: str) -> int:
        return events.get(kind, 0)

    # The headline numbers (§12 scoreboard subset)
    metrics = {
        "leads_contacted": n("outbound.sent"),
        "outbound_blocked": n("outbound.blocked"),
        "demos_deployed": n("demo.deployed") + n("job.done") * 0,  # job granularity below
        "qa_passes": n("qa.passed"),
        "payments_verified": n("payment.verified"),
        "replies_ingested": n("reply.ingested"),
        "complaints": n("reply.complaint"),
        "incidents": n("ladder.incident"),
        "jobs_dead": n("job.dead"),
    }

    # Pings that fired today — the "why did Ghost bother me" list
    pings: List[Dict[str, str]] = []
    for row in ledger.query(day=day, limit=200):
        et = row.get("event_type", "")
        payload = row.get("payload") or {}
        ping = payload.get("ping")
        if ping:
            pings.append({"time": time.strftime("%H:%M", time.localtime(row["ts"])),
                          "category": ping, "event": et,
                          "subject": row.get("subject") or ""})

    # Decisions made by the policy engine today (replayable trail)
    decisions = []
    for row in ledger.query(day=day, event_type="policy.decision",
                            limit=policy_events_limit):
        p = row.get("payload") or {}
        decisions.append({"time": time.strftime("%H:%M", time.localtime(row["ts"])),
                          "intent": p.get("intent"),
                          "reasons": (p.get("reasons") or [])[:2]})

    queue_block = queue.stats() if queue else {}

    md_lines = [
        f"# Ghost Daily Digest — {day}",
        "",
        "## Harvest",
        f"- Leads contacted: **{metrics['leads_contacted']}** (blocked by gates: {metrics['outbound_blocked']})",
        f"- QA passes: {metrics['qa_passes']} · Payments verified: {metrics['payments_verified']}",
        f"- Replies: {metrics['replies_ingested']} · Complaints: **{metrics['complaints']}**",
        f"- Incidents: {metrics['incidents']} · Dead jobs: {metrics['jobs_dead']}",
        "",
    ]
    if pings:
        md_lines.append("## Pings (why Ghost bothered you)")
        for p in pings:
            md_lines.append(f"- `{p['time']}` {p['category']} — {p['event']} ({p['subject']})")
        md_lines.append("")
    else:
        md_lines += ["## Pings", "None. It ran itself.", ""]

    if decisions:
        md_lines.append("## Decision trail (latest first)")
        for d in decisions[:10]:
            md_lines.append(f"- `{d['time']}` {d['intent']}: {'; '.join(d['reasons'])}")
        md_lines.append("")

    if queue_block:
        md_lines.append("## Queue")
        md_lines.append("- " + ", ".join(f"{k}: {v}" for k, v in sorted(queue_block.items())))
        md_lines.append("")

    return {"day": day, "metrics": metrics, "pings": pings,
            "decisions": decisions, "queue": queue_block,
            "markdown": "\n".join(md_lines)}


def deliver(markdown: str, deliver_fn=None) -> None:
    """Hand the digest to the operator. Default: desktop notification pointing
    to the file; deliver_fn overrides (email, GUI, etc.)."""
    if deliver_fn:
        deliver_fn(markdown)
        return
    try:
        import subprocess
        subprocess.run(
            ["notify-send", "-a", "Ghost", "Ghost Daily Digest",
             markdown.splitlines()[0] + " — see data/digests/"],
            timeout=5)
    except Exception:
        pass


def write_digest_file(content: Dict[str, Any], repo_dir: Optional[str] = None) -> str:
    import os
    repo = repo_dir or os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    d = os.path.join(repo, "data", "digests")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{content['day']}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content["markdown"])
    return path
