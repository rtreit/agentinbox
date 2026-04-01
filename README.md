# Agent Inbox

Agent Inbox routes directed GroupMe messages into Azure Storage Queues and
lets one or more daemons execute them. The normal topology is:

```text
GroupMe chat -> Azure Function webhook -> Azure Storage queue -> Agent Inbox daemon -> executor -> GroupMe reply
```

Each daemon usually listens to one queue such as `agentinbox-hal` or
`agentinbox-stressbot`. Multiple daemons can share the same chat as long as
each one has a distinct agent name or queue.

## Current project state

This repository currently contains:

- `src/agentinbox/` - the Python CLI, queue consumer, daemon, config loader,
  reply poster, and executors
- `azure-function/` - the GroupMe webhook that routes messages to queues
- `service/` - a Windows service wrapper that runs `python -m agentinbox daemon`
- `tools/` - queue provisioning and inspection helpers

The default executor is the GitHub Copilot CLI, but command and Python-script
executors are also supported.

## Prerequisites

- Python 3.13+
- `uv`
- Azure subscription
- Azure CLI
- Azure Functions Core Tools
- .NET 8 SDK (only needed if you want the Windows service)
- One or more GroupMe bots

## Core commands

From the repo root:

```powershell
uv sync

# Peek at the current queue without consuming messages
uv run agentinbox peek --agent-name hal

# One-shot mode: drain the queue and print what was accepted
uv run agentinbox --agent-name hal

# Persistent daemon
uv run agentinbox daemon --agent-name hal

# Dry-run daemon: consume and print directives without executing them
uv run agentinbox daemon --agent-name hal --dry-run
```

Configuration precedence is:

1. CLI args
2. `AGENTINBOX_*` environment variables
3. `agentinbox.toml`
4. `.env`

## One-time infrastructure deployment

