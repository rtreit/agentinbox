"""Persistent in-flight task tracker.

Tracks tasks through their lifecycle: accepted → dispatched → completed/failed.
Detects orphaned tasks after daemon restart/crash.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save(path: Path, tasks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tasks, indent=2), encoding="utf-8")


def _tasks_file(log_dir: Path) -> Path:
    return log_dir / "pending_tasks.json"


def track_accepted(log_dir: Path, message_id: str, instruction: str, sender: str) -> None:
    """Record that a task has been accepted from the queue."""
    path = _tasks_file(log_dir)
    tasks = _load(path)
    tasks.append({
        "message_id": message_id,
        "instruction": instruction[:200],
        "sender": sender,
        "status": "accepted",
        "accepted_at": time.time(),
    })
    _save(path, tasks)


def track_dispatched(log_dir: Path, message_id: str, pid: int | None = None) -> None:
    """Record that a task has been dispatched to an executor."""
    path = _tasks_file(log_dir)
    tasks = _load(path)
    for t in tasks:
        if t.get("message_id") == message_id:
            t["status"] = "dispatched"
            t["dispatched_at"] = time.time()
            if pid:
                t["executor_pid"] = pid
    _save(path, tasks)


def track_completed(log_dir: Path, message_id: str) -> None:
    """Record that a task completed successfully and remove it."""
    path = _tasks_file(log_dir)
    tasks = _load(path)
    tasks = [t for t in tasks if t.get("message_id") != message_id]
    _save(path, tasks)


def track_failed(log_dir: Path, message_id: str, error: str) -> None:
    """Record that a task failed and remove it."""
    path = _tasks_file(log_dir)
    tasks = _load(path)
    tasks = [t for t in tasks if t.get("message_id") != message_id]
    _save(path, tasks)


def get_orphaned_tasks(log_dir: Path, max_age_seconds: float = 3600) -> list[dict]:
    """Return tasks that appear orphaned (dispatched but not completed)."""
    path = _tasks_file(log_dir)
    tasks = _load(path)
    now = time.time()
    orphaned = []
    for t in tasks:
        age = now - t.get("dispatched_at", t.get("accepted_at", now))
        if age > max_age_seconds or t.get("status") in ("accepted", "dispatched"):
            orphaned.append(t)
    return orphaned


def clear_orphaned(log_dir: Path, message_id: str) -> None:
    """Remove a specific orphaned task."""
    path = _tasks_file(log_dir)
    tasks = _load(path)
    tasks = [t for t in tasks if t.get("message_id") != message_id]
    _save(path, tasks)


def clear_all_orphaned(log_dir: Path) -> None:
    """Remove all tasks (used after orphan recovery)."""
    path = _tasks_file(log_dir)
    _save(path, [])
