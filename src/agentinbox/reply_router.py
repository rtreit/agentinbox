"""Reply delivery for GroupMe and site webhook transports."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .config import Config
from .notify import post as groupme_post

SITE_REPLY_SCHEMA = "agentinbox-site-reply/v1"
SITE_REPLY_TOKEN_HEADER = "X-AgentInbox-Site-Token"


def _post_site_reply(
    reply_url: str,
    auth_token: str | None,
    payload: dict,
) -> bool:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers[SITE_REPLY_TOKEN_HEADER] = auth_token

    req = urllib.request.Request(
        reply_url,
        data=data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201, 202, 204)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"warning: site reply post failed: {exc}", file=sys.stderr)
        return False


def _build_site_payload(
    directive: dict,
    config: Config,
    status: str,
    text: str | None = None,
    success: bool | None = None,
) -> dict:
    payload = {
        "schema": SITE_REPLY_SCHEMA,
        "postedAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceProvider": directive.get("source_provider", "groupme"),
        "siteName": directive.get("site_name") or "",
        "threadId": directive.get("thread_id") or "",
        "messageId": directive.get("message_id") or "",
        "targetAgent": directive.get("target_agent") or config.agent_name,
        "senderName": directive.get("sender_name") or "",
        "senderId": directive.get("sender_id") or "",
        "status": status,
        "success": success if success is not None else status != "failed",
    }
    if text:
        payload["text"] = text
    return payload


def post_directive_event(
    directive: dict,
    config: Config,
    status: str,
    text: str | None = None,
    success: bool | None = None,
) -> bool:
    """Send an event update for a directive using its configured reply transport."""
    reply_url = str(directive.get("reply_webhook_url") or "").strip()
    if reply_url:
        payload = _build_site_payload(directive, config, status, text=text, success=success)
        auth_token = str(directive.get("reply_auth_token") or "").strip() or None
        return _post_site_reply(reply_url, auth_token, payload)

    bot_id = config.bot_id_for_chat(directive.get("group_id")) or directive.get("reply_bot_id")
    if status == "accepted":
        return groupme_post("🫡", bot_id=bot_id)

    if not text:
        return True

    return groupme_post(text, bot_id=bot_id)
