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

## Prerequisites

- **Python 3.13+** with [uv](https://docs.astral.sh/uv/) package manager
- **Azure CLI** (`az`) — [Install](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)
- **Azure Functions Core Tools** (`func`) — [Install](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local)
- **.NET 8 SDK** — for the Windows service (optional)
- **Azure subscription** — for Storage Account and Function App
- **GroupMe account** — with a bot created at [dev.groupme.com](https://dev.groupme.com)

## Quick Start

```powershell
# Clone and install
git clone https://github.com/rtreit/agentinbox.git
cd agentinbox
uv sync

# Configure
copy .env.example .env
# Edit .env with your STORAGE_CONNECTION_STRING and GROUPME_BOT_ID

# Run one-shot (process pending directives and exit)
uv run python -m agentinbox

# Run persistent daemon
uv run python -m agentinbox daemon

# Peek at queue without consuming
uv run python -m agentinbox peek
```

## Full Deployment Guide

This walks through deploying Agent Inbox from scratch on a new machine.

### Step 1: Create Azure Resources

```powershell
# Login to Azure
az login

# Create a resource group
az group create --name agentinbox --location westus2

# Create a storage account (name must be globally unique, lowercase, no hyphens)
az storage account create `
  --name <yourstorageaccount> `
  --resource-group agentinbox `
  --location westus2 `
  --sku Standard_LRS `
  --kind StorageV2

# Get the connection string (save this — you'll need it for .env and Function app settings)
az storage account show-connection-string `
  --name <yourstorageaccount> `
  --resource-group agentinbox `
  --query connectionString -o tsv

# Create queue(s) for your agent(s)
az storage queue create --name agentinbox-hal --connection-string "<your-connection-string>"
# For additional agents:
# az storage queue create --name agentinbox-buildbot --connection-string "<your-connection-string>"
```

### Step 2: Create a GroupMe Bot

1. Go to [dev.groupme.com](https://dev.groupme.com) and log in
2. Click **Bots** → **Create Bot**
3. Choose the group chat where you want to receive commands
4. Name the bot (e.g., "hal")
5. Leave the **Callback URL** blank for now (we'll set it after deploying the Function)
6. Save the **Bot ID** — you'll need it for `.env`

> **Multi-chat:** If you want the bot in multiple chats, create one bot per chat and use `AGENTINBOX_BOT_MAP` to map `group_id → bot_id`.

### Step 3: Deploy the Azure Function

The webhook function receives GroupMe callbacks and routes messages to per-agent queues.

```powershell
# Create a Function App (consumption plan, Node.js 20)
az functionapp create `
  --name <your-function-app-name> `
  --resource-group agentinbox `
  --consumption-plan-location westus2 `
  --runtime node `
  --runtime-version 20 `
  --functions-version 4 `
  --storage-account <yourstorageaccount> `
  --os-type Windows

# Configure app settings
az functionapp config appsettings set `
  --name <your-function-app-name> `
  --resource-group agentinbox `
  --settings `
    "STORAGE_CONNECTION_STRING=<your-connection-string>" `
    "AGENTINBOX_AGENTS=hal" `
    "AGENTINBOX_DEFAULT_AGENT=hal" `
    "AGENTINBOX_QUEUE_PREFIX=agentinbox-"

# Deploy the function code
cd azure-function
npm install
func azure functionapp publish <your-function-app-name> --javascript
```

The function uses **function-level auth** — Azure requires a function key (`?code=<key>`) on every request. This is more secure than a simple shared token. Get your function key:

```powershell
az functionapp keys list `
  --name <your-function-app-name> `
  --resource-group agentinbox `
  --query "functionKeys.default" -o tsv
```

### Step 4: Update GroupMe Bot Callback URL

Go back to [dev.groupme.com](https://dev.groupme.com) → your bot → edit, and set the **Callback URL** to:

```
https://<your-function-app-name>.azurewebsites.net/api/groupme-callback?code=<your-function-key>
```

### Step 5: (Optional) Custom Domain with SSL

If you want a clean URL like `https://inbox.yourdomain.com/api/groupme-callback`:

```powershell
# 1. Get your domain verification ID
az functionapp show `
  --name <your-function-app-name> `
  --resource-group agentinbox `
  --query customDomainVerificationId -o tsv
```

Add two DNS records at your DNS provider (e.g., Cloudflare):

| Type | Name | Value |
|------|------|-------|
| TXT | `asuid.inbox` | `<verification-id-from-above>` |
| CNAME | `inbox` | `<your-function-app-name>.azurewebsites.net` |

> **Cloudflare users:** Set the CNAME to **DNS only** (grey cloud) during setup. Azure must verify the hostname directly. You can enable proxying later.

Wait a minute for DNS propagation, then:

```powershell
# 2. Add the custom hostname to Azure
az functionapp config hostname add `
  --hostname inbox.yourdomain.com `
  --name <your-function-app-name> `
  --resource-group agentinbox

# 3. Create a free managed SSL certificate
az webapp config ssl create `
  --hostname inbox.yourdomain.com `
  --name <your-function-app-name> `
  --resource-group agentinbox

# 4. Bind the certificate (get thumbprint from the ssl create output)
az webapp config ssl bind `
  --certificate-thumbprint <thumbprint> `
  --ssl-type SNI `
  --name <your-function-app-name> `
  --resource-group agentinbox

# 5. Enforce HTTPS
az functionapp update `
  --name <your-function-app-name> `
  --resource-group agentinbox `
  --set httpsOnly=true
```

Update your GroupMe bot callback URL to:
```
https://inbox.yourdomain.com/api/groupme-callback?code=<your-function-key>
```

### Step 6: Configure the Daemon

```powershell
cd agentinbox   # your local clone

# Create .env from the example
copy .env.example .env
```

Edit `.env`:
```env
STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
GROUPME_BOT_ID=<your-bot-id-from-step-2>
GH_TOKEN=<github-token-for-headless-copilot-auth>
AGENTINBOX_AGENT_NAME=hal
```

If you plan to run the daemon as a Windows service in Session 0, set `GH_TOKEN`
or `GITHUB_TOKEN` in `.env`. Mapping `HOME`/`.copilot` alone can still be
insufficient for nested headless `copilot.exe` auth.

Test connectivity:
```powershell
# Peek at the queue (should show empty or any test messages)
uv run python -m agentinbox peek

# Run a quick one-shot to verify configuration
uv run python -m agentinbox
```

### Step 7: Verify End-to-End

Send a test message to your GroupMe chat:
```
@@ hello from agentinbox
```

Then check if it arrived:
```powershell
uv run python -m agentinbox peek
```

You should see the message in the queue. To consume and process it:
```powershell
uv run python -m agentinbox daemon --dry-run
```

### Step 8: (Optional) Install as Windows Service

The C# service auto-starts the daemon on boot with crash recovery.

```powershell
# Build the service
cd service
dotnet publish -c Release -o publish

# Install (requires an elevated/admin terminal)
.\publish\AgentInboxService.exe install

# Start the service
sc start AgentInboxDaemon
# Or from the publish directory:
.\publish\AgentInboxService.exe start
```

Verify it's running:
```powershell
sc query AgentInboxDaemon
# STATE should be: RUNNING
```

> **Note:** The executable is at `service\publish\AgentInboxService.exe`. It's not on PATH by default — always use the full path or run from the `service\publish\` directory.

Service management:
```powershell
# Full path required unless publish/ is on PATH
.\service\publish\AgentInboxService.exe status
.\service\publish\AgentInboxService.exe config show
.\service\publish\AgentInboxService.exe stop
.\service\publish\AgentInboxService.exe uninstall
```

The `agentinbox-service.json` config file (next to the exe) controls:
```json
{
  "pythonPath": ".venv\\Scripts\\python.exe",
  "scriptPath": "-m agentinbox daemon",
  "workingDirectory": ".",
  "restartOnCrash": true,
  "restartDelaySeconds": 5,
  "maxRestarts": 10,
  "maxRestartWindowMinutes": 30,
  "logDirectory": "logs"
}
```

The service automatically:
- Restarts the daemon on crash (up to `maxRestarts` within `maxRestartWindowMinutes`)
- Loads `.env` from the working directory
- Sets up user profile env vars for Session 0 (Copilot auth)
- Streams stdout/stderr to `logs/service_*.log`

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

### Azure Function App Settings

| Setting | Description |
|---------|-------------|
| `STORAGE_CONNECTION_STRING` | Azure Storage connection string |
| `AGENTINBOX_AGENTS` | Comma-separated agent names (e.g., `hal,buildbot`) |
| `AGENTINBOX_DEFAULT_AGENT` | Default agent for `@@` / `🤖` prefixes (e.g., `hal`) |
| `AGENTINBOX_QUEUE_PREFIX` | Queue name prefix (default: `agentinbox-`) |
| `AGENTINBOX_CHAT_ROUTES` | JSON mapping: `group_id → default_agent_name` |
| `AGENTINBOX_BOT_MAP` | JSON mapping: `group_id → bot_id` for reply routing |

### CLI Arguments

```powershell
uv run python -m agentinbox daemon `
  --agent-name hal `
  --queue-name agentinbox-hal `
  --interval 10 `
  --executor copilot `
  --executor-command "" `
  --config agentinbox.toml
```

## Multi-Agent Setup

Run multiple daemons on different machines, each listening to a different queue:

**PC-A (hal):**
```powershell
uv run python -m agentinbox daemon --agent-name hal
# Listens to queue: agentinbox-hal
```

**PC-B (buildbot):**
```powershell
uv run python -m agentinbox daemon --agent-name buildbot --executor command --executor-command "make {instruction}"
# Listens to queue: agentinbox-buildbot
```

**In GroupMe:**
- `@hal run the tests` → routed to PC-A
- `@buildbot deploy staging` → routed to PC-B
- `@@ check status` → routed to the chat's default agent

### Queue Provisioning for Multiple Agents

```powershell
.\tools\Setup-Queue.ps1 -Agents "hal,buildbot" -ConnectionString $env:STORAGE_CONNECTION_STRING
```

Or via the Azure CLI:
```powershell
az storage queue create --name agentinbox-hal --connection-string "<conn-string>"
az storage queue create --name agentinbox-buildbot --connection-string "<conn-string>"
```

Update the Function App settings to register the new agents:
```powershell
az functionapp config appsettings set `
  --name <your-function-app-name> `
  --resource-group agentinbox `
  --settings "AGENTINBOX_AGENTS=hal,buildbot"
```

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

## Troubleshooting

### Daemon crashes with UnicodeEncodeError
When running as a Windows service (Session 0), the console uses cp1252 encoding which can't handle emoji. The daemon sets `sys.stdout.reconfigure(errors="replace")` automatically, but if you see encoding errors in `logs/service_*.log`, ensure you're running the latest code.

### "unrecognized arguments" with CLI flags
Global flags like `--agent-name` must come **after** the subcommand:
```powershell
# Correct:
uv run python -m agentinbox daemon --agent-name hal

# Also correct (before the subcommand):
uv run python -m agentinbox --agent-name hal daemon
```

### Function returns 401
The function uses Azure function-level auth. Ensure you're including `?code=<your-function-key>` in the callback URL. Get the key with:
```powershell
az functionapp keys list --name <app-name> --resource-group agentinbox --query "functionKeys.default" -o tsv
```

### Service won't start (Access Denied)
Service management requires an **elevated (admin) terminal**. Right-click your terminal → Run as Administrator, then:
```powershell
sc start AgentInboxDaemon
```

### Messages not being routed
1. Check that the agent name is in `AGENTINBOX_AGENTS` on the Function App
2. Verify queues exist: `az storage queue list --connection-string "<conn-string>" -o table`
3. Test the function directly:
```powershell
$body = '{"sender_type":"user","text":"@@ test","name":"Test","id":"1","sender_id":"1","group_id":"1"}'
Invoke-RestMethod -Uri "https://<your-url>/api/groupme-callback?code=<key>" -Method POST -ContentType "application/json" -Body $body
```

## License

MIT
