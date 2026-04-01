# Agent Inbox — Azure Function

Azure Function that receives GroupMe webhook callbacks and routes directed messages to per-agent Azure Storage Queues.

## Architecture

```
GroupMe → webhook POST → /api/groupme-callback → @azure/storage-queue SDK → agentinbox-{agent}
```

Messages are routed to agent-specific queues based on directed prefixes:

| Pattern | Example | Routes to |
|---------|---------|-----------|
| `@agentname ...` | `@hal check the logs` | `agentinbox-hal` |
| `@@ ...` | `@@ run stress test` | Chat's default agent queue |
| `🤖 ...` | `🤖 analyze dumps` | Chat's default agent queue |
| Mention attachment | GroupMe @-mention of agent | That agent's queue |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROUPME_CALLBACK_TOKEN` | No | Token to authenticate GroupMe webhook callbacks. If unset, all requests are accepted. |
| `STORAGE_CONNECTION_STRING` | **Yes** | Azure Storage connection string for queue access. |
| `AGENTINBOX_AGENTS` | **Yes** | Comma-separated list of known agent names (e.g., `hal,devbot`). |
| `AGENTINBOX_DEFAULT_AGENT` | No | Default agent name when no specific agent is targeted. Default: `hal`. |
| `AGENTINBOX_QUEUE_PREFIX` | No | Queue name prefix. Default: `agentinbox-`. |
| `AGENTINBOX_CHAT_ROUTES` | No | JSON mapping `group_id` → default agent name (e.g., `{"12345":"hal","67890":"devbot"}`). |
| `AGENTINBOX_BOT_MAP` | No | JSON mapping `group_id` → bot_id for replies (e.g., `{"12345":"bot_abc","67890":"bot_xyz"}`). |
| `AGENTINBOX_AGENT_PERSONAS` | No | JSON mapping `agent_name` → persona definition. Persona affects tone/style/identity only and is attached to queued directives. |
| `GROUPME_BOT_ID` | No | Fallback bot ID used when `AGENTINBOX_BOT_MAP` has no entry for the group. |
| `SITE_CHAT_SEND_TOKEN` | No | Shared secret required by the `site-chat-config` and `site-chat-send` endpoints. |
| `SITE_CHAT_QUEUE_OVERRIDES` | No | Optional JSON mapping `agent_name` → queue name for site-chat traffic only (e.g., `{"hal":"agentinbox-hal-site"}`). |

To update personas on a deployed Function App, you can use the repo helper:

```powershell
.\tools\Set-FunctionAppPersonas.ps1 `
  -FunctionApp <your-function-app-name> `
  -ResourceGroup <your-resource-group> `
  -PersonasFile .\personas.json
```

Use `-Preview` to validate and print the normalized JSON without updating Azure.

## Message Schema (v2)

```json
{
  "schema": "groupme-directed-message/v2",
  "queuedAtUtc": "2025-01-15T12:00:00.000Z",
  "targetAgent": "hal",
  "targetQueue": "agentinbox-hal",
  "directedReason": "tag",
  "source": {
    "provider": "groupme",
    "messageId": "abc123",
    "groupId": "12345",
    "createdAtEpoch": 1234567890,
    "replyBotId": "bot_abc"
  },
  "sender": {
    "id": "sender_id",
    "name": "Randy",
    "type": "user"
  },
  "persona": {
    "id": "hal",
    "version": "1",
    "instructions": "You are HAL. Be calm, concise, and professional."
  },
  "message": {
    "text": "check the logs for crashes",
    "attachments": []
  }
}
```

## Local Development

```bash
# Install dependencies
npm install

