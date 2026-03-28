"""Configuration loading for Agent Inbox.

Priority order:
  1. CLI arguments (highest)
  2. Environment variables (AGENTINBOX_*)
  3. agentinbox.toml in working directory
  4. .env file in working directory
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from dotenv import load_dotenv


@dataclass
class Config:
    """Daemon configuration."""

    # Agent identity
    agent_name: str = "hal"

    # Queue settings
    queue_name: str = ""  # derived from agent_name if empty
    connection_string_env: str = "STORAGE_CONNECTION_STRING"
    poll_interval: float = 10.0

    # Executor settings
    executor_type: str = "copilot"  # copilot | command | python
    executor_command: str = ""
    working_directory: str = "."

    # GroupMe settings
    bot_id_env: str = "GROUPME_BOT_ID"
    chat_bots: dict[str, str] = field(default_factory=dict)

    # Logging
    log_directory: str = "logs"

    @property
    def resolved_queue_name(self) -> str:
        return self.queue_name or f"agentinbox-{self.agent_name}"

    @property
    def resolved_log_directory(self) -> Path:
        return Path(self.working_directory) / self.log_directory

    @property
    def connection_string(self) -> str | None:
        return os.environ.get(self.connection_string_env)

    @property
    def default_bot_id(self) -> str | None:
        return os.environ.get(self.bot_id_env)

    def bot_id_for_chat(self, group_id: str | None) -> str | None:
        """Return the bot ID for a specific chat, or the default."""
        if group_id and group_id in self.chat_bots:
            env_var = self.chat_bots[group_id]
            return os.environ.get(env_var, self.default_bot_id)
        return self.default_bot_id


def _load_toml(path: Path) -> dict:
    """Load agentinbox.toml if it exists."""
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _apply_toml(cfg: Config, data: dict) -> None:
    """Apply TOML config values to a Config object."""
    agent = data.get("agent", {})
    if "name" in agent:
        cfg.agent_name = agent["name"]

    queue = data.get("queue", {})
    if "name" in queue:
        cfg.queue_name = queue["name"]
    if "connection_string_env" in queue:
        cfg.connection_string_env = queue["connection_string_env"]
    if "poll_interval" in queue:
        cfg.poll_interval = float(queue["poll_interval"])

    executor = data.get("executor", {})
    if "type" in executor:
        cfg.executor_type = executor["type"]
    if "command" in executor:
        cfg.executor_command = executor["command"]
    if "working_directory" in executor:
        cfg.working_directory = executor["working_directory"]

    groupme = data.get("groupme", {})
    if "bot_id_env" in groupme:
        cfg.bot_id_env = groupme["bot_id_env"]
    if "chat_bots" in groupme:
        cfg.chat_bots = dict(groupme["chat_bots"])

    logging = data.get("logging", {})
    if "directory" in logging:
        cfg.log_directory = logging["directory"]


def _apply_env(cfg: Config) -> None:
    """Apply AGENTINBOX_* environment variable overrides."""
    env_map = {
        "AGENTINBOX_AGENT_NAME": "agent_name",
        "AGENTINBOX_QUEUE_NAME": "queue_name",
        "AGENTINBOX_POLL_INTERVAL": "poll_interval",
        "AGENTINBOX_EXECUTOR_TYPE": "executor_type",
        "AGENTINBOX_EXECUTOR_COMMAND": "executor_command",
        "AGENTINBOX_LOG_DIR": "log_directory",
    }
    for env_key, attr in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            if attr == "poll_interval":
                setattr(cfg, attr, float(val))
            else:
                setattr(cfg, attr, val)


def _apply_cli(cfg: Config, args: argparse.Namespace) -> None:
    """Apply CLI argument overrides."""
    if hasattr(args, "agent_name") and args.agent_name:
        cfg.agent_name = args.agent_name
    if hasattr(args, "queue_name") and args.queue_name:
        cfg.queue_name = args.queue_name
    if hasattr(args, "interval") and args.interval is not None:
        cfg.poll_interval = args.interval
    if hasattr(args, "executor") and args.executor:
        cfg.executor_type = args.executor
    if hasattr(args, "executor_command") and args.executor_command:
        cfg.executor_command = args.executor_command
    if hasattr(args, "config") and args.config:
        pass  # already loaded


def load_config(args: argparse.Namespace | None = None) -> Config:
    """Load configuration from all sources."""
    cfg = Config()

    # Determine working directory
    work_dir = "."
    if args and hasattr(args, "config") and args.config:
        work_dir = str(Path(args.config).parent)

    # Load .env
    env_path = Path(work_dir) / ".env"
    load_dotenv(env_path, override=False)

    # Load TOML
    toml_path = Path(work_dir) / "agentinbox.toml"
    if args and hasattr(args, "config") and args.config:
        toml_path = Path(args.config)
    toml_data = _load_toml(toml_path)
    _apply_toml(cfg, toml_data)

    # Apply env overrides
    _apply_env(cfg)

    # Apply CLI overrides
    if args:
        _apply_cli(cfg, args)

    return cfg
