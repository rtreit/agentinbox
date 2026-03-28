---
applyTo: "**"
---

# GroupMe Agent Queue Instructions

- The agent inbox uses Azure Storage Queues to receive directed GroupMe messages.
- Process only payloads with schema `groupme-directed-message/v1` or `groupme-directed-message/v2`.
- Respect direction metadata:
  - `targetAgent` must match this daemon's configured agent name
  - `directedReason` indicates how the message was directed
  - `sender.type` must be `user`
- The v2 schema includes `source.replyBotId` for multi-chat reply routing.

## Execution Contract

- Do not claim a task is complete unless it was actually executed and verified.
- If a task cannot be executed, respond with a clear failure reason.
- For long-running tasks, send state updates: accepted, running, completed or failed.
- Leave queue messages available for retry when execution fails.

## High Risk Task Handling

- Treat shutdown, reboot, process termination, and destructive changes as high risk.
- Require explicit confirmation before executing high risk tasks.
