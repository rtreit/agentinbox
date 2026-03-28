/**
 * groupme-callback — Azure Function (HTTP trigger)
 *
 * Receives GroupMe webhook callbacks and routes directed messages to
 * per-agent Azure Storage Queues for multi-agent / multi-chat support.
 *
 * Routing algorithm:
 *   1. @agentname prefix  → route to that agent's queue
 *   2. @@ or 🤖 prefix    → route to the chat's default agent
 *   3. Other configured prefixes → route to default agent
 *   Target queue = "{AGENTINBOX_QUEUE_PREFIX}{targetAgent}"
 *
 * Uses the @azure/storage-queue SDK for dynamic queue routing because
 * Azure Function output bindings only support a single static queue name.
 */

const { QueueClient } = require("@azure/storage-queue");

// ---------------------------------------------------------------------------
// Configuration helpers
// ---------------------------------------------------------------------------

/** Parse a comma-separated env var into a lowercase trimmed array. */
function parseList(envVar) {
  const raw = process.env[envVar] || "";
  return raw
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

/** Parse a JSON env var, returning fallback on failure. */
function parseJsonEnv(envVar, fallback) {
  const raw = process.env[envVar];
  if (!raw) return fallback;
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

/** Load configuration from environment variables (cached per cold-start). */
function loadConfig() {
  return {
    callbackToken: process.env.GROUPME_CALLBACK_TOKEN || "",
    agents: parseList("AGENTINBOX_AGENTS"),
    defaultAgent: (process.env.AGENTINBOX_DEFAULT_AGENT || "hal").toLowerCase(),
    queuePrefix: process.env.AGENTINBOX_QUEUE_PREFIX || "agentinbox-",
    chatRoutes: parseJsonEnv("AGENTINBOX_CHAT_ROUTES", {}),
    botMap: parseJsonEnv("AGENTINBOX_BOT_MAP", {}),
    fallbackBotId: process.env.GROUPME_BOT_ID || "",
    storageConnectionString: process.env.STORAGE_CONNECTION_STRING || "",
    // Additional directed-message prefixes beyond @@ and 🤖
    instructionPrefixes: ["@@", "🤖"],
  };
}

// ---------------------------------------------------------------------------
// Message filtering & parsing
// ---------------------------------------------------------------------------

/** Return the parsed body as an object regardless of content-type. */
function parseBody(req) {
  const body = req.body;
  if (!body) return null;
  if (typeof body === "string") {
    try {
      return JSON.parse(body);
    } catch {
      return null;
    }
  }
  return body;
}

/** Verify the callback token matches (if configured). */
function authenticateRequest(req, token) {
  if (!token) return true; // no token configured → allow all
  const supplied = (req.query && req.query.token) || req.headers["x-callback-token"];
  return supplied === token;
}

/** Return true if the message should be processed (user-sent, non-empty). */
function isProcessableMessage(msg) {
  if (!msg) return false;
  if (msg.sender_type !== "user") return false;
  if (!msg.text || msg.text.trim().length === 0) return false;
  return true;
}

/**
 * Select safe attachments to include in the queued message.
 * Only keep "mentions" and "reply" types; cap at 5 total.
 */
function selectSafeAttachments(attachments) {
  if (!Array.isArray(attachments)) return [];
  const safe = attachments.filter(
    (a) => a && (a.type === "mentions" || a.type === "reply")
  );
  return safe.slice(0, 5);
}

// ---------------------------------------------------------------------------
// Routing logic
// ---------------------------------------------------------------------------

/**
 * Determine the target agent and reason for routing.
 *
 * @param {string} text       - Raw message text
 * @param {string} groupId    - GroupMe group ID
 * @param {object} msg        - Full GroupMe message object
 * @param {object} config     - Loaded configuration
 * @returns {{ targetAgent: string, reason: string, trimmedText: string } | null}
 */
function resolveRoute(text, groupId, msg, config) {
  const trimmed = text.trim();
  const lower = trimmed.toLowerCase();

  // 1. Explicit @agentname prefix — route to that specific agent
  for (const agent of config.agents) {
    const prefix = `@${agent}`;
    if (lower.startsWith(prefix)) {
      // Make sure the prefix is followed by whitespace or end-of-string
      // to avoid matching @halo when the agent is "hal"
      const afterPrefix = lower.charAt(prefix.length);
      if (!afterPrefix || /\s/.test(afterPrefix)) {
        const instruction = trimmed.substring(prefix.length).trim();
        return {
          targetAgent: agent,
          reason: "tag",
          trimmedText: instruction || trimmed,
        };
      }
    }
  }

  // 2. Instruction prefixes (@@ or 🤖) — route to chat's default agent
  for (const pfx of config.instructionPrefixes) {
    if (trimmed.startsWith(pfx)) {
      const instruction = trimmed.substring(pfx.length).trim();
      const agent = resolveDefaultAgent(groupId, config);
      return {
        targetAgent: agent,
        reason: "instruction-prefix",
        trimmedText: instruction || trimmed,
      };
    }
  }

  // 3. Mention attachment — check if any mention user_id matches an agent tag
  //    GroupMe mentions have loci (start/length) mapping to user_ids.
  //    We look for user names that match our agent names.
  if (Array.isArray(msg.attachments)) {
    for (const att of msg.attachments) {
      if (att.type !== "mentions") continue;
      // Check if the mentioned text matches a known agent
      if (Array.isArray(att.loci) && Array.isArray(att.user_ids)) {
        for (let i = 0; i < att.loci.length; i++) {
          const [start, len] = att.loci[i];
          const mentionText = trimmed
            .substring(start, start + len)
            .replace(/^@/, "")
            .toLowerCase();
          if (config.agents.includes(mentionText)) {
            const instruction = trimmed.substring(start + len).trim();
            return {
              targetAgent: mentionText,
              reason: "mention",
              trimmedText: instruction || trimmed,
            };
          }
        }
      }
    }
  }

  // No directed pattern matched
  return null;
}

/** Get the default agent for a given group, falling back to global default. */
function resolveDefaultAgent(groupId, config) {
  if (groupId && config.chatRoutes[groupId]) {
    return config.chatRoutes[groupId].toLowerCase();
  }
  return config.defaultAgent;
}

/** Get the reply bot ID for a given group. */
function resolveReplyBotId(groupId, config) {
  if (groupId && config.botMap[groupId]) {
    return config.botMap[groupId];
  }
  return config.fallbackBotId;
}

// ---------------------------------------------------------------------------
// Queue operations
// ---------------------------------------------------------------------------

/**
 * Enqueue a message to the target agent's queue.
 * Creates the queue if it doesn't exist (idempotent).
 */
async function enqueueMessage(envelope, queueName, connectionString, context) {
  const client = new QueueClient(connectionString, queueName);
  await client.createIfNotExists();

  // Azure Storage Queues require base64-encoded message bodies
  const payload = JSON.stringify(envelope);
  const encoded = Buffer.from(payload).toString("base64");
  await client.sendMessage(encoded);

  context.log(`Enqueued to "${queueName}" — agent=${envelope.targetAgent}, ` +
    `sender=${envelope.sender.name}, reason=${envelope.directedReason}`);
}

// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------

module.exports = async function (context, req) {
  const config = loadConfig();

  // --- Parse & validate ---
  const msg = parseBody(req);
  if (!msg) {
    context.res = { status: 204, body: "" };
    return;
  }

  if (!authenticateRequest(req, config.callbackToken)) {
    context.log.warn("Callback token mismatch — rejecting request");
    context.res = { status: 401, body: "unauthorized" };
    return;
  }

  if (!isProcessableMessage(msg)) {
    // System messages, bot messages, or empty text — silently accept
    context.res = { status: 204, body: "" };
    return;
  }

  // --- Route ---
  const groupId = msg.group_id || "";
  const route = resolveRoute(msg.text, groupId, msg, config);

  if (!route) {
    // Not a directed message — ignore
    context.res = { status: 204, body: "" };
    return;
  }

  // --- Build v2 envelope ---
  const targetQueue = `${config.queuePrefix}${route.targetAgent}`;
  const replyBotId = resolveReplyBotId(groupId, config);

  const envelope = {
    schema: "groupme-directed-message/v2",
    queuedAtUtc: new Date().toISOString(),
    targetAgent: route.targetAgent,
    targetQueue: targetQueue,
    directedReason: route.reason,
    source: {
      provider: "groupme",
      messageId: msg.id || "",
      groupId: groupId,
      createdAtEpoch: msg.created_at || 0,
      replyBotId: replyBotId,
    },
    sender: {
      id: msg.sender_id || msg.user_id || "",
      name: msg.name || "",
      type: msg.sender_type || "user",
    },
    message: {
      text: (route.trimmedText || "").substring(0, 8000),
      attachments: selectSafeAttachments(msg.attachments),
    },
  };

  // --- Enqueue ---
  if (!config.storageConnectionString) {
    context.log.error("STORAGE_CONNECTION_STRING is not configured");
    context.res = { status: 500, body: "storage not configured" };
    return;
  }

  try {
    await enqueueMessage(envelope, targetQueue, config.storageConnectionString, context);
  } catch (err) {
    context.log.error(`Failed to enqueue message: ${err.message}`);
    context.res = { status: 500, body: "enqueue failed" };
    return;
  }

  context.res = {
    status: 200,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      status: "queued",
      targetAgent: route.targetAgent,
      targetQueue: targetQueue,
      reason: route.reason,
    }),
  };
};
