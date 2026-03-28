"""Python script executor.

Runs a Python script with the instruction piped via stdin.
Captures stdout as the reply text.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..executor import ExecutionContext, ExecutionResult, Executor


class PythonScriptExecutor(Executor):
    """Execute instructions by running a Python script."""

    def __init__(self, script_path: str, python_path: str | None = None):
        self._script_path = script_path
        self._python_path = python_path or sys.executable

    def name(self) -> str:
        return f"PythonScriptExecutor({self._script_path})"

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        if not Path(self._script_path).exists():
            return ExecutionResult(
                success=False,
                error=f"Script not found: {self._script_path}",
                exit_code=1,
            )

        try:
            result = subprocess.run(
                [self._python_path, self._script_path],
                input=ctx.instruction,
                capture_output=True,
                text=True,
                cwd=ctx.working_directory,
                timeout=3600,
                env={**dict(__import__("os").environ), **ctx.env},
            )

            reply = result.stdout.strip()
            if result.stderr.strip():
                reply += f"\n\nstderr:\n{result.stderr.strip()}"

            if not reply:
                reply = f"Script exited with code {result.returncode}"

            return ExecutionResult(
                success=result.returncode == 0,
                reply_text=reply[:1000],
                exit_code=result.returncode,
            )

        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                reply_text="Script timed out after 1 hour.",
                exit_code=-1,
                error="timeout",
            )
        except Exception as exc:
            return ExecutionResult(
                success=False,
                error=str(exc),
                exit_code=1,
            )
