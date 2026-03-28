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
    """Find the GitHub Copilot CLI executable."""
    winget_copilot = Path(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft",
        "WinGet",
        "Links",
        "copilot.exe",
    )
    if winget_copilot.is_file():
        return str(winget_copilot)

    found = shutil.which("copilot")
    if found and "WindowsApps" not in found:
        return found

    winget_packages = Path(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft",
        "WinGet",
        "Packages",
    )
    if winget_packages.is_dir():
        for candidate in winget_packages.glob("GitHub.Copilot_*/copilot.exe"):
            return str(candidate)

    fallback = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "copilot-cli" / "copilot.exe"
    if fallback.exists():
        return str(fallback)

    return found


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


def _read_exit_code(path: Path) -> int | None:
    """Read a numeric exit code file if it exists."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip().lstrip("\ufeff")
        return int(text)
    except (OSError, ValueError):
        return None


def _build_clean_env() -> dict[str, str]:
    """Remove shell/tooling env vars that can perturb nested Copilot launches."""
    clean: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper == "NODE_OPTIONS":
            continue
        if upper.startswith("NPM_CONFIG_") or upper.startswith("NPM_PACKAGE_"):
            continue
        if upper.startswith("YARN_") or upper.startswith("PNPM_"):
            continue
        if upper.startswith("COPILOT_"):
            continue
        if "--NO-WARNINGS" in value.upper() and (
            "NODE" in upper or upper.startswith(("NPM_", "YARN_", "PNPM_"))
        ):
            continue
        clean[key] = value

    if "HOME" not in clean and clean.get("USERPROFILE"):
        clean["HOME"] = clean["USERPROFILE"]
    return clean


class CopilotExecutor(Executor):
    """Execute instructions via the Copilot CLI."""

    def __init__(self, copilot_path: str | None = None):
        self._copilot_path = copilot_path or _find_copilot()

    def name(self) -> str:
        return "CopilotExecutor"

    @property
    def resolved_path(self) -> str | None:
        return self._copilot_path

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
        status_path = log_dir / f"copilot_exit_{safe_id}.txt"
        stderr_path.unlink(missing_ok=True)
        status_path.unlink(missing_ok=True)

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

        in_session_zero = _is_session_zero()

        copilot_invoke = f"& '{_powershell_literal(self._copilot_path)}' --yolo --autopilot -p $p"
        if in_session_zero:
            copilot_invoke += " 2> $stderrPath"

        # Build the command
        ps_command = (
            "$ErrorActionPreference = 'Stop'; "
            f"$statusPath = '{_powershell_literal(str(status_path))}'; "
            f"$stderrPath = '{_powershell_literal(str(stderr_path))}'; "
            "$code = 1; "
            "try { "
            f"$p = Get-Content -LiteralPath '{_powershell_literal(str(prompt_file))}' "
            "-Raw -Encoding UTF8; "
            f"Set-Location -LiteralPath '{_powershell_literal(str(Path(ctx.working_directory).resolve()))}'; "
            f"{copilot_invoke}; "
            "$code = $LASTEXITCODE; "
            "} catch { "
            "$_ | Out-File -LiteralPath $stderrPath -Append -Encoding utf8; "
            "$code = 1; "
            "} finally { "
            "Set-Content -LiteralPath $statusPath -Value $code -Encoding Ascii; "
            "}; "
            "exit $code"
        )

        clean_env = _build_clean_env()

        try:
            if in_session_zero:
                proc = subprocess.Popen(
                    ["powershell", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-NonInteractive", "-Command", ps_command],
                    cwd=ctx.working_directory,
                    env=clean_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                print(f"  [executor] PID={proc.pid} session0={in_session_zero} host=headless")
                proc.wait(timeout=3600)
                exit_code = _read_exit_code(status_path)
                if exit_code is None:
                    exit_code = proc.returncode or 1
            else:
                # Interactive launch — explicitly use conhost so the Copilot
                # session is not hosted inside Windows Terminal during stress runs.
                proc = subprocess.Popen(
                    ["conhost.exe", "powershell", "-NoLogo", "-NoProfile",
                     "-ExecutionPolicy", "Bypass", "-Command", ps_command],
                    cwd=ctx.working_directory,
                    env=clean_env,
                )
                print(f"  [executor] PID={proc.pid} session0={in_session_zero} host=conhost")
                deadline = time.time() + 3600
                exit_code = None
                while time.time() < deadline:
                    exit_code = _read_exit_code(status_path)
                    if exit_code is not None:
                        break
                    if reply_file.exists() and reply_file.stat().st_size > 0:
                        exit_code = 0
                        break
                    time.sleep(1)
                if exit_code is None:
                    if proc.poll() is None:
                        proc.kill()
                    return ExecutionResult(
                        success=False,
                        reply_text="Task timed out after 1 hour.",
                        exit_code=-1,
                        error="timeout",
                    )
                if proc.poll() is None:
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass

            # Read reply
            reply_text = ""
            if reply_file.exists():
                reply_text = reply_file.read_text(encoding="utf-8").strip()
                reply_file.unlink(missing_ok=True)

            if not reply_text:
                error_tail = _read_tail(stderr_path)
                if exit_code == 0:
                    return ExecutionResult(
                        success=False,
                        reply_text=(
                            "Task failed: Copilot exited without writing a reply file. "
                            f"See {stderr_path.name}."
                        ),
                        exit_code=0,
                        error="missing reply file",
                    )
                detail = f" Exit {exit_code}."
                if error_tail:
                    condensed = " ".join(error_tail.split())
                    detail += f" {condensed[:700]}"
                return ExecutionResult(
                    success=False,
                    reply_text=f"Task failed:{detail}",
                    exit_code=exit_code or 1,
                    error=error_tail or "copilot failed without writing a reply file",
                )

            return ExecutionResult(
                success=exit_code == 0,
                reply_text=reply_text,
                exit_code=exit_code or 0,
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
            status_path.unlink(missing_ok=True)
