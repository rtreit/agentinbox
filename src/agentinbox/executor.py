"""Pluggable executor interface.

Executors receive an instruction (natural-language directive from GroupMe)
and produce a result (reply text to post back).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionContext:
    """Context passed to an executor."""

    instruction: str
    sender_name: str
    message_id: str
    working_directory: str = "."
    raw_text: str = ""
    persona_id: str = ""
    persona_version: str = ""
    persona_instructions: str = ""
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """Result of an executor run."""

    success: bool
    reply_text: str = ""
    exit_code: int = 0
    error: str = ""


class Executor:
    """Base class for executors."""

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        """Execute the instruction and return a result."""
        raise NotImplementedError

    def name(self) -> str:
        return self.__class__.__name__