If the Azure Function, storage account, queues, and GroupMe callback are
already set up, skip ahead to
[Bring up an additional system](#bring-up-an-additional-system).

### 1. Create Azure resources

```powershell
az login
az group create --name agentinbox --location westus2

az storage account create `
  --name <yourstorageaccount> `
  --resource-group agentinbox `
  --location westus2 `
  --sku Standard_LRS `
  --kind StorageV2

az functionapp create `
  --name <your-function-app-name> `
  --resource-group agentinbox `
  --consumption-plan-location westus2 `
  --runtime node `
  --runtime-version 20 `
  --functions-version 4 `
  --storage-account <yourstorageaccount> `
  --os-type Windows
```

Fetch the storage connection string:

```powershell
az storage account show-connection-string `
  --name <yourstorageaccount> `
  --resource-group agentinbox `
  --query connectionString -o tsv
```

### 2. Configure the Function App

The webhook routes `@agent`, `@@`, and bot-prefix messages to agent-specific queues.

```powershell
az functionapp config appsettings set `
  --name <your-function-app-name> `
  --resource-group agentinbox `
  --settings `
    "STORAGE_CONNECTION_STRING=<your-connection-string>" `
    "AGENTINBOX_AGENTS=hal,stressbot" `
    "AGENTINBOX_DEFAULT_AGENT=hal" `
    "AGENTINBOX_QUEUE_PREFIX=agentinbox-"
```

Useful optional Function App settings:

| Setting | Purpose |
| --- | --- |
| `AGENTINBOX_CHAT_ROUTES` | Per-chat default agent mapping such as `{"12345":"hal"}` |
| `AGENTINBOX_BOT_MAP` | Per-chat reply bot mapping such as `{"12345":"bot_abc"}` |
| `AGENTINBOX_AGENT_PERSONAS` | Per-agent tone/style config such as `{"hal":{"instructions":"You are HAL. Be calm and concise."},"stressbot":{"instructions":"You are StressBot. Be energetic and crash-hunting focused."}}` |
| `GROUPME_BOT_ID` | Fallback bot ID when `AGENTINBOX_BOT_MAP` has no entry |
| `SITE_CHAT_SEND_TOKEN` | Shared secret required by the Azure Function `site-chat-config` and `site-chat-send` endpoints |
| `SITE_CHAT_QUEUE_OVERRIDES` | Optional JSON map for routing site-chat traffic to alternate queues, e.g. `{"hal":"agentinbox-hal-site"}` |

To update deployed personas without hand-writing the Azure CLI command, use:

```powershell
.\tools\Set-FunctionAppPersonas.ps1 `
  -FunctionApp <your-function-app-name> `
  -ResourceGroup agentinbox `
  -PersonasJson '{"hal":{"instructions":"You are HAL. Be calm and concise."},"stressbot":{"instructions":"You are StressBot. Be energetic and crash-hunting focused."}}'
```

You can also keep the persona JSON in a file:

```powershell
.\tools\Set-FunctionAppPersonas.ps1 `
  -FunctionApp <your-function-app-name> `
  -ResourceGroup agentinbox `
  -PersonasFile .\personas.json
```

Use `-Preview` to validate and print the normalized setting value without
updating Azure.

### 3. Deploy the webhook code

```powershell
Set-Location azure-function
npm install
func azure functionapp publish <your-function-app-name> --javascript
Set-Location ..
```

The Azure Function details and webhook schema are documented further in
`azure-function\README.md`.

### Optional: site chat transport

If you want a web UI instead of GroupMe, `rtreitweb` can call the Agent Inbox
Function App directly and still use the same queue/executor pipeline.

The Function App exposes:

- `GET /api/site-chat-config`
- `POST /api/site-chat-send`

Both endpoints require the normal function key plus a shared
`SITE_CHAT_SEND_TOKEN` header (`X-AgentInbox-Site-Token`). The site can enqueue
standard v2 directives with `source.provider = "site"`, and the daemon can post
acceptance/final replies back to a site webhook instead of GroupMe.

If you need site chat to use a different daemon than GroupMe for the same agent
name, set `SITE_CHAT_QUEUE_OVERRIDES` on the Function App. Example:

```json
{"hal":"agentinbox-hal-site"}
```

Then run a daemon with `--agent-name hal --queue-name agentinbox-hal-site`.

### 4. Point GroupMe at the webhook

Get the function key:

```powershell
az functionapp keys list `
  --name <your-function-app-name> `
  --resource-group agentinbox `
  --query "functionKeys.default" -o tsv
```

Set the GroupMe bot callback URL to:

```text
https://<your-function-app-name>.azurewebsites.net/api/groupme-callback?code=<your-function-key>
```

If you want to upload a bot avatar image to GroupMe's CDN, the repo includes a
helper script:

```powershell
.\scripts\Upload-GroupMeImage.ps1 -Path .\avatar.png
```

By default it reads `GROUPME_ACCESS_TOKEN` from the current environment or a
nearby `.env` file. Use the returned `Url` value as the bot `avatar_url`.

### 5. Queue provisioning

The webhook can create queues on first use, so pre-provisioning is optional.
If you want to create them ahead of time:

```powershell
.\tools\Setup-Queue.ps1 `
  -ConnectionString "<your-connection-string>" `
  -Agents "hal,stressbot"
```

## Bring up an additional system

Once the cloud side is deployed, adding another PC is mostly a local setup
exercise.

### 1. Clone the repo and install dependencies

```powershell
git clone https://github.com/rtreit/agentinbox.git
Set-Location agentinbox
uv sync
```

### 2. Create `.env`

```powershell
Copy-Item .env.example .env
```

At minimum, set:

```env
STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net
GH_TOKEN=github_pat_or_fine_grained_token_here
AGENTINBOX_AGENT_NAME=stressbot
AGENTINBOX_WORKING_DIRECTORY=C:\Users\randy\Git\TerminalStress
GROUPME_BOT_ID_STRESSBOT=your_stressbot_bot_id
```

Notes:

- `AGENTINBOX_AGENT_NAME` controls the default queue name. If you set
  `AGENTINBOX_AGENT_NAME=stressbot`, the daemon listens to
  `agentinbox-stressbot` unless you also set `AGENTINBOX_QUEUE_NAME`.
- `AGENTINBOX_WORKING_DIRECTORY` is the executor target directory. This is
  where Copilot, command, or Python executors will run.
- `AGENTINBOX_COPILOT_MODEL` optionally passes `--model <value>` to the nested
  Copilot CLI. Leave it unset to keep the current default model selection.
- `GH_TOKEN` is strongly recommended for Windows service / Session 0 runs. The
  daemon warns if it detects service mode without a headless auth token.
- If you only use one bot, `GROUPME_BOT_ID` is enough. Per-agent IDs such as
  `GROUPME_BOT_ID_STRESSBOT` override the default.
- Per-agent personality is normally defined centrally in the Azure Function via
  `AGENTINBOX_AGENT_PERSONAS`, then delivered with each queued directive. Most
  daemon machines do not need separate local persona config.

### 3. Decide what directory the daemon should execute in

This is the most important distinction when setting up a second machine:

- the Agent Inbox process itself should usually start from the `agentinbox`
  clone so it can load that clone's `.env` and optional `agentinbox.toml`
- the executor target repo is configured separately through
  `AGENTINBOX_WORKING_DIRECTORY` or `--working-directory`

Example:

- Agent Inbox lives at `C:\Users\randy\Git\agentinbox`
- the daemon loads `.env` from that directory
- the executor actually works in `C:\Users\randy\Git\TerminalStress`

### 4. Smoke test interactively

```powershell
# Confirm the queue resolves the way you expect
uv run agentinbox peek

# Consume pending directives without executing them
uv run agentinbox daemon --dry-run

# Run the real daemon interactively
uv run agentinbox daemon
```

If your chat and webhook are already wired up, send a test message such as:

```text
@stressbot pwd
```

or, for the chat's default agent:

```text
@@ status
```

### 5. Auto-launch the interactive daemon at sign-in (Windows)

Use this if you want a visible interactive daemon window each time you sign in.
Do **not** point the Run key directly at `conhost.exe` or a raw
`python -m agentinbox daemon` command. That approach is fragile because quoting
and working-directory differences can leave the console at
`C:\Windows\System32` and fail before Agent Inbox starts.

Instead, call the helper script that already knows how to find the repo root,
pick the right Python interpreter, and launch the daemon only if one is not
already running:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "C:\Users\randy\Git\agentinbox\src\agentinbox\ensure_daemon.ps1"
```

To register that for the current user at logon:

```powershell
$repo = "C:\Users\randy\Git\agentinbox"
$command = "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$repo\src\agentinbox\ensure_daemon.ps1`""

New-ItemProperty `
  -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
  -Name "AgentInboxHal" `
  -Value $command `
  -PropertyType String `
  -Force
```

That is the recommended interactive autostart path today. If you want an
unattended/background install instead, use the Windows service below.

### 6. Install the Windows service

If the interactive daemon works, you can install the service wrapper on that
machine.

```powershell
dotnet publish service\AgentInboxService.csproj -c Release -o service\publish

# Requires an elevated PowerShell window
.\service\publish\AgentInboxService.exe install
.\service\publish\AgentInboxService.exe start
.\service\publish\AgentInboxService.exe status
```

The service wrapper reads `service\publish\agentinbox-service.json`. The
defaults are usually fine after `uv sync`:

- `pythonPath` -> `.venv\Scripts\python.exe`
- `scriptPath` -> `-m agentinbox daemon`
- `workingDirectory` -> `.` (the repo root when published under `service\publish`)

That default `workingDirectory` is usually what you want, because the service
loads `.env` from there.

Inspect or modify the wrapper configuration with:

```powershell
.\service\publish\AgentInboxService.exe config show
.\service\publish\AgentInboxService.exe config path
.\service\publish\AgentInboxService.exe config set restartDelaySeconds 10
```

In most cases, put per-machine runtime settings in `.env` and leave the service
wrapper config for Python path, restart policy, and other service-specific
settings.

## Running multiple daemons

A common pattern is one queue per machine or role:

- `agentinbox-hal`
- `agentinbox-stressbot`
- `agentinbox-devbot`

Then configure the Function App so those agent names are routable:

```powershell
az functionapp config appsettings set `
  --name <your-function-app-name> `
  --resource-group agentinbox `
  --settings "AGENTINBOX_AGENTS=hal,stressbot,devbot"
```

Example chat usage:

- `@hal what user are you running as?`
- `@stressbot run a 10 minute stress test`
- `@@ status`

If multiple GroupMe chats share the same infrastructure, use:

- `AGENTINBOX_CHAT_ROUTES` to choose the default agent per chat
- `AGENTINBOX_BOT_MAP` to choose the reply bot per chat

## Optional `agentinbox.toml`

If you prefer a checked-in config file instead of relying on environment
variables, create `agentinbox.toml` in the repo root:

```toml
[agent]
name = "stressbot"

[queue]
name = "agentinbox-stressbot"
poll_interval = 10

[executor]
type = "copilot"
copilot_model = "claude-sonnet-4.5"
working_directory = "C:\\Users\\randy\\Git\\TerminalStress"

[logging]
directory = "logs"
```

Remember that CLI args and `AGENTINBOX_*` environment variables override the
TOML file.

For Copilot executor model selection, you can use either:

- `AGENTINBOX_COPILOT_MODEL=claude-sonnet-4.5`
- `[executor].copilot_model = "claude-sonnet-4.5"`

When unset, Agent Inbox preserves the existing Copilot CLI behavior and does
not add `--model`.

## Logs and troubleshooting

### Where to look

- Windows service wrapper logs:
  - `logs\service_stdout.log`
  - `logs\service_stderr.log`
- Structured daemon log:
  - `<AGENTINBOX_WORKING_DIRECTORY>\logs\daemon.jsonl`
- Copilot per-task stderr:
  - `<AGENTINBOX_WORKING_DIRECTORY>\logs\copilot_stderr_<message_id>.log`

### Useful checks

```powershell
Get-Content .\logs\service_stdout.log -Tail 50 -ErrorAction SilentlyContinue
Get-Content .\logs\service_stderr.log -Tail 50 -ErrorAction SilentlyContinue
Get-Content C:\path\to\target-repo\logs\daemon.jsonl -Tail 20 -ErrorAction SilentlyContinue
```

### Common problems

#### Service mode says "No authentication information found"

Set `GH_TOKEN` (or `GITHUB_TOKEN` / `COPILOT_GITHUB_TOKEN`) in `.env`, then
restart the service. This is the supported headless auth path for nested
Copilot runs under Session 0.

#### The daemon says the queue does not exist

For a brand-new agent, this usually just means no routed message has created the
queue yet. Current builds treat that as an idle queue and keep polling quietly.

If you want the queue provisioned ahead of time, create it with
`tools\Setup-Queue.ps1` or `az storage queue create`.

If the queue still never appears after sending a directed message, make sure the
Azure Function is correctly configured so it can create the queue on first
routed message.

#### No reply appears in GroupMe

Check, in order:

1. the Function App settings (`AGENTINBOX_AGENTS`, `AGENTINBOX_CHAT_ROUTES`,
   `AGENTINBOX_BOT_MAP`)
2. the daemon's resolved queue (`uv run agentinbox peek`)
3. the daemon/service logs
4. the bot ID values in `.env`

## Project structure

```text
agentinbox/
|-- src/agentinbox/      Python package
|-- azure-function/      GroupMe webhook
|-- scripts/             Helper scripts
|-- service/             Windows service wrapper
|-- tools/               Queue setup and diagnostics
|-- .env.example         Environment variable template
|-- pyproject.toml       uv project metadata
`-- README.md
```

## License

MIT
