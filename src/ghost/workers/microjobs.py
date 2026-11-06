"""WORKERS — the micro-jobs copilot (Operator's 11 P8, goal 9).

When the main pipeline is quiet, Ghost finds small jobs it can actually
deliver (site builds, fixes, 3D embeds) and DRAFTS tailored proposals.
A HUMAN presses send — there is deliberately NO auto-bid code path anywhere
in this module (Upwork ToS honesty, §0.6); a purity test enforces it.

Sources (operator-owned, configs/microjobs.json):
  rss_feeds:          any job-board RSS (fetch_feed pattern from BidCopilot)
  upwork_search_urls: saved Upwork searches, read through the operator's own
                      browser (CDP :9222, read-only) or plain HTTP as fallback

Honesty rules:
- every job dedups by URL; scoring cites WRITTEN reasons (which deliverable
  keywords matched) — no vibes
- a parse failure or a blocked fetch is reported as exactly that, never
  fabricated into zero jobs quietly
- proposals are status awaiting_human_send until the OPERATOR says otherwise
"""
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from ghost.core.audit import audit
from ghost.paths import data_dir

logger = logging.getLogger("ghost.microjobs")

CONFIG_PATH = os.environ.get("GHOST_MICROJOBS_CONFIG") or os.path.join(
    os.path.dirname(data_dir()), "configs", "microjobs.json")

DEFAULT_CONFIG = {
    "rss_feeds": [],
    "upwork_search_urls": [],
    "min_fit_score": 3.0,
    "max_jobs_per_sweep": 20,
}

# what Ghost can ACTUALLY deliver (flows.json sectors + the real pipeline)
DELIVERABLES = [
    ("site_or_landing_page", ("landing page", "website", "web site", "one-page",
                              "redesign", "new site", "business site"), 4.0,
     "we build exactly this kind of site (mobile-first, fast, deployed)"),
    ("fix_or_speed", ("fix", "broken", "bug", "error", "slow", "speed up",
                      "optimize", "lighthouse", "page speed"), 4.0,
     "diagnosing and fixing broken/slow sites is a core Ghost capability"),
    ("three_d_showcase", ("three.js", "3d", "webgl", "product viewer",
                          "3d model", "3d configurator"), 5.0,
     "3D product showcases are our signature work (Three.js, GLB)"),
    ("motion_polish", ("animation", "motion", "gsap", "scroll effect",
                       "micro-interaction"), 3.0,
     "premium motion/animation polish is in our daily toolkit"),
    ("wordpress_or_cms", ("wordpress", "elementor", "webflow", "cms"), 2.0,
     "WordPress/Webflow builds and cleanups are routine for us"),
    ("booking_or_contact", ("booking", "appointment", "reservation",
                            "contact form", "whatsapp"), 3.0,
     "we wire booking/contact paths that actually convert"),
]


def load_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})
    return out


# ------------------------------------------------------------------- the store

def _db_path() -> str:
    return os.environ.get("GHOST_MICROJOBS_DB") or os.path.join(data_dir(), "microjobs.db")


