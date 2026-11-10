"""Ghost CLI — the operator's hands on the machine (Roadmap §0.5, §3.4, wiring gap).

`ghost halt` stops all outbound in <5s. `ghost status` is the one-screen
answer. `ghost digest` renders today's harvest. `ghost run` is the headless
autonomy loop. `ghost chaos` runs the weekly kill-tests (§3.4). `ghost
voice-demo` records the §14.3 reference tape.

The operator is the ONLY one here — no LLM touches these commands.
"""
import argparse
import asyncio
import json
import sys


def cmd_halt(args) -> int:
    from ghost.core import halt as halt_mod
    meta = halt_mod.trip(reason=args.reason or halt_mod.REASON_OPERATOR, by="operator")
    print(f"🛑 HALTED — all outbound frozen.\n   reason: {meta['reason']}\n   at: {meta['tripped_at_iso']}")
    return 0


def cmd_resume(args) -> int:
    from ghost.core import halt as halt_mod
    prev = halt_mod.resume(by="operator")
    if prev:
        print(f"✅ Resumed (was halted {prev.get('halted_for_s', '?')}s, reason={prev.get('reason')})")
    else:
        print("Was not halted.")
    return 0


def cmd_status(args) -> int:
    from ghost.core import halt as halt_mod, heartbeat
    from ghost.core.jobqueue import JobQueue
    from ghost.core.audit import AuditLedger
    from ghost.autonomy.ladder import AutonomyLadder

    halted = halt_mod.is_halted()
    state = halt_mod.halt_state() or {}
    q = JobQueue().stats()
    print("┌─ GHOST STATUS " + "─" * 40)
    print(f"│ halt: {'🛑 HALTED' if halted else '✅ clear'}"
          + (f" (reason={state.get('reason')}, by={state.get('by')}, for {state.get('halted_for_s', '?')}s)" if halted else ""))
    print(f"│ heartbeat age: {heartbeat.age_s():.0f}s (stale > {heartbeat.STALE_AFTER_S:.0f}s)")
    print(f"│ queue: {json.dumps(q)}")
    led = AuditLedger.get_instance()
    print(f"│ today: {json.dumps(led.day_summary().get('events', {}))}")
    ladder = AutonomyLadder()
    print("│ ladder:")
    for cap, st in sorted(ladder.status().items()):
        print(f"│   {cap:20s} L{st['level']} (eligible L{st['eligible_for']}, streak {st['success_streak']})")
    print("└" + "─" * 55)
    return 0


def cmd_digest(args) -> int:
    from ghost.autonomy.digest import build_digest, write_digest_file
    from ghost.core.audit import AuditLedger
    content = build_digest(day=args.day, ledger=AuditLedger.get_instance())
    path = write_digest_file(content)
    print(content["markdown"])
    print(f"---\nwritten to {path}")
    return 0


def cmd_run(args) -> int:
    """Headless autonomy runtime: heartbeat + scheduler + queue worker."""
    from ghost.autonomy.runtime import AutonomyRuntime

    async def go():
        rt = AutonomyRuntime(scheduler_interval_s=args.interval)
        await rt.start()
        print("👻 Ghost runtime running. Ctrl-C to stand down.")
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            await rt.stop()
            print("Runtime stood down cleanly.")
    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        pass
    return 0


