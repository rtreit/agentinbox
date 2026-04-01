"""Azure Storage Queue consumer for GroupMe-directed messages.

Polls the configured queue, validates messages against the
groupme-directed-message schema (v1/v2), strips directed prefixes,
acknowledges receipt in GroupMe, and returns normalized directives.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

# In Session 0 (Windows service), stdout/stderr default to cp1252 which can't
# encode emoji (e.g. 🤦‍♂️).  Replace un-encodable chars instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass

from azure.storage.queue import QueueClient

from .config import Config
from .reply_router import post_directive_event

MAX_PERSONA_INSTRUCTIONS = 1200


def _get_queue_client(config: Config) -> QueueClient:
    """Create a QueueClient from config."""
    conn_str = config.connection_string
    if not conn_str:
        print(
            f"error: {config.connection_string_env} not set.",
            file=sys.stderr,
        )
        sys.exit(1)
    return QueueClient.from_connection_string(conn_str, config.resolved_queue_name)


def _parse_queue_message(raw: str) -> dict | None:
    """Parse a queue message body (plain JSON or base64-encoded JSON)."""
    # Try plain JSON first
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try base64 decoding
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        pass

    return None


def _validate_message(parsed: dict, config: Config) -> str | None:
    """Validate a parsed queue message. Returns error string or None if valid."""
    schema = parsed.get("schema", "")
    if schema not in ("groupme-directed-message/v1", "groupme-directed-message/v2"):
        return f"unknown schema: {schema}"

    target = parsed.get("targetAgent", "")
    if target and target.lower() != config.agent_name.lower():
        return f"wrong target: {target} (I am {config.agent_name})"

    text = parsed.get("message", {}).get("text", "")
    if not text.strip():
        return "empty text"

    return None


def _extract_instruction(text: str, agent_name: str) -> str:
    """Strip directed prefixes from the message text."""
    stripped = text.strip()

    # Order matters: longest/most-specific prefixes first
    prefixes = [
        "@@!",
        "@@",
        "🤖",
        f"@{agent_name}",
        f"{agent_name}:",
        f"/{agent_name}",
        f"!{agent_name}",
    ]

    lower = stripped.lower()
    for prefix in prefixes:
        if lower.startswith(prefix.lower()):
            stripped = stripped[len(prefix):].strip()
            break

    return stripped


def _normalize_persona(raw: object, target_agent: str) -> dict | None:
    """Normalize optional persona metadata from the queue envelope."""
    if isinstance(raw, str):
        raw = {"instructions": raw}

    if not isinstance(raw, dict):
        return None

    instructions = raw.get("instructions")
    if not isinstance(instructions, str):
        return None

    instructions = instructions.strip()
    if not instructions:
        return None

    persona_id = raw.get("id")
    if not isinstance(persona_id, str) or not persona_id.strip():
        persona_id = target_agent

    version = raw.get("version")
    if isinstance(version, (int, float)):
        version = str(version)
    elif not isinstance(version, str):
        version = ""

    return {
        "id": persona_id.strip(),
        "version": version.strip(),
        "instructions": instructions[:MAX_PERSONA_INSTRUCTIONS],
    }


def peek_messages(config: Config, max_messages: int = 5) -> list[dict]:
    """Peek at queue messages without consuming them."""
    client = _get_queue_client(config)
    messages = []
    for msg in client.peek_messages(max_messages=max_messages):
        parsed = _parse_queue_message(msg.content)
        if parsed:
            messages.append({
                "id": msg.id,
                "parsed": parsed,
                "raw": msg.content[:200],
            })
        else:
            messages.append({
                "id": msg.id,
                "parsed": None,
                "raw": msg.content[:200],
            })
    return messages


def process_one(config: Config, seen_ids: set[str] | None = None) -> dict | None:
    """Receive and process one queue message.

    Returns a directive dict or None if the queue is empty.
    """
    client = _get_queue_client(config)

    # Receive one message with 60s visibility timeout
    batch = client.receive_messages(messages_per_page=1, visibility_timeout=60)
    msg = next(iter(batch), None)
    if msg is None:
        return None

    parsed = _parse_queue_message(msg.content)
    if parsed is None:
        print(f"  warning: unparseable message {msg.id}, skipping")
        client.delete_message(msg)
        return None

    error = _validate_message(parsed, config)
    if error:
        print(f"  skip: {error} (msg {msg.id})")
        client.delete_message(msg)
        return None

    sender = parsed.get("sender", {})
    sender_id = str(sender.get("id") or "").strip()
    sender_name = sender.get("name", "Unknown")
    message_id = parsed.get("source", {}).get("messageId", msg.id)
    target_agent = parsed.get("targetAgent", config.agent_name)
    text = parsed.get("message", {}).get("text", "")
    instruction = _extract_instruction(text, target_agent)
    persona = _normalize_persona(parsed.get("persona"), target_agent)

    # Extract reply routing info (v2 schema)
    source = parsed.get("source", {})
    source_provider = str(source.get("provider") or "groupme").strip().lower()
    site_name = str(source.get("siteName") or "").strip()
    thread_id = str(source.get("threadId") or "").strip()
    group_id = source.get("groupId")
    reply_bot_id = source.get("replyBotId")
    reply_webhook_url = str(source.get("replyWebhookUrl") or "").strip()
    reply_auth_token = str(source.get("replyAuthToken") or "").strip()

    if seen_ids is not None and message_id in seen_ids:
        print(f"  skip duplicate before ack: {message_id}")
        client.delete_message(msg)
        return {"_duplicate": True, "message_id": message_id}

    print(f"  [{sender_name}] {instruction}")

    directive = {
        "instruction": instruction,
        "sender_name": sender_name,
        "sender_id": sender_id,
        "message_id": message_id,
        "raw_text": text,
        "group_id": group_id,
        "reply_bot_id": reply_bot_id,
        "reply_webhook_url": reply_webhook_url,
        "reply_auth_token": reply_auth_token,
        "source_provider": source_provider,
        "site_name": site_name,
        "thread_id": thread_id,
        "target_agent": target_agent,
        "persona": persona,
    }

    # Acknowledge receipt
    if not post_directive_event(directive, config, status="accepted"):
        print(f"  warning: failed to send acceptance update for {message_id}")

    # Delete from queue — we've accepted responsibility
    client.delete_message(msg)

    return directive


def get_all_directives(config: Config, pre_seen_ids: set[str] | None = None) -> list[dict]:
    """Drain all pending directives from the queue, deduplicating by message_id."""
    directives = []
    seen_ids: set[str] = set(pre_seen_ids or set())
    while True:
        directive = process_one(config, seen_ids=seen_ids)
        if directive is None:
            break
        if directive.get("_duplicate"):
            continue
        mid = directive.get("message_id", "")
        if mid:
            seen_ids.add(mid)
        directives.append(directive)
    return directives


def poll_loop(config: Config, callback=None) -> None:
    """Continuously poll the queue and process directives."""
    interval = config.poll_interval
    print(f"Polling {config.resolved_queue_name} every {interval}s (Ctrl+C to stop)...")

    try:
        while True:
            directives = get_all_directives(config)
            if directives:
                print(f"  processed {len(directives)} directive(s)")
                if callback:
                    for d in directives:
                        callback(d)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")
