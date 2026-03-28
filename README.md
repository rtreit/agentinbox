# Agent Inbox

A multi-agent inbox system that routes GroupMe messages through Azure Storage Queues to configurable executors. Run multiple daemons on different PCs, each listening to their own queue, all controllable from the same GroupMe chat.

## Architecture

```
GroupMe Chat ──► Azure Function (webhook) ──► Azure Storage Queue(s)
                   │                              │
                   │ Routes by:                   │ One queue per agent:
                   │  • @agentname → agent queue  │  agentinbox-hal
                   │  • @@ → chat default agent   │  agentinbox-buildbot
                   │  • Chat → default agent       │  ...
                   │                              │
                   ▼                              ▼
              Reply via                     Daemon (per agent)
              replyBotId                        │
                   ▲                           │ Dispatches to executor:
                   │                           │  • Copilot CLI
                   └───────── GroupMe ◄────────│  • Shell command
                              reply             │  • Python script
```

### Message Flow

1. User posts in GroupMe with a directed prefix (`@hal`, `@@`, `🤖`, etc.)
2. Azure Function receives the webhook, determines target agent and queue
3. Message is enqueued with v2 schema (includes `replyBotId` for multi-chat routing)
4. Daemon polls its queue, extracts the instruction, dispatches to executor
5. Executor runs the task and returns a reply
6. Daemon posts the reply back to the correct GroupMe chat

## Quick Start

```powershell
# Clone and install
cd C:\Users\randy\Git\agentinbox
uv sync

# Configure
copy .env.example .env
# Edit .env with your STORAGE_CONNECTION_STRING and GROUPME_BOT_ID

# Run one-shot (process pending directives and exit)
python -m agentinbox

# Run persistent daemon
python -m agentinbox daemon

# Peek at queue without consuming
python -m agentinbox peek
```

## Configuration

Configuration is loaded in priority order: CLI args > env vars > `agentinbox.toml` > `.env`

### agentinbox.toml

```toml
[agent]
name = "hal"

[queue]
name = "agentinbox-hal"
connection_string_env = "STORAGE_CONNECTION_STRING"
poll_interval = 10

[executor]
type = "copilot"       # copilot | command | python
command = ""           # for command/python types
working_directory = "."

[groupme]
bot_id_env = "GROUPME_BOT_ID"

# Per-chat bot mapping (group_id -> env var with bot_id)
[groupme.chat_bots]
"12345678" = "GROUPME_BOT_ID_CHAT1"
"87654321" = "GROUPME_BOT_ID_CHAT2"

[logging]
directory = "logs"
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `STORAGE_CONNECTION_STRING` | Azure Storage connection string |
| `GROUPME_BOT_ID` | Default GroupMe bot ID for replies |
| `AGENTINBOX_AGENT_NAME` | Agent name (default: `hal`) |
| `AGENTINBOX_QUEUE_NAME` | Queue name (default: `agentinbox-{agent}`) |
| `AGENTINBOX_POLL_INTERVAL` | Poll interval in seconds |
| `AGENTINBOX_EXECUTOR_TYPE` | Executor type: `copilot`, `command`, `python` |
| `AGENTINBOX_EXECUTOR_COMMAND` | Command for command/python executors |
| `AGENTINBOX_LOG_DIR` | Log directory path |

### CLI Arguments

```
python -m agentinbox daemon \
  --agent-name hal \
  --queue-name agentinbox-hal \
  --interval 10 \
  --executor copilot \
  --executor-command "" \
  --config agentinbox.toml