def cmd_chaos(args) -> int:
    """Weekly kill-tests (§3.4): recovery code that's never been run is fiction."""
    import unittest
    suite = unittest.defaultTestLoader.discover("tests", pattern="test_chaos*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def cmd_voice_demo(args) -> int:
    """§14.3: record the reference-parity tape with the real TTS sink."""
    from ghost.voice.narrator import Narrator, proof_of_honesty
    from ghost.core.audit import AuditLedger
    from ghost.voice.task_runner import TaskRunner, find_and_build_demo_flow

    sink_lines = []

    def sink(text: str) -> None:
        sink_lines.append(text)
        try:
            from ghost.voice.kokoro import KokoroSpeechEngine
            eng = KokoroSpeechEngine.get_instance()
            eng.initialize()
            if eng.is_available():
                import asyncio
                wav, _prov = asyncio.run(eng.synthesize_wav(text))
                if wav:
                    os.makedirs("data/voice-tape", exist_ok=True)
                    fname = f"data/voice-tape/line-{len(sink_lines):02d}.wav"
                    with open(fname, "wb") as f:
                        f.write(wav)
                    print(f"🔊 {text}   [{fname}]")
                    return
        except Exception as e:
            print(f"(tts unavailable: {e})")
        print(f"🔊 {text}")

    narrator = Narrator(speak=sink)

    async def go():
        runner = TaskRunner(narrator=narrator)
        return await runner.run(
            "voice-demo", "long", "finding an expensive restaurant without a website",
            find_and_build_demo_flow(
                {"count": 6, "top_business": "Salt"},
                {"screenshots": 10, "fps": 60.0},
                "https://salt-concept.vercel.app"))

    asyncio.run(go())
    ok = proof_of_honesty(narrator, AuditLedger.get_instance())
    print(f"\n{'✅' if ok else '❌'} tape honesty: every spoken line {'traces to' if ok else 'DOES NOT trace to'} a logged event")
    print(f"tape: {len(sink_lines)} lines in data/voice-tape/")
    return 0 if ok else 1


def cmd_migrate(args) -> int:
    """T1b: copy every store into data/ghost.db (old stores untouched = rollback)."""
    from ghost.store import migrator
    report = migrator.migrate_all()
    if getattr(args, "report_json", None):
        with open(args.report_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
    failed = [t for t, r in report.get("tables", {}).items()
              if r.get("state") in ("parity_failed", "error")]
    quarantined = sum(r.get("quarantined", 0) for r in report.get("tables", {}).values())
    print(json.dumps(report, indent=2, default=str))
    if failed:
        print(f"\n❌ PARITY FAILED for: {', '.join(failed)} — do not cutover")
        return 1
    print(f"\n✅ migration complete — {len(report.get('tables', {}))} tables, "
          f"{quarantined} rows quarantined (see migration_quarantine), "
          f"sources untouched")
    return 0


def cmd_parity(args) -> int:
    """T1c dual-read check: nothing in the legacy stores may be missing from
    the unified store. Drift = a code path still writing legacy post-cutover."""
    from ghost.store import parity
    report = parity.run()
    print(json.dumps(report, indent=2, default=str, sort_keys=True))
    if not report.get("ok"):
        print("\n❌ parity drift — history is missing from the unified store")
        return 1
    print("\n✅ parity clean — no legacy event or job is missing from ghost.db")
    return 0


def cmd_replay(args) -> int:
    """T1c: reconstruct a day's state from the event log alone (deterministic)."""
    from ghost.core.audit import AuditLedger
    from ghost.store.chain import verify_hash_chain
    from ghost.store.replay import derive_day
    import time as _t
    led = AuditLedger.get_instance()
    day = args.day or _t.strftime("%Y-%m-%d")
    state = derive_day(day, led)
    if led.unified:
        try:
            with led._conn() as conn:
                ok, detail = verify_hash_chain(conn, led.table)
        except Exception as e:
            ok, detail = False, f"chain check failed: {e}"
    else:
        # The legacy store has no hash chain columns — n/a, not a failure.
        ok, detail = None, "legacy store — no hash chain (cutover to unified to enable)"
    state["chain_ok"] = ok
    state["chain_detail"] = detail
    print(json.dumps(state, indent=2, sort_keys=True, default=str))
    return 0 if ok is not False else 1


def cmd_supervise(args) -> int:
    """T4: run the supervisor loop in THIS process (separate from main).
    File writes only — it never opens the store, even to freeze."""
    from ghost.supervisor import service
    return service.run_loop(interval_s=args.interval)


def cmd_deadman(args) -> int:
    """T4: external cron check — alarm if the SUPERVISOR went silent."""
    from ghost.supervisor import service
    rc = service.deadman(threshold_s=args.threshold * 60.0)
    if rc == 0:
        print("supervisor liveness OK")
    return rc


def cmd_reconcile(args) -> int:
    """T5: Ghost's records vs reality — payments, sends, deploys."""
    from ghost.verify import reconciliation as reco
    payment_fetcher = reco.razorpay_payment_fetcher()   # None when unconfigured
    report = reco.run_all(payment_fetcher=payment_fetcher)
    print(json.dumps(report, indent=2, default=str, sort_keys=True))
    if report.get("mismatch_sections"):
        print(f"\n❌ reconciliation mismatches: {', '.join(report['mismatch_sections'])}")
        return 1
    print("\n✅ reconciliation clean (or honestly not configured)")
    return 0


def cmd_sim(args) -> int:
    """T6: a simulated day — 50 fixture leads, zero operator input,
    deterministic. The proof harness every later phase must pass. Runs the
    day twice and records the determinism receipt (T10 preflight gate 9)."""
    from ghost.sim.day import record_sim_receipt
    receipt = record_sim_receipt(seed=args.seed, n_leads=args.leads,
                                 cycles=args.cycles)
    print(json.dumps(receipt, indent=2, sort_keys=True, default=str))
    if receipt["ok"]:
        print(f"\n✅ simulated day complete — {receipt['decisions']} decisions, "
              f"determinism re-proven (same seed, two runs, identical day); "
              f"receipt at {receipt['path']}")
        return 0
    print("\n❌ simulated day FAILED its determinism re-check — "
          "the receipt records the failure; the dark run will refuse to start")
    return 1


def cmd_darkrun(args) -> int:
    """T10: the 30-day dark run harness — preflight / start / status / stop."""
    from ghost.control import darkrun
    if args.action == "preflight":
        rep = darkrun.preflight()
        print(json.dumps(rep, indent=2, sort_keys=True, default=str))
        if rep["ready"]:
            print("\n✅ preflight green — say the word and `ghost darkrun --action start`")
            return 0
        print("\n❌ NOT READY — operator actions required:")
        for a in rep["operator_actions"]:
            print(f"  - {a}")
        return 1
    if args.action == "start":
        out = darkrun.start(force=args.force)
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
        return 0 if out.get("started") else 1
    if args.action == "status":
        out = darkrun.status()
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
        return 0
    if args.action == "stop":
        out = darkrun.stop()
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
        return 0
    print(f"unknown action {args.action}")
    return 2


def cmd_grantd(args) -> int:
    """P4: run the grant authority daemon — the signing key leaves the main
    process. Foreground; the supervisor owns it in production."""
    from ghost.authority import service
    return service.main(["--socket", args.socket] if args.socket else [])


def cmd_metrics(args) -> int:
    from ghost.core.metrics import compute_metrics
    print(json.dumps(compute_metrics(), indent=2, default=str))
    return 0


def cmd_fixer(args) -> int:
    """P7: the staged-fix approval queue — list, or approve one package."""
    from ghost.control.fixers import approve_staged, list_staged
    if args.action == "list":
        rows = list_staged()
        print(json.dumps(rows, indent=2, default=str))
        pending = [r for r in rows if r.get("status") == "staged"]
        print(f"\n{len(pending)} staged fix(es) awaiting your approval")
        return 0
    out = approve_staged(args.staged_id, by="operator")
    print(json.dumps(out, indent=2, default=str))
    if out.get("applied"):
        print(f"\n✅ staged fix {out['staged_id']} applied to: {', '.join(out['files'])}")
        return 0
    print(f"\n❌ not applied: {out.get('reason')}")
    return 1


def cmd_export(args) -> int:
    from ghost.workers.export_demo_to_production import export_demo_to_production
    try:
        res = export_demo_to_production(args.slug, output_base_dir=args.out_dir)
        print(f"✅ Exported demo '{args.slug}' to {res['target_dir']} ({res['files_created']} files, Next.js 14 App Router)")
        return 0
    except Exception as e:
        print(f"❌ Failed to export '{args.slug}': {e}")
        return 1


COMMANDS = {
    "halt": (cmd_halt, "freeze ALL outbound now (the throat-cut handle)"),
    "resume": (cmd_resume, "clear a halt"),
    "status": (cmd_status, "one-screen answer: what is Ghost doing"),
    "digest": (cmd_digest, "render the daily digest (optionally for --day YYYY-MM-DD)"),
    "run": (cmd_run, "start the headless autonomy runtime"),
    "chaos": (cmd_chaos, "run the weekly kill-tests (§3.4)"),
    "voice-demo": (cmd_voice_demo, "record the §14.3 reference voice tape"),
    "metrics": (cmd_metrics, "print the §12 scoreboard from the audit ledger"),
    "migrate": (cmd_migrate, "T1b: copy all stores into data/ghost.db (sources untouched)"),
    "parity": (cmd_parity, "T1c: legacy vs unified stores — no history may be missing"),
    "replay": (cmd_replay, "T1c: reconstruct a day from events (--day YYYY-MM-DD)"),
    "supervise": (cmd_supervise, "T4: run the kill-switch supervisor (file writes only)"),
    "deadman": (cmd_deadman, "T4: cron check — is the supervisor itself alive? (--threshold minutes)"),
    "reconcile": (cmd_reconcile, "T5: records vs reality — payments, sends, deploys"),
    "sim": (cmd_sim, "T6: simulated day with fixture leads (deterministic)"),
    "darkrun": (cmd_darkrun, "T10: 30-day dark run — preflight/start/status/stop"),
    "grantd": (cmd_grantd, "P4: grant authority daemon (key isolation; run alongside supervise)"),
    "fixer": (cmd_fixer, "P7: staged fixes — list the approval queue / approve one (--staged-id)"),
    "export": (cmd_export, "export an approved demo to a production Next.js 14 App Router project"),
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ghost", description="Ghost operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (fn, help_text) in COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        if name == "halt":
            p.add_argument("--reason", default=None, help="why (recorded in the ledger)")
        if name == "digest":
            p.add_argument("--day", default=None, help="YYYY-MM-DD (default today)")
        if name == "run":
            p.add_argument("--interval", type=float, default=300.0,
                           help="scheduler cycle seconds (default 300)")
        if name == "migrate":
            p.add_argument("--report-json", default=None,
                           help="write the migration report dict to this path")
        if name == "replay":
            p.add_argument("--day", default=None, help="YYYY-MM-DD (default today)")
        if name == "supervise":
            p.add_argument("--interval", type=float, default=15.0,
                           help="supervision cycle seconds (default 15)")
        if name == "deadman":
            p.add_argument("--threshold", type=float, default=30.0,
                           help="alarm threshold minutes (default 30)")
        if name == "sim":
            p.add_argument("--seed", type=int, default=20261106)
            p.add_argument("--leads", type=int, default=50)
            p.add_argument("--cycles", type=int, default=5)
        if name == "grantd":
            p.add_argument("--socket", default=None, help="override the unix socket path")
        if name == "fixer":
            p.add_argument("--action", default="list", choices=["list", "approve"])
            p.add_argument("--staged-id", default=None, help="staged fix id to approve")
        if name == "darkrun":
            p.add_argument("--action", required=True,
                           choices=["preflight", "start", "status", "stop"])
            p.add_argument("--force", action="store_true",
                           help="start despite failed preflight (recorded as forced)")
        if name == "export":
            p.add_argument("slug", help="demo slug to export to production Next.js codebase")
            p.add_argument("--out-dir", default=None, help="output base directory (default dist/production)")
        p.set_defaults(fn=fn)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
