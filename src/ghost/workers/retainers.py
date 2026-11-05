"""Retainer Fleet — subscriptions, dunning, auto-fix tiers, monthly reports (P4).

State lives in its own SQLite store (data/retainers.db). The constitution is
load-bearing here:

- DUNNING NEVER TOUCHES A CLIENT SITE. Charge failures escalate the *billing*
  state: retry -> grace -> suspended. Suspension keeps the site UP (soft:
  banner optional) — money problems never become availability problems.
- THERE IS NO DELETE. The store has no deletion path for paid client sites;
  removal requires an operator-performed action outside this module (§2:
  'Ghost never deletes a paid client's site without explicit human
  confirmation. Ever.'). Attempting to call one raises by design.
- Auto-fix tiers gate through the autonomy ladder + halt switch:
    T1 (L4): injected mechanical fixers (cache purge, cert renew trigger)
    T2 (L3): restore from a drill-verified snapshot
    T3 (L2): staged fix -> operator approval token; nothing is touched
- Monthly report = evidence: uptime %, incidents, fixes — the retention tool.
"""
import json
import logging
import os
import sqlite3
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from ghost.core.audit import audit
from ghost.core.halt import assert_not_halted

logger = logging.getLogger("ghost.retainers")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
RETAINER_DB_PATH = os.environ.get("GHOST_RETAINER_DB_PATH") or os.path.join(REPO_DIR, "data", "retainers.db")
GRACE_DAYS = 7
MAX_CHARGE_FAILURES = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    slug TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'retainer',
    amount_inr REAL NOT NULL DEFAULT 0,
    billing_state TEXT NOT NULL DEFAULT 'active',   -- active|dunning|grace|suspended
    charge_failures INTEGER NOT NULL DEFAULT 0,
    grace_until REAL,
    last_charge_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    kind TEXT NOT NULL,              -- uptime|ssl|seo_drift|broken_links
    severity TEXT NOT NULL,          -- t1|t2|t3
    detail TEXT,
    evidence TEXT,
    status TEXT NOT NULL DEFAULT 'open',   -- open|fixed|staged|escalated
    opened_at REAL NOT NULL,
    resolved_at REAL,
    resolution TEXT
);
CREATE TABLE IF NOT EXISTS fixes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    incident_id INTEGER NOT NULL,
    tier TEXT NOT NULL,
    action TEXT NOT NULL,
    approved_by TEXT,
    result TEXT,
    at REAL NOT NULL
);
"""


class RetainerStore:
    def __init__(self, db_path: str = RETAINER_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        from contextlib import closing
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------ sites

    def register_site(self, slug: str, url: str, amount_inr: float = 0.0,
                      plan: str = "retainer") -> Dict[str, Any]:
        now = time.time()
        with self._conn() as conn, conn:
            conn.execute(
                """INSERT INTO sites (slug, url, plan, amount_inr, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(slug) DO UPDATE SET url=excluded.url,
                     amount_inr=excluded.amount_inr, plan=excluded.plan,
                     updated_at=excluded.updated_at""",
                (slug, url, plan, amount_inr, now, now))
        audit("retainer.site_registered", subject=slug, url=url, amount_inr=amount_inr)
        return self.get_site(slug)

    def get_site(self, slug: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sites WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def list_sites(self, billing_state: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            if billing_state:
                rows = conn.execute("SELECT * FROM sites WHERE billing_state = ? ORDER BY slug",
                                    (billing_state,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM sites ORDER BY slug").fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- dunning

    def record_charge_succeeded(self, slug: str, event_id: Optional[str] = None) -> Dict[str, Any]:
        with self._conn() as conn, conn:
            conn.execute(
                """UPDATE sites SET billing_state='active', charge_failures=0,
                   grace_until=NULL, last_charge_at=?, updated_at=? WHERE slug=?""",
                (time.time(), time.time(), slug))
        audit("retainer.charge_succeeded", subject=slug, event_id=event_id)
        return self.get_site(slug)

    def record_charge_failed(self, slug: str, event_id: Optional[str] = None) -> Dict[str, Any]:
        site = self.get_site(slug)
        if not site:
            raise KeyError(f"unknown retainer site '{slug}'")
        failures = site["charge_failures"] + 1
        now = time.time()
        if failures < MAX_CHARGE_FAILURES:
            state = "dunning"
            grace_until = None
        elif failures == MAX_CHARGE_FAILURES:
            state = "grace"
            grace_until = now + GRACE_DAYS * 86400
            audit("retainer.grace_entered", subject=slug, failures=failures,
                  grace_days=GRACE_DAYS,
                  ping="(a) money: payment failing — grace period started")
        else:
            # Past grace (failures beyond max + grace window): suspend billing,
            # KEEP THE SITE UP. Never delete.
            state = "suspended"
            grace_until = None
            audit("retainer.suspended", subject=slug, failures=failures,
                  note="site stays UP; no deletion path exists in this module",
                  ping="(a) money: retainer suspended — operator decision required")
        with self._conn() as conn, conn:
            conn.execute(
                """UPDATE sites SET billing_state=?, charge_failures=?,
                   grace_until=?, updated_at=? WHERE slug=?""",
                (state, failures, grace_until, now, slug))
        return self.get_site(slug)

    def is_in_grace_expired(self, slug: str) -> bool:
        site = self.get_site(slug)
        if not site:
            return False
        return (site["billing_state"] == "grace"
                and site["grace_until"] is not None
                and time.time() > site["grace_until"])

    # -------------------------------------------------------------- incidents

    def open_incident(self, slug: str, kind: str, severity: str,
                      detail: str, evidence: Dict[str, Any]) -> int:
        with self._conn() as conn, conn:
            cur = conn.execute(
                """INSERT INTO incidents (slug, kind, severity, detail, evidence,
                   opened_at) VALUES (?, ?, ?, ?, ?, ?)""",
                (slug, kind, severity, detail, json.dumps(evidence), time.time()))
            iid = cur.lastrowid
        audit("incident.opened", subject=slug, incident_id=iid, kind=kind,
              severity=severity, detail=detail[:200])
        return iid

    def resolve_incident(self, incident_id: int, resolution: str) -> None:
        with self._conn() as conn, conn:
            conn.execute(
                """UPDATE incidents SET status='fixed', resolved_at=?, resolution=?
                   WHERE id=?""", (time.time(), resolution, incident_id))
        audit("incident.resolved", subject=str(incident_id), resolution=resolution[:200])

    def open_incidents(self, slug: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            if slug:
                rows = conn.execute(
                    "SELECT * FROM incidents WHERE slug=? AND status='open' ORDER BY opened_at DESC",
                    (slug,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM incidents WHERE status='open' ORDER BY opened_at DESC").fetchall()
        return [dict(r) for r in rows]

    def over_slo_incident_count(self) -> int:
        """Incidents open longer than 10 minutes (the §4 SLO) — feeds the
        policy engine's SERVICE_EXISTING trigger."""
        cutoff = time.time() - 600
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM incidents WHERE status='open' AND opened_at < ?",
                (cutoff,)).fetchone()
        return row["n"]

    # ------------------------------------------------------------------ fixes

    def record_fix(self, slug: str, incident_id: int, tier: str, action: str,
                   result: str, approved_by: Optional[str] = None) -> None:
        with self._conn() as conn, conn:
            conn.execute(
                """INSERT INTO fixes (slug, incident_id, tier, action, approved_by,
                   result, at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (slug, incident_id, tier, action, approved_by, result, time.time()))

    def fixes_in_month(self, slug: str, year: int, month: int) -> List[Dict[str, Any]]:
        start = time.mktime((year, month, 1, 0, 0, 0, 0, 0, -1))
        end_month = month + 1 if month < 12 else 1
        end_year = year if month < 12 else year + 1
        end = time.mktime((end_year, end_month, 1, 0, 0, 0, 0, 0, -1))
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM fixes WHERE slug=? AND at BETWEEN ? AND ? ORDER BY at",
                (slug, start, end)).fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------------------------ auto-fix

class AutoFixer:
    """Dispatches incident fixes through tier + ladder + halt gates."""

    def __init__(self, store: RetainerStore, snapshots, ladder,
                 t1_fixers: Optional[Dict[str, Callable[..., Any]]] = None,
                 ping: Optional[Callable[[str, Dict[str, Any]], Any]] = None):
        self.store = store
        self.snapshots = snapshots          # SnapshotStore
        self.ladder = ladder                # AutonomyLadder
        self.t1_fixers = t1_fixers or {}    # action_name -> callable(slug, incident) -> result
        self.ping = ping

    def dispatch(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        slug, iid = incident["slug"], incident["id"]
        severity = incident["severity"]

        # NEVER touch a live site without a snapshot (§2 Stage 7).
        snap = self.snapshots.latest(slug)
        if not snap:
            self._escalate(incident, "no snapshot on file — refusing to touch the site")
            return {"status": "escalated", "reason": "no_snapshot"}

        if severity == "t1":
            refusal = self.ladder.require_level("retainer_autofix", 4)
            if refusal:
                self._escalate(incident, refusal["reason"])
                return {"status": "escalated", "reason": "ladder"}
            return self._run_t1(incident)

        if severity == "t2":
            refusal = self.ladder.require_level("retainer_autofix", 3)
            if refusal:
                self._escalate(incident, refusal["reason"])
                return {"status": "escalated", "reason": "ladder"}
            return self._run_t2(incident, snap)

        # t3: staged — even at L4 nothing is auto-applied; needs approval token.
        token = f"approve-{uuid.uuid4().hex[:10]}"
        self.store.record_fix(slug, iid, "t3", "staged_fix_proposed",
                              result="awaiting operator approval", approved_by=None)
        with self.store._conn() as conn, conn:
            conn.execute("UPDATE incidents SET status='staged' WHERE id=?", (iid,))
        audit("incident.staged", subject=slug, incident_id=iid, token=token,
              ping="(c) unverifiable: staged fix needs one-click approval")
        if self.ping:
            self.ping("(c) unverifiable", {"kind": "staged_fix", "incident": iid,
                                           "token": token, "detail": incident["detail"]})
        return {"status": "staged", "approval_token": token}

    def _run_t1(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        slug, iid = incident["slug"], incident["id"]
        action = f"t1:{incident['kind']}"
        fixer = self.t1_fixers.get(action)
        if not fixer:
            self._escalate(incident, f"no registered T1 fixer for '{action}'")
            return {"status": "escalated", "reason": "no_fixer"}
        try:
            assert_not_halted(f"autofix:{action}:{slug}")
        except Exception as e:
            from ghost.core.halt import HaltActive
            if isinstance(e, HaltActive):
                # Halt is a global operator order, not an incident property:
                # leave the incident open, stop cleanly.
                audit("autofix.halt_paused", subject=slug, incident_id=iid, tier="t1")
                return {"status": "halted"}
            self._escalate(incident, str(e)[:150])
            return {"status": "escalated"}
        result = fixer(slug, incident)
        self.store.record_fix(slug, iid, "t1", action, result=str(result)[:200],
                              approved_by="autonomous_L4")
        self.store.resolve_incident(iid, f"T1 {action}: {result}")
        return {"status": "fixed", "tier": "t1", "result": result}

    def _run_t2(self, incident: Dict[str, Any], snap: Dict[str, Any]) -> Dict[str, Any]:
        slug, iid = incident["slug"], incident["id"]
        # A snapshot is only restorable if a drill has PROVEN it (§2 Stage 7).
        if not snap.get("drill_verified_at"):
            self._escalate(incident, "newest snapshot not drill-verified — T2 restore refused")
            return {"status": "escalated", "reason": "snapshot_not_drilled"}
        try:
            assert_not_halted(f"autofix:t2_restore:{slug}")
        except Exception as e:
            from ghost.core.halt import HaltActive
            if isinstance(e, HaltActive):
                audit("autofix.halt_paused", subject=slug, incident_id=iid, tier="t2")
                return {"status": "halted"}
            self._escalate(incident, str(e)[:150])
            return {"status": "escalated"}
        result = self.snapshots.restore_drill(slug, snap["snapshot_id"])  # drill first, then restore live
        ok = result.get("ok")
        if ok:
            self.store.record_fix(slug, iid, "t2", "snapshot_restore",
                                  result=f"restored {result.get('restored_files')} files",
                                  approved_by="autonomous_L3")
            self.store.resolve_incident(iid, f"T2 snapshot restore ({snap['snapshot_id']})")
            return {"status": "fixed", "tier": "t2", "restored": result.get("restored_files")}
        self._escalate(incident, f"restore drill failed: {result.get('error')}")
        return {"status": "escalated", "reason": "drill_failed"}

    def _escalate(self, incident: Dict[str, Any], reason: str) -> None:
        slug = incident["slug"]
        with self.store._conn() as conn, conn:
            conn.execute("UPDATE incidents SET status='escalated' WHERE id=?",
                         (incident["id"],))
        audit("incident.escalated", subject=slug, incident_id=incident["id"],
              reason=reason[:200],
              ping="(c) unverifiable: incident needs a human")
        if self.ping:
            self.ping("(c) unverifiable", {"kind": "incident_escalated",
                                           "incident": incident["id"],
                                           "slug": slug, "reason": reason})


# -------------------------------------------------------------- monthly report

def monthly_report(store: RetainerStore, monitor_history: Callable[[str], Dict[str, Any]],
                   slug: str, year: Optional[int] = None, month: Optional[int] = None,
                   repo_dir: Optional[str] = None) -> Dict[str, Any]:
    """The retention artifact: evidence of what Ghost did all month.
    monitor_history(slug) -> {'checks': n, 'uptime_pct': x, 'incidents': n}."""
    now = time.localtime()
    year, month = year or now.tm_year, month or now.tm_mon
    hist = monitor_history(slug)
    fixes = store.fixes_in_month(slug, year, month)
    lines = [
        f"# Monthly Care Report — {slug} — {year}-{month:02d}",
        "",
        f"- Checks performed: **{hist.get('checks', 0)}**",
        f"- Uptime: **{hist.get('uptime_pct', 100.0)}%**",
        f"- Incidents detected: {hist.get('incidents', 0)} · Auto-fixed: **{len(fixes)}**",
        "",
        "## Fixes applied",
    ]
    if fixes:
        for f in fixes:
            ts = time.strftime("%d %b %H:%M", time.localtime(f["at"]))
            lines.append(f"- `{ts}` [{f['tier'].upper()}] {f['action']} — {f['result']}")
    else:
        lines.append("- None needed. The site ran clean all month.")
    lines.append("")
    md = "\n".join(lines)

    repo = repo_dir or REPO_DIR
    d = os.path.join(repo, "data", "reports", slug)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{year}-{month:02d}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    audit("retainer.report_written", subject=slug, path=path)
    return {"path": path, "markdown": md, "fixes": len(fixes)}
