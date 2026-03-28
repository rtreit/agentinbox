"""Generic shell command executor.

Runs an arbitrary command with the instruction passed as an argument
or via stdin. Captures stdout/stderr as the reply text.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..executor import ExecutionContext, ExecutionResult, Executor


class CommandExecutor(Executor):
    """Execute instructions by running a shell command."""

    def __init__(self, command: str):
        """
        Args:
            command: The command template. Use {instruction} as a placeholder
                     for the instruction text, or omit it to pipe via stdin.
        """
        self._command = command

    def name(self) -> str:
        return f"CommandExecutor({self._command})"

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        if not self._command:
            return ExecutionResult(
                success=False,
                error="No command configured for CommandExecutor",
                exit_code=1,
            )

        # Substitute {instruction} placeholder if present
        if "{instruction}" in self._command:
            cmd = self._command.replace("{instruction}", ctx.instruction)
            stdin_input = None
        else:
            cmd = self._command
            stdin_input = ctx.instruction

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=ctx.working_directory,
                input=stdin_input,
                capture_output=True,
                text=True,
                timeout=3600,
                env={**dict(__import__("os").environ), **ctx.env},
            )

            reply = result.stdout.strip()
            if result.stderr.strip():
                reply += f"\n\nstderr:\n{result.stderr.strip()}"

            if not reply:
                reply = f"Command exited with code {result.returncode}"

            return ExecutionResult(
                success=result.returncode == 0,
                reply_text=reply[:1000],
                exit_code=result.returncode,
            )

        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                reply_text="Command timed out after 1 hour.",
                exit_code=-1,
                error="timeout",
            )
        except Exception as exc:
            return ExecutionResult(
                success=False,
                error=str(exc),
                exit_code=1,
            )
