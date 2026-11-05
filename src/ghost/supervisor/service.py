"""SUPERVISOR PLANE — a SEPARATE PROCESS that can kill-switch a dead main (T4).

The single-writer rule (§2) says only the main process writes ghost.db. So
this process is DELIBERATELY self-contained: it imports NOTHING that touches
the store — only stdlib and the pure revocation-epoch module. It communicates
through files alone:

  watches:
    - the heartbeat file's age (a sick agent must not talk to customers)
    - expected-rhythm markers (core/rhythm.py) — "an outreach batch should
      have run by 11:00" — silence is an alarm
  acts (file writes only):
    - freeze: writes the HALT sentinel + meta ITSELF (same protocol as
      core/halt.py, minus the ledger write it must not make) and BUMPS the
      revocation epoch — killing every outstanding grant even though the
      main process is dead
    - alert: data/supervisor_alerts.log + notify-send (best effort)
    - its own liveness: data/supervisor_beat.json every cycle, so an external
      cron (`ghost deadman`) can alarm if THIS process dies

Run: `ghost supervise` (foreground loop) or --check-once for tests/cron.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional

# The ONLY ghost import allowed here: pure stdlib, no store, no audit.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from ghost.core.revocation import bump_epoch, current_epoch  # noqa: E402

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

HEARTBEAT_STALE_S = 180.0      # mirror of core.heartbeat.STALE_AFTER_S (kept in sync by test)
PID_DEAD_GRACE_S = 15.0        # main's pid gone + this grace => frozen fast (≤60s §exit)
SUPERVISE_INTERVAL_S = 15.0
DEADMAN_MAX_AGE_S = 30 * 60.0  # external cron threshold for the supervisor's own silence


def _env_path(env_key: str, default: str) -> str:
    return os.environ.get(env_key) or os.path.join(REPO_DIR, default)


def heartbeat_path() -> str:
    return _env_path("GHOST_HEARTBEAT_PATH", os.path.join("data", "heartbeat.json"))


def halt_file() -> str:
    return _env_path("GHOST_HALT_FILE", os.path.join("data", "HALTED"))


def halt_meta_file() -> str:
    return _env_path("GHOST_HALT_META", os.path.join("data", "HALTED.json"))


def beat_path() -> str:
    return _env_path("GHOST_SUPERVISOR_BEAT", os.path.join("data", "supervisor_beat.json"))


def alerts_log() -> str:
    return _env_path("GHOST_SUPERVISOR_ALERTS", os.path.join("data", "supervisor_alerts.log"))


# ------------------------------------------------------- file-only primitives

def heartbeat_age_s() -> float:
    """Seconds since the main process last beat. Missing/corrupt -> inf."""
    try:
        with open(heartbeat_path(), "r", encoding="utf-8") as f:
            return max(0.0, time.time() - float(json.load(f).get("ts", 0)))
    except Exception:
        return float("inf")


def main_pid_dead() -> bool:
    """True when the heartbeat records a pid that no longer exists (Linux
    /proc probe) — how a kill -9'd main is caught in seconds, not minutes."""
    try:
        with open(heartbeat_path(), "r", encoding="utf-8") as f:
            pid = int(json.load(f).get("pid", 0))
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False   # pid exists, just not ours


def _pid_dead_grace() -> float:
    return float(os.environ.get("GHOST_PID_DEAD_GRACE_S") or PID_DEAD_GRACE_S)


def main_halted() -> bool:
    return os.path.exists(halt_file())


