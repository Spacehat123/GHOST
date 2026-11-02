#!/usr/bin/env python3
"""CLI interface for Ghost's Directive & Instruction Tracking (follow.py)."""
import sys
import os
import argparse
import json

# Ensure Ghost source is on path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(SCRIPT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from ghost.core.follow import DirectiveManager

def main():
    parser = argparse.ArgumentParser(
        prog="follow.py",
        description="Ghost Live Preference & Directive Tracker CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # Add command
    parser_add = subparsers.add_parser("add", help="Add a new directive or fact")
    parser_add.add_argument("text", type=str, help="The instruction, rule, or preference to remember")
    parser_add.add_argument("--fact", action="store_true", help="Store as a known fact instead of a behavioral directive")

    # List command
    parser_list = subparsers.add_parser("list", help="List all active directives and profile facts")
    parser_list.add_argument("--json", action="store_true", help="Output raw JSON format")

    # Remove command
    parser_remove = subparsers.add_parser("remove", help="Remove an instruction or fact by ID or matching text")
    parser_remove.add_argument("id", type=str, help="ID (e.g. d1, f2) or substring of the directive to delete")

    # Snapshot command
    subparsers.add_parser("snapshot", help="Print the compact pre-baked prompt envelope snapshot (< 250 tokens)")

    args = parser.parse_args()

    if not args.command or args.command == "list":
        data = DirectiveManager.load()
        if getattr(args, "json", False):
            print(json.dumps(data, indent=2))
            return

        print("\n👻 GHOST CORE MEMORY & DIRECTIVES")
        print("=" * 50)
        print(f"User: {data.get('user_name', 'Pranc')}")
        env = data.get("environment", {})
        print(f"Environment: {', '.join(f'{k}: {v}' for k, v in env.items())}\n")

        print("📋 Active Directives:")
        directives = data.get("directives", [])
        if not directives:
            print("  (No active directives)")
        for d in directives:
            print(f"  [{d.get('id')}] {d.get('text')}")

        print("\n🧠 Known Facts:")
        facts = data.get("facts", [])
        if not facts:
            print("  (No known facts)")
        for f in facts:
            print(f"  [{f.get('id')}] {f.get('text')}")

        print("=" * 50)
        print("Run `python3 follow.py add \"<instruction>\"` to add more.\n")

    elif args.command == "add":
        cat = "facts" if args.fact else "directives"
        res = DirectiveManager.add_directive(args.text, category=cat)
        if res.get("action") == "already_exists":
            print(f"ℹ️ Directive already exists [{res.get('id')}]: {args.text}")
        else:
            print(f"✅ Directive saved [{res.get('id')}] in {cat}: \"{args.text}\"")

    elif args.command == "remove":
        res = DirectiveManager.remove_directive(args.id)
        if res.get("success"):
            for item in res.get("removed", []):
                print(f"🗑️ Removed [{item.get('id')}]: {item.get('text')}")
        else:
            print(f"❌ Error: {res.get('error')}")

    elif args.command == "snapshot":
        print(DirectiveManager.get_snapshot())

if __name__ == "__main__":
    main()