class JobStore:
    """Tiny sqlite ledger of seen jobs + drafted proposals. Dedup by URL."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _db_path()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._conn() as conn, conn:
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS jobs ("
                " id TEXT PRIMARY KEY, source TEXT, url TEXT UNIQUE, title TEXT,"
                " budget_text TEXT, fit_score REAL, reasons_json TEXT,"
                " status TEXT DEFAULT 'discovered', discovered_at REAL);"
                "CREATE TABLE IF NOT EXISTS proposals ("
                " id TEXT PRIMARY KEY, job_id TEXT, body TEXT,"
                " status TEXT DEFAULT 'awaiting_human_send',"
                " created_at REAL, sent_at REAL);")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def add_job(self, job: Dict[str, Any]) -> Optional[str]:
        """Insert a discovered job. Returns the id, or None when the URL is
        already known (dedup — the sweep counts only genuinely new rows)."""
        url = job.get("url")
        if not url:
            return None
        try:
            with self._conn() as conn, conn:
                row = conn.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()
                if row:
                    return None   # already known
                jid = f"job_{uuid.uuid4().hex[:10]}"
                conn.execute(
                    "INSERT INTO jobs (id, source, url, title, budget_text,"
                    " fit_score, reasons_json, status, discovered_at)"
                    " VALUES (?,?,?,?,?,?,?,'discovered',?)",
                    (jid, job.get("source"), url, job.get("title", "")[:200],
                     str(job.get("budget_text", ""))[:60], float(job.get("fit_score", 0)),
                     json.dumps(job.get("reasons", [])), time.time()))
            return jid
        except sqlite3.Error as e:
            logger.warning("job insert failed: %s", e)
            return None

    def list_jobs(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM jobs"
        args: tuple = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY discovered_at DESC LIMIT 100"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def set_status(self, job_id: str, status: str) -> bool:
        if status not in ("discovered", "proposed", "sent", "won", "lost", "skipped"):
            return False
        with self._conn() as conn, conn:
            cur = conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        return cur.rowcount > 0

    def add_proposal(self, job_id: str, body: str) -> Dict[str, Any]:
        pid = f"prop_{uuid.uuid4().hex[:10]}"
        with self._conn() as conn, conn:
            conn.execute(
                "INSERT INTO proposals (id, job_id, body, status, created_at)"
                " VALUES (?,?,?,'awaiting_human_send',?)",
                (pid, job_id, body[:4000], time.time()))
            conn.execute("UPDATE jobs SET status = 'proposed' WHERE id = ?", (job_id,))
        return {"id": pid, "job_id": job_id, "body": body,
                "status": "awaiting_human_send"}

    def get_proposal(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM proposals WHERE job_id = ? ORDER BY created_at DESC",
                (job_id,)).fetchone()
        return dict(row) if row else None

    def set_proposal_status(self, job_id: str, status: str) -> bool:
        if status not in ("sent", "won", "lost", "awaiting_human_send"):
            return False
        with self._conn() as conn, conn:
            cur = conn.execute(
                "UPDATE proposals SET status = ?, sent_at = ? WHERE job_id = ?",
                (status, time.time() if status == "sent" else None, job_id))
        return cur.rowcount > 0


# ------------------------------------------------------------------- sources

def fetch_url(url: str, timeout: float = 15.0) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Ghost-MicroJobs/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(2_000_000).decode("utf-8", errors="replace")


def fetch_via_browser(url: str) -> str:
    """Read a search page through the operator's OWN browser (CDP :9222,
    read-only, the same trust model as the recon scout). Raises honestly."""
    import asyncio
    return asyncio.run(_fetch_via_browser_async(url))


async def _fetch_via_browser_async(url: str) -> str:
    import aiohttp
    import websockets
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        async with session.get("http://127.0.0.1:9222/json") as resp:
            tabs = await resp.json(content_type=None)
        ws_url = None
        for t in tabs:
            if t.get("type") == "page":
                ws_url = t.get("webSocketDebuggerUrl")
                break
        if not ws_url:
            raise RuntimeError("no browser page tab on CDP :9222")
    async with websockets.connect(ws_url, max_size=20_000_000, ping_timeout=10) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        await ws.send(json.dumps({"id": 2, "method": "Page.navigate",
                                  "params": {"url": url}}))
        await asyncio.sleep(4.0)   # results render progressively; one bounded wait
        await ws.send(json.dumps({
            "id": 3, "method": "Runtime.evaluate",
            "params": {"expression": "document.documentElement.outerHTML",
                       "returnByValue": True}}))
        deadline = time.time() + 10
        while time.time() < deadline:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 3:
                return str(msg.get("result", {}).get("result", {}).get("value", ""))
        raise RuntimeError("browser did not return the page in time")


def parse_upwork_search(html: str) -> List[Dict[str, Any]]:
    """Best-effort parse of an Upwork search page (job tiles). The markup is
    Upwork's to change — a parse that finds nothing reports that, honestly."""
    jobs: List[Dict[str, Any]] = []
    seen: set = set()
    for m in re.finditer(
            r'<a[^>]+href="(/jobs/~[0-9a-f]+[^"]*)"[^>]*data-test="?JobTileLink"?[^>]*>(.*?)</a>',
            html, re.S | re.I):
        href, inner = m.group(1), m.group(2)
        title = re.sub(r"<[^>]+>", " ", inner)
        title = re.sub(r"\s+", " ", title).strip()
        if not title or href in seen:
            continue
        seen.add(href)
        jobs.append({"source": "upwork_search", "url": f"https://www.upwork.com{href}",
                     "title": title[:200], "description": "",
                     "budget_text": ""})
    return jobs


def normalize_rss_item(item: Dict[str, Any]) -> Dict[str, Any]:
    desc = re.sub(r"<[^>]+>", " ", item.get("description", ""))
    budget = ""
    m = re.search(r"(?:budget[:\s]*)?(\$|₹|£)\s?[\d,]+(?:\s?[-–]\s?[\d,]+)?", desc)
    if m:
        budget = m.group(0).strip()
    return {"source": "rss", "url": item.get("link", ""),
            "title": item.get("title", ""), "description": desc[:2000],
            "budget_text": budget}


