"""P4/P5 authority tests — the key physically leaves the main process.

THE isolation proof: a daemon subprocess holds GHOST_GRANT_SECRET; THIS test
process (standing in for the brain/effects process) has NO secret anywhere in
its environment — yet grants work via the daemon, and there is nothing in
this process to steal or mint with.

Also covers: daemon-first routing in grants.py, cross-process replay
protection (O(1) nonce table), halt/epoch enforcement by the daemon, honest
fallbacks, and fail-closed behavior with neither daemon nor secret.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp(prefix="ghost-auth-")

from ghost.authority import protocol
from ghost.authority.service import call as authority_call, handle, serve
from ghost.core import grants


def _fresh_env(extra=None):
    env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src"))
    env.pop("GHOST_GRANT_SECRET", None)
    env.update(extra or {})
    return env


class TestIsolationTwoProcess(unittest.TestCase):
    """The daemon holds the key; this process cannot even read it."""

    def setUp(self):
        self.sock = os.path.join(TMP, f"grantd-{uuid.uuid4().hex}.sock")
        self.nonce_db = os.path.join(TMP, f"nonces-{uuid.uuid4().hex}.db")
        self.audit_db = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
        self.halt_file = os.path.join(TMP, f"HALTED-{uuid.uuid4().hex}")
        self.halt_meta = self.halt_file + ".json"
        # the DAEMON's secret — never exported to this process
        self.secret = f"authd-{uuid.uuid4().hex}"
        env = _fresh_env({
            "GHOST_GRANT_SECRET": self.secret,
            "GHOST_GRANT_SOCKET": self.sock,
            "GHOST_AUTHORITY_NONCE_DB": self.nonce_db,
            "GHOST_AUDIT_DB_PATH": self.audit_db,
            "GHOST_HALT_FILE": self.halt_file,
            "GHOST_HALT_META": self.halt_meta,
            "GHOST_REVOCATION_EPOCH": os.path.join(TMP, f"epoch-{uuid.uuid4().hex}"),
        })
        self.daemon = subprocess.Popen(
            [sys.executable, "-m", "ghost.authority.service", "--socket", self.sock],
            env=env, cwd=REPO,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # THIS process (the "brain/effects" stand-in): no secret anywhere
        for k in ("GHOST_GRANT_SECRET", "GHOST_AUTHORITY_NONCE_DB"):
            os.environ.pop(k, None)
        os.environ["GHOST_GRANT_SOCKET"] = self.sock
        os.environ["GHOST_AUDIT_DB_PATH"] = self.audit_db
        os.environ["GHOST_HALT_FILE"] = self.halt_file
        os.environ["GHOST_HALT_META"] = self.halt_meta
        deadline = time.time() + 10
        while time.time() < deadline:
            if authority_call({"op": "ping"}, self.sock):
                break
            time.sleep(0.05)
        else:
            self.fail("grantd did not come up")
        self.addCleanup(self._stop)

    def _stop(self):
        self.daemon.terminate()
        self.daemon.wait(timeout=10)
        os.environ.pop("GHOST_GRANT_SOCKET", None)
        for p in (self.halt_file, self.halt_meta):
            if os.path.exists(p):
                os.remove(p)

    def test_the_isolation_proof(self):
        # 1. the key is NOT in this process — nothing to exfiltrate or mint with
        self.assertNotIn("GHOST_GRANT_SECRET", os.environ)
        # 2. yet authority works: issue + verify route through the daemon
        token = grants.issue("outbound_email", "iso-lead", {"daily_cap": 5})
        self.assertIsNotNone(token, "daemon must authorize without a local secret")
        bounds = grants.verify(token, capability="outbound_email", subject="iso-lead")
        self.assertEqual(bounds, {"daily_cap": 5})
        # 3. a local protocol attempt WITHOUT the secret cannot even sign
        self.assertIsNone(protocol.sign("outbound_email", "forged", {}, secret=None))
        # 4. forged tokens are refused by the daemon
        self.assertIsNone(grants.verify("gt.forged.deadbeef",
                                        capability="outbound_email"))

    def test_cross_process_replay_once_only(self):
        tok1 = grants.issue("outbound_email", "r-lead", {})
        tok2 = grants.issue("outbound_email", "r-lead", {})
        self.assertIsNotNone(grants.verify(tok1, capability="outbound_email", subject="r-lead"))
        self.assertIsNone(grants.verify(tok1, capability="outbound_email", subject="r-lead"),
                          "replay must fail (nonce consumed in the DAEMON's store)")
        self.assertIsNotNone(grants.verify(tok2, capability="outbound_email", subject="r-lead"))

    def test_halt_freezes_the_daemons_authority(self):
        with open(self.halt_file, "w") as f:
            f.write("halted")
        try:
            self.assertIsNone(grants.issue("outbound_email", "halt-lead", {}),
                              "a halted system mints no authority — enforced BY THE DAEMON")
        finally:
            os.remove(self.halt_file)

    def test_epoch_mismatch_kills_pre_trip_tokens(self):
        tok = grants.issue("outbound_email", "e-lead", {})
        self.assertIsNotNone(tok)
        epoch_file = os.environ.get("GHOST_REVOCATION_EPOCH") or os.path.join(TMP, "EPOCH")
        # the daemon re-resolves its own epoch env — bump via its path
        daemon_epoch = subprocess.run(
            [sys.executable, "-c",
             "from ghost.core.revocation import bump_epoch; print(bump_epoch())"],
            env=_fresh_env({"GHOST_REVOCATION_EPOCH": epoch_file}),
            capture_output=True, text=True, cwd=REPO)
        self.assertEqual(daemon_epoch.returncode, 0, daemon_epoch.stderr)
        self.assertIsNone(grants.verify(tok, capability="outbound_email", subject="e-lead"),
                          "epoch bump kills pre-trip authority")


class TestInProcessFallback(unittest.TestCase):

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GHOST_GRANT_SECRET", "GHOST_GRANT_SOCKET",
                        "GHOST_AUTHORITY_NONCE_DB", "GHOST_AUDIT_DB_PATH",
                        "GHOST_HALT_FILE", "GHOST_HALT_META")}
        os.environ.pop("GHOST_GRANT_SOCKET", None)   # no daemon in these tests
        os.environ["GHOST_GRANT_SECRET"] = f"fb-{uuid.uuid4().hex}"
        os.environ["GHOST_AUTHORITY_NONCE_DB"] = os.path.join(TMP, f"n-{uuid.uuid4().hex}.db")
        os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"a-{uuid.uuid4().hex}.db")
        os.environ["GHOST_HALT_FILE"] = os.path.join(TMP, f"H-{uuid.uuid4().hex}")
        os.environ["GHOST_HALT_META"] = os.environ["GHOST_HALT_FILE"] + ".json"
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_fallback_issue_verify_replay(self):
        tok = grants.issue("outbound_email", "fb-lead", {"x": 1})
        self.assertIsNotNone(tok)
        self.assertEqual(grants.verify(tok, capability="outbound_email", subject="fb-lead"),
                         {"x": 1})
        self.assertIsNone(grants.verify(tok, capability="outbound_email", subject="fb-lead"),
                          "fallback path replays fail too (O(1) nonce table)")

    def test_neither_daemon_nor_secret_fails_closed(self):
        os.environ.pop("GHOST_GRANT_SECRET", None)
        self.assertIsNone(grants.issue("outbound_email", "nolead", {}))
        self.assertIsNone(grants.verify("gt.x.y", capability="outbound_email"))

    def test_nonce_table_o1_not_a_scan(self):
        """P5: replay protection is a table lookup — fill it beyond the old
        500-event scan window and replay STILL fails."""
        toks = [grants.issue("outbound_email", f"bulk-{i}", {}) for i in range(600)]
        victim = toks[0]
        self.assertIsNotNone(grants.verify(victim, capability="outbound_email", subject="bulk-0"))
        # 599 more consumptions push the victim far past any 500-row window
        for i, t in enumerate(toks[1:], start=1):
            self.assertIsNotNone(grants.verify(t, capability="outbound_email", subject=f"bulk-{i}"))
        self.assertIsNone(grants.verify(victim, capability="outbound_email", subject="bulk-0"),
                          "the old 500-event scan would have FORGOTTEN this nonce; the table doesn't")

    def test_protocol_purity_no_store_imports(self):
        """The daemon must never touch ghost.db — verify statically (the
        single-writer rule's structural half on the authority side)."""
        import ast
        src = open(os.path.join(REPO, "src", "ghost", "authority", "protocol.py")).read()
        banned = ("ghost.core.audit", "ghost.store", "ghost.crm", "sqlite3.connect")
        tree = ast.parse(src)
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(node.module == b or node.module.startswith(b + ".") for b in banned):
                found.append(node.module)
        self.assertEqual(found, [], f"authority/protocol.py must stay pure: {found}")


class TestHandleMatrix(unittest.TestCase):

    def test_handle_matrix(self):
        self.assertEqual(handle({"op": "ping"})["ok"], True)
        self.assertEqual(handle({"op": "nope"})["ok"], False)
        self.assertEqual(handle({})["ok"], False)


if __name__ == "__main__":
    unittest.main()