```

## Multi-Agent Setup

Run multiple daemons on different machines, each listening to a different queue:

**PC-A (hal):**
```powershell
python -m agentinbox daemon --agent-name hal
# Listens to queue: agentinbox-hal
```

**PC-B (buildbot):**
```powershell
python -m agentinbox daemon --agent-name buildbot --executor command --executor-command "make {instruction}"
# Listens to queue: agentinbox-buildbot
```

**In GroupMe:**
- `@hal run the tests` → routed to PC-A
- `@buildbot deploy staging` → routed to PC-B
- `@@ check status` → routed to the chat's default agent

## Directed Message Prefixes

| Prefix | Behavior |
|--------|----------|
| `@hal <cmd>` | Routes to hal's queue |
| `@buildbot <cmd>` | Routes to buildbot's queue |
| `@@ <cmd>` | Routes to chat's default agent |
| `🤖 <cmd>` | Same as `@@` |
| `hal: <cmd>` | Routes to hal's queue |
| `/hal <cmd>` | Routes to hal's queue |
| `!hal <cmd>` | Routes to hal's queue |

## Executors

### Copilot CLI (default)
Launches `copilot -p <prompt> --yolo --autopilot`. Handles Session 0 (Windows service) by skipping conhost.

### Shell Command
Runs an arbitrary command. Use `{instruction}` as placeholder:
```toml
[executor]
type = "command"
command = "python my_handler.py {instruction}"
```

### Python Script
Pipes the instruction to a Python script via stdin:
```toml
[executor]
type = "python"
command = "scripts/handler.py"
```

## Windows Service

The C# service (`service/`) auto-starts the daemon on boot with crash recovery.

```powershell
# Build
cd service
dotnet publish -c Release

# Install (requires admin)
.\bin\Release\net8.0\win-x64\publish\AgentInboxService.exe install

# Manage
AgentInboxService start
AgentInboxService stop
AgentInboxService status
AgentInboxService config show
AgentInboxService config set pythonPath ".venv\Scripts\python.exe"
```

The service automatically:
- Restarts the daemon on crash (with exponential backoff)
- Loads `.env` from the working directory
- Sets up user profile env vars for Session 0 (Copilot auth)
- Streams stdout/stderr to `logs/service_*.log`

## Azure Function

The webhook function (`azure-function/`) receives GroupMe callbacks and routes to queues.

### Deployment

```powershell
cd azure-function
npm install
# Deploy to Azure Functions (via Azure CLI, VS Code, or GitHub Actions)
func azure functionapp publish <app-name>
```

### Required App Settings

| Setting | Description |
|---------|-------------|
| `STORAGE_CONNECTION_STRING` | Azure Storage connection string |
| `GROUPME_CALLBACK_TOKEN` | Webhook auth token |
| `AGENTINBOX_AGENTS` | Comma-separated agent names (e.g., `hal,buildbot`) |
| `AGENTINBOX_DEFAULT_AGENT` | Default agent (e.g., `hal`) |
| `AGENTINBOX_QUEUE_PREFIX` | Queue name prefix (default: `agentinbox-`) |
| `AGENTINBOX_CHAT_ROUTES` | JSON: group_id → agent name |
| `AGENTINBOX_BOT_MAP` | JSON: group_id → bot_id for replies |

### Queue Provisioning

```powershell
.\tools\Setup-Queue.ps1 -Agents "hal,buildbot" -ConnectionString $env:STORAGE_CONNECTION_STRING
```

## Message Schema

### v2 (current)

```json
{
  "schema": "groupme-directed-message/v2",
  "queuedAtUtc": "2026-03-27T12:00:00.000Z",
  "targetAgent": "hal",
  "targetQueue": "agentinbox-hal",
  "directedReason": "prefix",
  "source": {
    "provider": "groupme",
    "messageId": "abc123",
    "groupId": "12345678",
    "createdAtEpoch": 1234567890,
    "replyBotId": "bot_id_for_this_chat"
  },
  "sender": {
    "id": "sender123",
    "name": "Randy",
    "type": "user"
  },
  "message": {
    "text": "run the tests",
    "attachments": []
  }
}
```

### v1 (backward compatible)

Same as v2 but without `targetQueue` and `source.replyBotId`. Daemons accept both.

## Project Structure

```
agentinbox/
├── .github/                  # Copilot instructions, agents, hooks
├── .vscode/                  # MCP server config
├── src/agentinbox/           # Python package
│   ├── __main__.py           # CLI entry point
│   ├── config.py             # Configuration loading
│   ├── daemon.py             # Main daemon loop
│   ├── inbox.py              # Azure Queue consumer
│   ├── notify.py             # GroupMe poster
│   ├── task_tracker.py       # In-flight task state
│   ├── executor.py           # Executor interface
│   └── executors/            # Executor implementations
│       ├── copilot.py        # Copilot CLI
│       ├── command.py        # Shell command
│       └── python_script.py  # Python script
├── azure-function/           # Azure Function webhook
├── service/                  # C# Windows service
├── tools/                    # PowerShell helpers
├── pyproject.toml            # uv project config
└── agentinbox.toml           # Runtime config (optional)
```

## License

MIT
