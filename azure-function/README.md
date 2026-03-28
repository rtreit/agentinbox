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
| `AGENTINBOX_AGENTS` | **Yes** | Comma-separated list of known agent names (e.g., `hal,buildbot`). |
| `AGENTINBOX_DEFAULT_AGENT` | No | Default agent name when no specific agent is targeted. Default: `hal`. |
| `AGENTINBOX_QUEUE_PREFIX` | No | Queue name prefix. Default: `agentinbox-`. |
| `AGENTINBOX_CHAT_ROUTES` | No | JSON mapping `group_id` → default agent name (e.g., `{"12345":"hal","67890":"buildbot"}`). |
| `AGENTINBOX_BOT_MAP` | No | JSON mapping `group_id` → bot_id for replies (e.g., `{"12345":"bot_abc","67890":"bot_xyz"}`). |
| `GROUPME_BOT_ID` | No | Fallback bot ID used when `AGENTINBOX_BOT_MAP` has no entry for the group. |

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
    "AGENTINBOX_AGENTS": "hal,buildbot",
    "AGENTINBOX_DEFAULT_AGENT": "hal",
    "AGENTINBOX_QUEUE_PREFIX": "agentinbox-"
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