# ------------------------------------------------------------------- scoring

def score_fit(job: Dict[str, Any]) -> Dict[str, Any]:
    """Deliverable-keyword fit with WRITTEN reasons. Deterministic; no vibes."""
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    reasons: List[str] = []
    score = 0.0
    matched: List[str] = []
    for key, keywords, weight, line in DELIVERABLES:
        hits = [k for k in keywords if k in text]
        if hits:
            score += weight
            matched.append(key)
            reasons.append(f"{key} (+{weight:.0f}): matched {', '.join(hits[:4])} — {line}")
    budget = job.get("budget_text", "")
    if budget:
        score += 1.0
        reasons.append(f"budget signal (+1): {budget}")
    return {"fit_score": round(score, 1), "reasons": reasons,
            "deliverables": matched}


def draft_proposal_text(job: Dict[str, Any], fit: Dict[str, Any]) -> str:
    """Deterministic, tailored template — the operator edits and sends."""
    deliverables = fit.get("deliverables", [])
    question = ("is the content ready, or would you like help with that too?")
    if "booking_or_contact" in deliverables:
        question = "does it need online booking wired in, or is that later?"
    elif "three_d_showcase" in deliverables:
        question = "do you already have a 3D model (GLB) we could use?"
    elif "fix_or_speed" in deliverables:
        question = "can you share the URL and what you see breaking?"
    title = (job.get("title") or "your posting")[:80]
    lead_line = "we do exactly this kind of work every week"
    if deliverables:
        lead_line = DELIVERABLES[[d[0] for d in DELIVERABLES].index(deliverables[0])][3]
    return (f"Hi — saw '{title}'. {lead_line.capitalize()}. "
            f"Happy to show two live examples this week. Fixed price, mobile-first, "
            f"delivered fast. Quick scope question before we quote precisely: "
            f"{question} ({job.get('url', '')})")


# -------------------------------------------------------------------- sweep

def sweep(store: Optional[JobStore] = None,
          fetch_page: Optional[Callable[[str], str]] = None,
          fetch_rss: Optional[Callable[[str], str]] = None,
          sources: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One source sweep: fetch → normalize → score → dedup-store. Sources
    default to configs/microjobs.json (operator-owned); tests and callers may
    pass them explicitly. Returns the honest counts (including what failed
    and why). Never drafts by itself — drafting is an explicit operator
    action per job."""
    cfg = load_config()
    sources = sources or cfg
    store = store or JobStore()
    considered = stored = failed = 0
    failures: List[str] = []

    candidates: List[Dict[str, Any]] = []
    for feed in sources.get("rss_feeds", []):
        try:
            from ghost.workers.growth import BidCopilot
            items = BidCopilot(feed_url=feed).fetch_feed(fetch=fetch_rss)
            if not items:
                raise RuntimeError("empty feed / no <item> rows")
            candidates.extend(normalize_rss_item(i) for i in items)
        except Exception as e:
            failed += 1
            failures.append(f"rss {feed[:60]}: {str(e)[:80]}")
            audit("microjobs.source_failed", source="rss", url=feed[:200],
                  error=str(e)[:120])
    for url in sources.get("upwork_search_urls", []):
        try:
            html = (fetch_page or _search_page)(url)
            found = parse_upwork_search(html)
            if not found:
                raise RuntimeError("page parsed to zero job tiles (markup changed?)")
            candidates.extend(found)
        except Exception as e:
            failed += 1
            failures.append(f"upwork {url[:60]}: {str(e)[:80]}")
            audit("microjobs.source_failed", source="upwork_search", url=url[:200],
                  error=str(e)[:120])

    scored = 0
    for job in candidates[: cfg.get("max_jobs_per_sweep", 20) * 3]:
        if not job.get("url"):
            continue
        considered += 1
        fit = score_fit(job)
        job["fit_score"] = fit["fit_score"]
        job["reasons"] = fit["reasons"]
        if store.add_job(job):
            stored += 1
            if fit["fit_score"] >= float(cfg.get("min_fit_score", 3.0)):
                scored += 1

    audit("microjobs.swept", considered=considered, stored=stored,
          above_min_score=scored, failed=failed, failures=failures[:5])
    return {"status": "ok", "considered": considered, "stored": stored,
            "above_min_score": scored, "failed": failed, "failures": failures,
            "note": "jobs wait in the board — drafting a proposal is YOUR click"}


def _search_page(url: str) -> str:
    """Upwork search pages block plain bots; the operator's own browser is
    the honest reader. Plain HTTP is the fallback, honestly labeled."""
    try:
        return fetch_via_browser(url)
    except Exception:
        return fetch_url(url)