# Create local.settings.json (not committed)
cat > local.settings.json <<'EOF'
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "node",
    "STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true",
    "AGENTINBOX_AGENTS": "hal,devbot",
    "AGENTINBOX_DEFAULT_AGENT": "hal",
    "AGENTINBOX_QUEUE_PREFIX": "agentinbox-",
    "AGENTINBOX_AGENT_PERSONAS": "{\"hal\":{\"instructions\":\"You are HAL. Be calm and concise.\"},\"devbot\":{\"instructions\":\"You are DevBot. Be concise and development-focused.\"}}"
  }
}
EOF

# Start (requires Azure Functions Core Tools: npm i -g azure-functions-core-tools@4)
func start
```

## Testing

```bash
# Directed message via @agentname
curl -X POST http://localhost:7071/api/groupme-callback \
  -H "Content-Type: application/json" \
  -d '{
    "id": "1", "sender_type": "user", "sender_id": "123",
    "name": "Randy", "group_id": "12345",
    "text": "@hal check the crash dumps", "attachments": [],
    "created_at": 1700000000
  }'

# Directed message via @@
curl -X POST http://localhost:7071/api/groupme-callback \
  -H "Content-Type: application/json" \
  -d '{
    "id": "2", "sender_type": "user", "sender_id": "123",
    "name": "Randy", "group_id": "12345",
    "text": "@@ run a 10 minute stress test", "attachments": [],
    "created_at": 1700000001
  }'

# Non-directed message (should return 204)
curl -X POST http://localhost:7071/api/groupme-callback \
  -H "Content-Type: application/json" \
  -d '{
    "id": "3", "sender_type": "user", "sender_id": "123",
    "name": "Randy", "group_id": "12345",
    "text": "just a regular chat message", "attachments": [],
    "created_at": 1700000002
  }'
```

### Site chat endpoints

The same Function App can also serve a web-based chat transport for sites such
as `rtreitweb`.

- `GET /api/site-chat-config` — returns the configured agent list and personas
- `POST /api/site-chat-send` — validates a site-originated chat message and enqueues a v2 directive

Both endpoints require:

- the normal Azure Function key (`?code=...`)
- an `X-AgentInbox-Site-Token` header matching `SITE_CHAT_SEND_TOKEN`

If you want the web UI to route an agent through a different daemon/queue than
GroupMe uses, set `SITE_CHAT_QUEUE_OVERRIDES`. For example, this routes site
chat for `hal` to `agentinbox-hal-site` while leaving normal GroupMe `hal`
traffic on `agentinbox-hal`:

```json
{"hal":"agentinbox-hal-site"}
```

Example request:

```bash
curl -X POST "https://<your-function-app>.azurewebsites.net/api/site-chat-send?code=<function-key>" \
  -H "Content-Type: application/json" \
  -H "X-AgentInbox-Site-Token: <shared-site-token>" \
  -d '{
    "siteName": "rtreitweb",
    "threadId": "thread-123",
    "messageId": "user-456",
    "targetAgent": "devbot",
    "text": "Check the latest deployment logs",
    "sender": {
      "id": "user@example.com",
      "name": "Randy"
    },
    "replyWebhookUrl": "https://rtreit.com/api/chat-reply",
    "replyAuthToken": "<reply-token>"
  }'
```

## Deploying

```bash
# Deploy to Azure (requires az CLI and func CLI)
func azure functionapp publish <your-function-app-name>
```

Configure the environment variables listed above in the Azure Function App's **Configuration → Application settings**.

## Queue Notes

- Queues are created automatically on first message (`createIfNotExists`).
- Queue names follow the pattern `agentinbox-{agentname}` (lowercase, alphanumeric + hyphens).
- Messages are base64-encoded JSON, as required by Azure Storage Queues.
- The `replyBotId` field tells the consuming daemon which GroupMe bot to use when posting replies back to the chat.
- The optional `persona` block is included when `AGENTINBOX_AGENT_PERSONAS` defines one for the routed agent.
- Site-originated messages can set `source.provider = "site"` along with `threadId`, `replyWebhookUrl`, and `replyAuthToken` so daemon replies can go back to a web UI instead of GroupMe.
