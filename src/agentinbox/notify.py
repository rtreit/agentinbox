"""GroupMe bot notification module.

Supports multi-chat reply routing by accepting an optional bot_id
parameter. When not specified, falls back to GROUPME_BOT_ID from
the environment or .env file.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path


GROUPME_API_URL = "https://api.groupme.com/v3/bots/post"


def _load_bot_id() -> str | None:
    """Load the default bot ID from env or .env file."""
    bot_id = os.environ.get("GROUPME_BOT_ID")
    if bot_id:
        return bot_id

    # Walk up to find .env
    path = Path.cwd()
    for _ in range(5):
        env_file = path / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GROUPME_BOT_ID="):
                    return line.split("=", 1)[1].strip().strip("'\"")
        parent = path.parent
        if parent == path:
            break
        path = parent

    return None


def post(text: str, bot_id: str | None = None, picture_url: str | None = None) -> bool:
    """Post a message to GroupMe.

    Args:
        text: Message text (truncated to 1000 chars if needed).
        bot_id: GroupMe bot ID. If None, uses GROUPME_BOT_ID from env.
        picture_url: Optional image URL to attach.

    Returns:
        True if posted successfully, False otherwise.
    """
    resolved_bot_id = bot_id or _load_bot_id()
    if not resolved_bot_id:
        print("warning: no GroupMe bot ID configured", file=sys.stderr)
        return False

    if len(text) > 1000:
        text = text[:997] + "..."

    payload: dict = {"bot_id": resolved_bot_id, "text": text}
    if picture_url:
        payload["picture_url"] = picture_url

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        GROUPME_API_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201, 202)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"warning: GroupMe post failed: {exc}", file=sys.stderr)
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m agentinbox.notify 'message text'")
        sys.exit(1)
    msg = " ".join(sys.argv[1:])
    ok = post(msg)
    sys.exit(0 if ok else 1)
