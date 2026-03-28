"""Agent Inbox daemon — polls queue and dispatches to executors.

Runs as a persistent process (interactive or Windows service).
Handles orphan recovery, quick commands, structured logging,
and chat-aware reply routing.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# In Session 0 (Windows service), stdout/stderr default to cp1252 which can't
# encode emoji.  Replace un-encodable chars instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass

from .config import Config, load_config
from .executor import ExecutionContext, Executor
from .executors.copilot import CopilotExecutor
from .executors.command import CommandExecutor
from .executors.python_script import PythonScriptExecutor
from .inbox import get_all_directives
from .notify import post as groupme_post
from . import task_tracker


def _log_entry(log_dir: Path, entry: dict) -> None:
    """Append a structured JSON log entry."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "daemon.jsonl"
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _create_executor(config: Config) -> Executor:
    """Create an executor based on config."""
    exec_type = config.executor_type.lower()
    if exec_type == "copilot":
        return CopilotExecutor()
    elif exec_type == "command":
        return CommandExecutor(config.executor_command)
    elif exec_type == "python":
        return PythonScriptExecutor(config.executor_command)
    else:
        raise ValueError(f"Unknown executor type: {exec_type}")


def _try_quick_handle(instruction: str) -> str | None:
    """Handle trivial commands locally without spawning an executor."""
    lower = instruction.lower().strip()

    if lower in ("ping", "hello", "hi", "hey"):
        return "🤖 pong!"

    if lower in ("status", "health"):
        return "🤖 Online and listening."

    if lower == "help":
        return (
            "🤖 I process directives from this chat. "
            "Send me a natural-language instruction and I'll do my best. "
            "Commands: ping, status, help, time"
        )

    if lower in ("time", "what time is it", "what time is it?"):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"🤖 {now}"

    return None


def _recover_orphans(config: Config, log_dir: Path) -> None:
    """Check for orphaned tasks from a previous crash and notify."""
    orphaned = task_tracker.get_orphaned_tasks(log_dir)
    if not orphaned:
        return

    msg_parts = [f"🤖 Found {len(orphaned)} orphaned task(s) from previous run:"]
    for t in orphaned[:5]:
        msg_parts.append(f"  - [{t.get('sender', '?')}] {t.get('instruction', '?')[:80]}")

    msg = "\n".join(msg_parts)
    groupme_post(msg, bot_id=config.default_bot_id)
    _log_entry(log_dir, {"event": "orphan_recovery", "count": len(orphaned)})
    task_tracker.clear_all_orphaned(log_dir)

    # Also check for orphaned reply files
    for reply_file in log_dir.glob("reply_*.txt"):
        try:
            reply_text = reply_file.read_text(encoding="utf-8").strip()
            if reply_text:
                groupme_post(f"🤖 (recovered reply) {reply_text}", bot_id=config.default_bot_id)
            reply_file.unlink()
        except Exception:
            pass


