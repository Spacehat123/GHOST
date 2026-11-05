"""CONTROL PLANE — expected-rhythm markers (T4).

The supervisor must judge "an outreach batch should have run by 11:00"
WITHOUT reading the store (single-writer rule: it never opens ghost.db).
So the MAIN process leaves tiny marker files when expected work happens:

    data/rhythm/<name>.json  =  {"day": "YYYY-MM-DD", "at": <unix ts>, "tz": "IST"}

- mark(name): the main process, after completing expected work
- due(name, cfg): the supervisor, checking a deadline — True when today is a
  scheduled day, the IST deadline (+grace) has passed, and no marker exists
  for today.

Marker files are the contract between the two processes: no DB, no locks.
"""
import json
import os
import time
from typing import Any, Dict, Optional

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

DEFAULT_RHYTHM: Dict[str, Any] = {
    "events": {
        # the autonomy loop ran and decided (ANY intent — HOLD_FIRE all
        # morning is itself an alarm per §10: silence is not stillness)
        "scheduler_cycle": {"by": "11:00", "days": [0, 1, 2, 3, 4, 5], "grace_min": 45},
        # the daily digest was written
        "digest": {"by": "09:30", "days": [0, 1, 2, 3, 4, 5, 6], "grace_min": 30},
    },
}


def rhythm_dir() -> str:
    return os.environ.get("GHOST_RHYTHM_DIR") or os.path.join(REPO_DIR, "data", "rhythm")


def rhythm_config() -> Dict[str, Any]:
    """Defaults + operator knob file (configs/rhythm.json)."""
    cfg = json.loads(json.dumps(DEFAULT_RHYTHM))
    path = os.environ.get("GHOST_RHYTHM_CONFIG") or os.path.join(REPO_DIR, "configs", "rhythm.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                override = json.load(f)
            cfg["events"].update(override.get("events", {}))
        except Exception:
            pass  # unreadable knob -> defaults (fail toward watching)
    return cfg


def _ist_now(ts: Optional[float] = None) -> time.struct_time:
    return time.gmtime((ts or time.time()) + 5.5 * 3600)


def mark(name: str, at: Optional[float] = None) -> str:
    """Main process: expected work happened. Atomic-rename marker file."""
    ts = at or time.time()
    d = rhythm_dir()
    os.makedirs(d, exist_ok=True)
    rec = {"day": time.strftime("%Y-%m-%d", _ist_now(ts)), "at": ts, "tz": "IST"}
    tmp = os.path.join(d, f".{name}.tmp.{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f)
    os.replace(tmp, os.path.join(d, f"{name}.json"))
    return rec["day"]


def marked_day(name: str) -> Optional[str]:
    try:
        with open(os.path.join(rhythm_dir(), f"{name}.json"), "r", encoding="utf-8") as f:
            return json.load(f).get("day")
    except (OSError, ValueError):
        return None


def due(name: str, cfg: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Supervisor: is this expected event MISSING past its deadline?
    Returns {"due": bool, "reason": ...} — never raises."""
    cfg = cfg or rhythm_config()
    spec = (cfg.get("events") or {}).get(name)
    if not spec:
        return {"due": False, "reason": "not_configured"}
    now = now or time.time()
    ist = _ist_now(now)
    today = time.strftime("%Y-%m-%d", ist)
    weekday = ist.tm_wday          # Monday = 0 (matches time.gmtime)
    if spec.get("days") is not None and weekday not in set(spec["days"]):
        return {"due": False, "reason": "not_scheduled_today"}
    hh, mm = str(spec.get("by", "23:59")).split(":")
    deadline = int(ist.tm_year), int(ist.tm_mon), int(ist.tm_mday), int(hh), int(mm), 0, 0, 0, -1
    deadline_ts = time.mktime(deadline) - 5.5 * 3600   # IST -> UTC epoch
    grace = float(spec.get("grace_min", 30)) * 60
    if now < deadline_ts + grace:
        return {"due": False, "reason": "before_deadline"}
    if marked_day(name) == today:
        return {"due": False, "reason": "marked_today"}
    return {"due": True, "reason": f"no {name} marker for {today} (deadline {spec.get('by')} IST +{int(grace/60)}m grace)"}
