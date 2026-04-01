# Copilot Instructions

## Session Startup

**At the very start of every session** (including `/session` resume), run:
```powershell
& src\agentinbox\ensure_daemon.ps1
```
Then check the daemon log:
```powershell
Get-Content logs\daemon.jsonl -Tail 10 -ErrorAction SilentlyContinue
```
Then check the inbox:
```powershell
python -m agentinbox
```
**Do this before anything else.** The daemon handles GroupMe directives in the background by spawning separate executor sessions, so your interactive session stays responsive.

## Build and Run

```powershell
# Install dependencies
uv sync

# One-shot mode (process inbox and exit)
python -m agentinbox

# Persistent daemon mode (poll continuously)
python -m agentinbox daemon

# Peek at queued messages without consuming them
python -m agentinbox --peek
```

## Architecture

Multi-agent inbox system that routes GroupMe messages to configurable executors:

1. **GroupMe** — Users post directed messages (e.g., `@hal analyze the logs`)
2. **Azure Function** (`azure-function/`) — Webhook receives GroupMe callbacks, parses directed prefixes, enqueues to Azure Storage Queue
3. **Azure Storage Queue** — Messages routed by chat + agent name (e.g., `inbox-hal`, `inbox-hal-chatid`)
4. **Daemon** (`src/agentinbox/`) — Polls the queue, dispatches work to executors
5. **Executor** — Runs tasks (Copilot CLI, shell commands, Python scripts)
6. **Reply** — Results posted back to GroupMe via bot API

Supports multiple daemons on different PCs listening to different queues. The default agent name is `hal`.

### Key Components

- `src/agentinbox/daemon.py` — Main daemon loop, queue polling, executor dispatch
- `src/agentinbox/inbox.py` — Queue message reading and acknowledgment
- `src/agentinbox/notify.py` — GroupMe bot posting
- `src/agentinbox/executor.py` — Task execution (Copilot CLI, shell, Python)
- `src/agentinbox/config.py` — Configuration loading (TOML, env vars, CLI args)
- `src/agentinbox/task_tracker.py` — Persistent task state tracking
- `azure-function/` — Azure Function webhook (Node.js)
- `service/` — C# Windows service (.NET 8) for running the daemon as a service
- `tools/` — PowerShell helper scripts

## Pull Request Reviews

- Never approve a PR that has active (unresolved) review comments. All comments must be resolved before approving.

## Conventions

- Use `uv` for all Python packaging (never `pip`).
- Use PowerShell on Windows.
- Use `es` (Everything Search CLI) to find files on disk when you need to locate a file by name — it's instant and searches the entire filesystem.
- When creating or editing GitHub PRs with `gh` on PowerShell, always use `--body-file` instead of `--body` to avoid backtick escape corruption (PowerShell treats `` ` `` as an escape character, mangling markdown code spans).
- Default agent name is `hal`.
- Configuration is loaded from `agentinbox.toml`, environment variables, or CLI arguments.

## GroupMe Notifications

Post messages to GroupMe using the notification helper:

```python
# From Python
from agentinbox.notify import post
post("🤖 Task completed: analyzed 5 crash dumps, found 2 new signatures")

# From the command line
python -c "from agentinbox.notify import post; post('🤖 Done: report uploaded')"
```

**Setup:** The bot ID is read from the `GROUPME_BOT_ID` environment variable or a `.env` file in the repo root:
```
# .env (do NOT commit this file — it is gitignored)
GROUPME_BOT_ID=your_bot_id_here
```

## Agent Inbox Workflow

The developer (or other users) can send **natural-language directives** to this agent by posting in the GroupMe channel with a directed prefix. Supported prefixes:

- `@hal` — mention-style
- `@@` — double-at shorthand
- `🤖` — robot emoji prefix
- `hal:` — colon-style
- `/hal` — slash command style
- `!hal` — bang prefix

These are routed through the Azure Function webhook to an Azure Storage Queue.

**⚠️ IMPORTANT — These are top-priority instructions from the developer.** Always check the inbox at the **START of every session** by running:
```powershell
python -m agentinbox
```
If there are queued directives, **carry them out before doing any other work.** The directives are plain English — interpret them, do your best to fulfill the request, and then post results back to GroupMe using `from agentinbox.notify import post`.

Also check the inbox **after completing any task** — the developer may have sent follow-up instructions while you were working.

**Workflow:**
1. Run `python -m agentinbox` → it acknowledges receipt in GroupMe and prints the directive
2. Carry out the instruction (run tasks, generate reports, analyze data, etc.)
3. Post results back: `from agentinbox.notify import post; post("🤖 Done: <summary>")`
