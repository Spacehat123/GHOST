"""AUTHORITY PLANE — the grant daemon (finding #4): the key leaves the main
process entirely.

`ghost grantd` is a SEPARATE OS PROCESS holding GHOST_GRANT_SECRET. The main
process (brain + effects, the part that runs LLM-facing code) never sees the
key: it asks this daemon over a local unix domain socket. Code execution in
the main process can no longer mint authority — there is nothing there to
mint with.

Design constraints honored:
- the daemon imports ONLY pure modules (authority.protocol: no store, no
  audit, no effects) — it is not a second writer of ghost.db; its consumed
  nonces live in its OWN sqlite (data/grant_authority.db)
- every issue/verify re-checks the halt sentinel and revocation epoch from
  FILES, so a trip freezes authority even if the daemon outlives the main
  process
- fail closed: socket absent / daemon dead -> the main process cannot
  authorize (grants.py degrades honestly; tests keep an in-process fallback
  via GHOST_GRANT_SECRET for non-production use)

Run: `ghost grantd` (foreground; the supervisor should own it in production).
"""
import argparse
import json
import os
import socket
import sys
import threading
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from ghost.authority import protocol  # noqa: E402 — pure: no store, no audit

SOCKET_PATH_ENV = "GHOST_GRANT_SOCKET"


def socket_path() -> str:
    from ghost.paths import data_dir
    return os.environ.get(SOCKET_PATH_ENV) or os.path.join(
        data_dir(), "grantd.sock")


def handle(request: Dict[str, Any]) -> Dict[str, Any]:
    """One request. The ONLY two operations authority ever performs."""
    op = request.get("op")
    if op == "ping":
        return {"ok": True, "halt": protocol.halt_active(), "epoch": protocol.current_epoch()}
    if op == "issue":
        if protocol.halt_active():
            return {"ok": False, "error": "system halted — no authority minted"}
        token = protocol.sign(
            capability=str(request.get("capability", "")),
            subject=str(request.get("subject", "")),
            bounds=request.get("bounds") or {},
            ttl_s=float(request.get("ttl_s", protocol.DEFAULT_TTL_S)))
        if token is None:
            return {"ok": False, "error": "no signing secret configured"}
        return {"ok": True, "token": token}
    if op == "verify":
        bounds = protocol.verify(
            token=str(request.get("token", "")),
            capability=str(request.get("capability", "")),
            subject=request.get("subject"))
        if bounds is None:
            return {"ok": False, "error": "invalid, halted, revoked, expired or replayed"}
        return {"ok": True, "bounds": bounds}
    return {"ok": False, "error": f"unknown op {op!r}"}


def serve(sock_path: Optional[str] = None, stop: Optional[threading.Event] = None) -> None:
    path = sock_path or socket_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.unlink(path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    os.chmod(path, 0o600)          # local trust boundary only
    server.listen(8)
    server.settimeout(0.5)
    try:
        while not (stop and stop.is_set()):
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            with conn:
                try:
                    conn.settimeout(5)
                    buf = conn.recv(65536)
                    req = json.loads(buf.decode())
                    resp = handle(req)
                except Exception as e:
                    resp = {"ok": False, "error": str(e)[:200]}
                conn.sendall(json.dumps(resp).encode())
    finally:
        server.close()
        if os.path.exists(path):
            os.unlink(path)


# ------------------------------------------------------------- the RPC client

def call(request: Dict[str, Any], sock_path: Optional[str] = None,
         timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """Client side (main process). None = authority unreachable — callers
    fail closed. Never raises into callers."""
    path = sock_path or socket_path()
    if not os.path.exists(path):
        return None
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout)
        with client:
            client.connect(path)
            client.sendall(json.dumps(request).encode())
            buf = client.recv(65536)
        return json.loads(buf.decode())
    except Exception:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ghost grant authority daemon")
    ap.add_argument("--socket", default=None)
    ap.add_argument("--check", action="store_true",
                    help="ping the running daemon and exit")
    args = ap.parse_args(argv)
    if args.check:
        resp = call({"op": "ping"}, args.socket)
        print(json.dumps(resp or {"ok": False, "error": "authority unreachable"}))
        return 0 if resp and resp.get("ok") else 1
    if not os.environ.get("GHOST_GRANT_SECRET"):
        print("grantd: GHOST_GRANT_SECRET not set — refusing to run keyless", file=sys.stderr)
        return 1
    serve(args.socket)
    return 0


if __name__ == "__main__":
    sys.exit(main())
