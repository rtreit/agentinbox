"""CLI entry point for Agent Inbox.

Usage:
    python -m agentinbox                  # One-shot: process all pending directives
    python -m agentinbox --peek           # Peek at queue without consuming
    python -m agentinbox daemon           # Run persistent daemon
    python -m agentinbox daemon --agent-name hal --interval 10
"""
from __future__ import annotations

import argparse
import sys

from .config import load_config
from .inbox import get_all_directives, peek_messages
from .daemon import run_daemon


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentinbox",
        description="Agent Inbox — multi-agent GroupMe message router and executor",
    )
    parser.add_argument(
        "--config", "-c",
        help="Path to agentinbox.toml config file",
    )
    parser.add_argument(
        "--agent-name",
        help="Agent name (default: hal)",
    )
    parser.add_argument(
        "--queue-name",
        help="Azure Storage Queue name (default: agentinbox-{agent_name})",
    )

    sub = parser.add_subparsers(dest="command")

    # daemon subcommand
    daemon_parser = sub.add_parser("daemon", help="Run persistent daemon")
    daemon_parser.add_argument(
        "--interval", type=float,
        help="Poll interval in seconds (default: 10)",
    )
    daemon_parser.add_argument(
        "--executor",
        choices=["copilot", "command", "python"],
        help="Executor type",
    )
    daemon_parser.add_argument(
        "--executor-command",
        help="Command for command/python executors",
    )
    daemon_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print directives without executing",
    )

    # peek subcommand
    sub.add_parser("peek", help="Peek at queue without consuming")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config = load_config(args)

    if args.command == "peek":
        # Peek mode
        print(f"Agent: {config.agent_name}  Queue: {config.resolved_queue_name}")
        messages = peek_messages(config)
        if not messages:
            print("Queue is empty.")
        else:
            print(f"{len(messages)} message(s):")
            for m in messages:
                if m["parsed"]:
                    sender = m["parsed"].get("sender", {}).get("name", "?")
                    text = m["parsed"].get("message", {}).get("text", "?")[:80]
                    print(f"  [{sender}] {text}")
                else:
                    print(f"  [unparseable] {m['raw']}")
        return 0

    elif args.command == "daemon":
        # Persistent daemon mode
        if getattr(args, "dry_run", False):
            print(f"[DRY RUN] Agent: {config.agent_name}  Queue: {config.resolved_queue_name}")
            directives = get_all_directives(config)
            if not directives:
                print("No pending directives.")
            for d in directives:
                print(f"  [{d['sender_name']}] {d['instruction']}")
            return 0

        run_daemon(config)
        return 0

    else:
        # Default: one-shot mode — process all pending directives
        print(f"Agent: {config.agent_name}  Queue: {config.resolved_queue_name}")
        directives = get_all_directives(config)
        if not directives:
            print("No pending directives.")
        else:
            print(f"Received {len(directives)} directive(s):")
            for d in directives:
                print(f"  [{d['sender_name']}] {d['instruction']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
