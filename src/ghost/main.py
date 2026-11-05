"""Entrypoint for Ghost Voice Assistant & Autonomy Runtime.

`python -m ghost.main` (default): uvicorn + full autonomy runtime in the same
process. All operator commands live in ghost.cli: `python -m ghost.main halt`,
`status`, `digest`, `run`, `chaos`, `voice-demo`, `metrics`.
"""
import uvicorn
import argparse
import sys

OPERATOR_COMMANDS = ("halt", "resume", "status", "digest", "run",
                     "chaos", "voice-demo", "metrics", "migrate", "parity",
                     "replay", "supervise", "deadman", "reconcile", "sim")


def main():
    parser = argparse.ArgumentParser(description="Ghost Voice Assistant & Autonomy Runtime")
    parser.add_argument("--host", default="127.0.0.1", help="Host address")
    parser.add_argument("--port", type=int, default=8765, help="Port")
    args, unknown = parser.parse_known_args()

    # Operator subcommands pass straight through to the CLI.
    if unknown and unknown[0] in OPERATOR_COMMANDS:
        from ghost.cli import main as cli_main
        sys.exit(cli_main(unknown))

    print(f"👻 Starting Ghost Voice Assistant + Autonomy Runtime on http://{args.host}:{args.port}")
    uvicorn.run("ghost.server.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