def _dispatch_directive(directive: dict, config: Config, executor: Executor, log_dir: Path) -> None:
    """Dispatch a single directive to the executor."""
    instruction = directive["instruction"]
    sender_name = directive["sender_name"]
    message_id = directive["message_id"]
    reply_bot_id = config.bot_id_for_chat(directive.get("group_id")) or directive.get(
        "reply_bot_id"
    )

    _log_entry(log_dir, {
        "event": "dispatch",
        "sender": sender_name,
        "instruction": instruction[:200],
        "message_id": message_id,
    })

    # Try quick handle first
    quick = _try_quick_handle(instruction)
    if quick:
        groupme_post(quick, bot_id=reply_bot_id)
        _log_entry(log_dir, {
            "event": "quick_handle",
            "message_id": message_id,
            "reply_chars": len(quick),
        })
        return

    # Full executor dispatch
    task_tracker.track_accepted(log_dir, message_id, instruction, sender_name)

    ctx = ExecutionContext(
        instruction=instruction,
        sender_name=sender_name,
        message_id=message_id,
        working_directory=config.resolved_working_directory,
        raw_text=directive.get("raw_text", ""),
    )

    start = time.time()
    task_tracker.track_dispatched(log_dir, message_id)

    try:
        result = executor.execute(ctx)
        elapsed = time.time() - start

        reply_text = result.reply_text or "Done — task completed."

        # Truncate if needed
        if len(reply_text) > 1000:
            reply_text = reply_text[:997] + "..."

        groupme_post(reply_text, bot_id=reply_bot_id)

        if result.success:
            task_tracker.track_completed(log_dir, message_id)
        else:
            task_tracker.track_failed(log_dir, message_id, result.error)

        _log_entry(log_dir, {
            "event": "completed",
            "message_id": message_id,
            "exit_code": result.exit_code,
            "elapsed_seconds": round(elapsed, 1),
            "reply_chars": len(reply_text),
            "success": result.success,
        })

    except Exception as exc:
        elapsed = time.time() - start
        error_msg = f"🤖 Error processing task: {exc}"
        groupme_post(error_msg, bot_id=reply_bot_id)
        task_tracker.track_failed(log_dir, message_id, str(exc))
        _log_entry(log_dir, {
            "event": "error",
            "message_id": message_id,
            "error": str(exc),
            "elapsed_seconds": round(elapsed, 1),
        })


def run_daemon(config: Config) -> None:
    """Main daemon loop — poll queue and dispatch directives."""
    log_dir = config.resolved_log_directory
    log_dir.mkdir(parents=True, exist_ok=True)

    executor = _create_executor(config)
    startup_warning = (
        executor.startup_warning()
        if isinstance(executor, CopilotExecutor)
        else None
    )

    print(f"Agent Inbox daemon started")
    print(f"  Agent: {config.agent_name}")
    print(f"  Queue: {config.resolved_queue_name}")
    print(f"  Executor: {executor.name()}")
    if isinstance(executor, CopilotExecutor):
        print(f"  Copilot: {executor.resolved_path or '(not found)'}")
    print(f"  Working dir: {Path(config.working_directory).resolve()}")
    print(f"  Interval: {config.poll_interval}s")
    print(f"  Log dir: {log_dir}")
    if startup_warning:
        print(f"  {startup_warning}")
    print()

    _log_entry(log_dir, {
        "event": "daemon_start",
        "agent": config.agent_name,
        "queue": config.resolved_queue_name,
        "executor": executor.name(),
        "copilot": executor.resolved_path if isinstance(executor, CopilotExecutor) else None,
        "interval": config.poll_interval,
    })
    if startup_warning:
        _log_entry(log_dir, {
            "event": "startup_warning",
            "agent": config.agent_name,
            "warning": startup_warning,
        })

    # Recover orphaned tasks from previous runs
    _recover_orphans(config, log_dir)

    # Dedup cache: message_id → timestamp (prevents processing the same
    # GroupMe message twice when multiple bots forward to the same queue)
    seen_message_ids: dict[str, float] = {}
    DEDUP_WINDOW = 300  # 5 minutes

    try:
        while True:
            try:
                # Prune expired entries from dedup cache
                now = time.time()
                expired = [k for k, v in seen_message_ids.items() if now - v > DEDUP_WINDOW]
                for k in expired:
                    del seen_message_ids[k]

                directives = get_all_directives(config, pre_seen_ids=set(seen_message_ids))
                for directive in directives:
                    mid = directive.get("message_id", "")
                    if mid and mid in seen_message_ids:
                        print(f"  skip duplicate (dedup cache): {mid}")
                        _log_entry(log_dir, {"event": "dedup_skip", "message_id": mid})
                        continue
                    if mid:
                        seen_message_ids[mid] = time.time()
                    _dispatch_directive(directive, config, executor, log_dir)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"  error in poll loop: {exc}", file=sys.stderr)
                _log_entry(log_dir, {"event": "poll_error", "error": str(exc)})

            time.sleep(config.poll_interval)

    except KeyboardInterrupt:
        print("\nDaemon stopped.")
        _log_entry(log_dir, {"event": "daemon_stop", "reason": "keyboard_interrupt"})