def freeze(reason: str, detail: str = "") -> Dict[str, Any]:
    """Write the halt protocol DIRECTLY (files only) + bump the revocation
    epoch. Works even when the main process is a corpse — that is the point."""
    import uuid
    meta = {
        "halt_id": str(uuid.uuid4()),
        "reason": reason,
        "by": "supervisor",
        "detail": detail[:300],
        "tripped_at": time.time(),
        "tripped_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    }
    meta["revocation_epoch"] = bump_epoch(by="supervisor")   # BEFORE writing meta
    os.makedirs(os.path.dirname(halt_meta_file()), exist_ok=True)
    with open(halt_meta_file(), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    with open(halt_file(), "w", encoding="utf-8") as f:   # sentinel LAST
        f.write(meta["halt_id"])
    alert(f"freeze: {reason} — {detail}", event="freeze")
    return meta


def alert(message: str, event: str = "alarm") -> None:
    """Ping (b)/(c)-style: log file always, desktop notify best-effort."""
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} [{event}] {message}"
    try:
        os.makedirs(os.path.dirname(alerts_log()), exist_ok=True)
        with open(alerts_log(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        subprocess.run(["notify-send", "-u", "critical", "Ghost supervisor", message],
                       timeout=5, capture_output=True)
    except Exception:
        pass  # headless is fine; the log file is the durable trail


def beat() -> None:
    path = beat_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time()}, f)
    os.replace(tmp, path)


def beat_age_s() -> float:
    try:
        with open(beat_path(), "r", encoding="utf-8") as f:
            return max(0.0, time.time() - float(json.load(f).get("ts", 0)))
    except Exception:
        return float("inf")


# ------------------------------------------------------------------- checks

def check_cycle(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One supervision pass. Returns what it saw/did (also the test seam)."""
    result: Dict[str, Any] = {"heartbeat_age_s": heartbeat_age_s(), "actions": []}
    beat()

    # 1. main liveness: stale heartbeat OR a dead pid past its grace -> freeze
    #    + revoke. If a halt already stands, observe only.
    dead_pid = main_pid_dead()
    result["main_pid_dead"] = dead_pid
    stale = result["heartbeat_age_s"] > HEARTBEAT_STALE_S
    killed = dead_pid and result["heartbeat_age_s"] > _pid_dead_grace()
    if stale or killed:
        if main_halted():
            result["actions"].append("halt_already_standing")
        else:
            why = (f"no heartbeat for {result['heartbeat_age_s']:.0f}s "
                   f"(threshold {HEARTBEAT_STALE_S:.0f}s)" if stale
                   else f"main process (pid from heartbeat) is GONE and silent "
                        f"{result['heartbeat_age_s']:.0f}s")
            meta = freeze("heartbeat_lost", why + " — outbound frozen, "
                          "grants revoked; operator must resume")
            result["actions"].append(f"freeze(heartbeat_lost, epoch={meta['revocation_epoch']})")

    # 2. expected rhythm: missed deadlines alarm (they do NOT freeze — no
    #    work due is a policy question; a dead loop is a liveness question)
    from ghost.core import rhythm
    for name in sorted((rhythm.rhythm_config().get("events") or {}).keys()):
        state = rhythm.due(name, cfg)
        if state.get("due"):
            alert(f"rhythm missed: {state.get('reason')}", event=f"rhythm:{name}")
            result["actions"].append(f"alarm(rhythm:{name})")
    result["supervisor_beat_age_s"] = 0.0
    return result


def run_loop(interval_s: float = SUPERVISE_INTERVAL_S,
             max_cycles: Optional[int] = None) -> int:
    cycles = 0
    alert("supervisor started", event="lifecycle")
    while True:
        check_cycle()
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return cycles
        time.sleep(interval_s)


def deadman(threshold_s: float = DEADMAN_MAX_AGE_S) -> int:
    """External cron entry: alarm if the SUPERVISOR itself went silent."""
    age = beat_age_s()
    if age > threshold_s:
        alert(f"DEAD-MAN: supervisor silent for {age/60:.0f}min "
              f"(threshold {threshold_s/60:.0f}min) — who watches the watchmen? "
              "Restart `ghost supervise`.", event="deadman")
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ghost supervisor (separate process)")
    ap.add_argument("--interval", type=float, default=SUPERVISE_INTERVAL_S)
    ap.add_argument("--check-once", action="store_true",
                    help="one supervision cycle, then exit (tests / cron)")
    ap.add_argument("--deadman", action="store_true",
                    help="check the supervisor's own liveness marker; exit 1 = alarm")
    args = ap.parse_args(argv)
    if args.deadman:
        return deadman()
    if args.check_once:
        print(json.dumps(check_cycle(), indent=2, default=str))
        return 0
    return run_loop(args.interval)


if __name__ == "__main__":
    sys.exit(main())
