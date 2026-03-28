---
applyTo: "**"
---

# Security Instructions

## Secrets Management

- Store secrets in `.env` files (gitignored) or environment variables.
- Never hardcode API keys, connection strings, bot IDs, or tokens in source code.
- The `.env.example` file documents required variables without real values.

## Azure Storage Queue

- Use `STORAGE_CONNECTION_STRING` for queue access, never embed in code.
- Validate queue message schema before processing.
- Delete messages only after successful handling.

## GroupMe API

- Bot IDs are read from environment variables (`GROUPME_BOT_ID`, per-chat variants).
- Callback tokens authenticate incoming webhooks (`GROUPME_CALLBACK_TOKEN`).
- Never log bot IDs or callback tokens.

## Executor Safety

- Executors run arbitrary commands. Treat all incoming instructions as untrusted input.
- The daemon should not execute system-destructive commands without explicit safeguards.
- Log all dispatched instructions for audit trail.
