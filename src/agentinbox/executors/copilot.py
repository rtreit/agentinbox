"""Copilot CLI executor.

Launches `copilot -p <prompt> --yolo --autopilot` and reads the reply
from a file written by the Copilot session.
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..executor import ExecutionContext, ExecutionResult, Executor


def _find_copilot() -> str | None:
    """Locate the copilot CLI executable."""
    # Check PATH first
    found = shutil.which("copilot")
    if found:
        return found

    # Common Windows install locations
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "copilot.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "copilot-cli" / "copilot.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return None


def _is_session_zero() -> bool:
    """Return True if running in Session 0 (Windows service context)."""
    if sys.platform != "win32":
        return False
    try:
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        session_id = ctypes.c_ulong()
        if ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session_id)):
            return session_id.value == 0
    except Exception:
        pass
    return False


def _powershell_literal(s: str) -> str:
    """Escape a string for use as a PowerShell single-quoted literal."""
    return s.replace("'", "''")


def _read_tail(path: Path, max_chars: int = 1200) -> str:
    """Read the tail of a log file, tolerating encoding issues."""
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


class CopilotExecutor(Executor):
    """Execute instructions via the Copilot CLI."""

    def __init__(self, copilot_path: str | None = None):
        self._copilot_path = copilot_path or _find_copilot()

    def name(self) -> str:
        return "CopilotExecutor"

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        if not self._copilot_path:
            return ExecutionResult(
                success=False,
                error="copilot CLI not found on PATH",
                exit_code=1,
            )

        log_dir = Path(ctx.working_directory) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", ctx.message_id)
        reply_file = log_dir / f"reply_{safe_id}.txt"
        prompt_file = log_dir / f"prompt_{safe_id}.txt"

        stderr_path = log_dir / f"copilot_stderr_{safe_id}.log"
        stderr_path.unlink(missing_ok=True)

        # Build the prompt
        prompt = (
            f"GroupMe directive from {ctx.sender_name} "
            f"(message_id: {ctx.message_id}):\n\n"
            f"{ctx.instruction}\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Carry out the request above. Run commands, inspect files, and do what is needed.\n"
            "2. You MUST write a useful, substantive plain-text answer to the reply file below.\n"
            "3. NEVER write just 'Done' or 'Task completed' — include the actual result/data.\n"
            "4. If the request is unclear, make your best interpretation and answer that.\n"
            "5. Focus only on the current request.\n\n"
            "Write your final reply (plain text, max 900 chars) to:\n"
            f"  {reply_file}\n\n"
            "Do NOT post directly to GroupMe; the daemon posts from this file."
        )
        prompt_file.write_text(prompt, encoding="utf-8")

        # Build the command
        ps_command = (
            f"$p = Get-Content -LiteralPath '{_powershell_literal(str(prompt_file))}' "
            f"-Raw -Encoding UTF8; "
            f"Set-Location -LiteralPath '{_powershell_literal(str(Path(ctx.working_directory).resolve()))}'; "
            f"& '{_powershell_literal(self._copilot_path)}' --yolo --autopilot -p $p"
        )

        in_session_zero = _is_session_zero()

        # Clean env — uv injects NODE_OPTIONS=--no-warnings which breaks copilot
        clean_env = {k: v for k, v in os.environ.items() if k != "NODE_OPTIONS"}
        stderr_file = None

        try:
            if in_session_zero:
                stderr_file = open(stderr_path, "wb")
                proc = subprocess.Popen(
                    ["powershell", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-NonInteractive", "-Command", ps_command],
                    cwd=ctx.working_directory,
                    env=clean_env,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_file,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                # Interactive launch — CREATE_NEW_CONSOLE gives copilot its own
                # visible window and proc.wait() blocks until it exits.
                stderr_file = open(stderr_path, "wb")
                proc = subprocess.Popen(
                    ["powershell", "-NoLogo", "-NoProfile",
                      "-ExecutionPolicy", "Bypass", "-Command", ps_command],
                    cwd=ctx.working_directory,
                    env=clean_env,
                    stderr=stderr_file,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )

            print(f"  [executor] PID={proc.pid} session0={in_session_zero}")

            proc.wait(timeout=3600)

            # Read reply
            reply_text = ""
            if reply_file.exists():
                reply_text = reply_file.read_text(encoding="utf-8").strip()
                reply_file.unlink(missing_ok=True)

            if not reply_text:
                error_tail = _read_tail(stderr_path)
                if proc.returncode == 0:
                    return ExecutionResult(
                        success=False,
                        reply_text=(
                            "Task failed: Copilot exited without writing a reply file. "
                            f"See {stderr_path.name}."
                        ),
                        exit_code=0,
                        error="missing reply file",
                    )
                detail = f" Exit {proc.returncode}."
                if error_tail:
                    condensed = " ".join(error_tail.split())
                    detail += f" {condensed[:700]}"
                return ExecutionResult(
                    success=False,
                    reply_text=f"Task failed:{detail}",
                    exit_code=proc.returncode or 1,
                    error=error_tail or "copilot failed without writing a reply file",
                )

            return ExecutionResult(
                success=proc.returncode == 0,
                reply_text=reply_text,
                exit_code=proc.returncode or 0,
            )

        except subprocess.TimeoutExpired:
            proc.kill()
            return ExecutionResult(
                success=False,
                reply_text="Task timed out after 1 hour.",
                exit_code=-1,
                error="timeout",
            )
        except Exception as exc:
            return ExecutionResult(
                success=False,
                error=str(exc),
                exit_code=1,
            )
        finally:
            prompt_file.unlink(missing_ok=True)
            if stderr_file is not None:
                try:
                    stderr_file.close()
                except Exception:
                    pass
