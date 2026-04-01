const { timingSafeEqual } = require("crypto");
const { QueueClient } = require("@azure/storage-queue");

const MAX_PERSONA_INSTRUCTIONS = 1200;
const SITE_TOKEN_HEADER = "x-agentinbox-site-token";

class ValidationError extends Error {}

function parseList(envVar) {
  const raw = process.env[envVar] || "";
  return raw
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

function parseJsonEnv(envVar, fallback) {
  const raw = process.env[envVar];
  if (!raw) return fallback;
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function parseStringMap(envVar) {
  const envValue = process.env[envVar];
  if (!envValue) {
    return {};
  }

  const raw = parseJsonEnv(envVar, null);
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const normalized = {};
    for (const [key, value] of Object.entries(raw)) {
      const mapKey = String(key || "").trim().toLowerCase();
      const mapValue = typeof value === "string" ? value.trim() : "";
      if (mapKey && mapValue) {
        normalized[mapKey] = mapValue;
      }
    }

    return normalized;
  }

  const normalized = {};
  const trimmed = String(envValue).trim().replace(/^\{|\}$/g, "");
  for (const entry of trimmed.split(",")) {
    const [key, ...rest] = entry.split(":");
    const mapKey = String(key || "").trim().toLowerCase().replace(/^["']|["']$/g, "");
    const mapValue = rest.join(":").trim().replace(/^["']|["']$/g, "");
    if (mapKey && mapValue) {
      normalized[mapKey] = mapValue;
    }
  }

  return normalized;
}

function normalizePersonaDefinition(agentName, raw) {
  if (!raw) return null;

  let persona = raw;
  if (typeof raw === "string") {
    persona = { instructions: raw };
  }

  if (typeof persona !== "object" || Array.isArray(persona)) {
    return null;
  }

  const instructions = typeof persona.instructions === "string"
    ? persona.instructions.trim()
    : "";
  if (!instructions) {
    return null;
  }

  const id = typeof persona.id === "string" && persona.id.trim()
    ? persona.id.trim()
    : agentName;

  let version = "";
  if (typeof persona.version === "string") {
    version = persona.version.trim();
  } else if (typeof persona.version === "number") {
    version = String(persona.version);
  }

  return {
    id,
    version,
    instructions: instructions.substring(0, MAX_PERSONA_INSTRUCTIONS),
  };
}

function parsePersonaMap(envVar) {
  const raw = process.env[envVar];
  if (!raw) return {};

  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    return {};
  }

  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return {};
  }

  const normalized = {};
  for (const [agentName, persona] of Object.entries(data)) {
    const key = String(agentName || "").trim().toLowerCase();
    if (!key) continue;

    const parsed = normalizePersonaDefinition(key, persona);
    if (parsed) {
      normalized[key] = parsed;
    }
  }
  return normalized;
}

function loadConfig() {
  return {
    agents: parseList("AGENTINBOX_AGENTS"),
    defaultAgent: (process.env.AGENTINBOX_DEFAULT_AGENT || "hal").toLowerCase(),
    queuePrefix: process.env.AGENTINBOX_QUEUE_PREFIX || "agentinbox-",
    agentPersonas: parsePersonaMap("AGENTINBOX_AGENT_PERSONAS"),
    siteQueueOverrides: parseStringMap("SITE_CHAT_QUEUE_OVERRIDES"),
    storageConnectionString: process.env.STORAGE_CONNECTION_STRING || "",
    siteToken: process.env.SITE_CHAT_SEND_TOKEN || "",
  };
}

function buildChatConfig(config) {
  return {
    agents: config.agents.map((name) => {
      const persona = config.agentPersonas[name] || null;
      return {
        name,
        personaId: persona ? persona.id : null,
        personaVersion: persona ? persona.version : "",
      };
    }),
    defaultAgent: config.defaultAgent,
  };
}

function requireString(value, fieldName) {
  if (typeof value !== "string" || !value.trim()) {
    throw new ValidationError(`${fieldName} is required.`);
  }
  return value.trim();
}

function validateUrl(rawUrl, fieldName) {
  const value = requireString(rawUrl, fieldName);
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new ValidationError(`${fieldName} must be a valid URL.`);
  }

  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new ValidationError(`${fieldName} must use http or https.`);
  }

  return parsed.toString();
}

function resolveTargetAgent(rawAgent, config) {
  const requested = typeof rawAgent === "string" && rawAgent.trim()
    ? rawAgent.trim().toLowerCase()
    : config.defaultAgent;

  if (!requested) {
    throw new ValidationError("targetAgent is required.");
  }

  if (config.agents.length > 0 && !config.agents.includes(requested)) {
    throw new ValidationError(`Unknown targetAgent '${requested}'.`);
  }

  return requested;
}

function buildSiteEnvelope(body, config) {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new ValidationError("Request body must be a JSON object.");
  }

  const targetAgent = resolveTargetAgent(body.targetAgent, config);
  const messageText = requireString(body.text, "text").slice(0, 8000);
  const sender = body.sender && typeof body.sender === "object" ? body.sender : {};
  const senderName = requireString(sender.name, "sender.name");
  const senderId = typeof sender.id === "string" ? sender.id.trim() : "";
  const messageId = requireString(body.messageId, "messageId");
  const threadId = requireString(body.threadId, "threadId");
  const replyWebhookUrl = validateUrl(body.replyWebhookUrl, "replyWebhookUrl");
  const replyAuthToken = typeof body.replyAuthToken === "string"
    ? body.replyAuthToken.trim()
    : "";
  const siteName = typeof body.siteName === "string" && body.siteName.trim()
    ? body.siteName.trim()
    : "rtreitweb";

  const targetQueue = config.siteQueueOverrides[targetAgent] || `${config.queuePrefix}${targetAgent}`;
  const persona = config.agentPersonas[targetAgent] || null;
  const envelope = {
    schema: "groupme-directed-message/v2",
    queuedAtUtc: new Date().toISOString(),
    targetAgent,
    targetQueue,
    directedReason: "site-chat",
    source: {
      provider: "site",
      siteName,
      messageId,
      userId: senderId,
      threadId,
      createdAtEpoch: Math.floor(Date.now() / 1000),
      replyWebhookUrl,
      replyAuthToken,
    },
    sender: {
      id: senderId,
      name: senderName,
      type: "user",
    },
    message: {
      text: messageText,
      attachments: [],
    },
  };

  if (persona) {
    envelope.persona = persona;
  }

  return { envelope, targetQueue, targetAgent };
}

async function enqueueMessage(envelope, queueName, connectionString) {
  const client = new QueueClient(connectionString, queueName);
  await client.createIfNotExists();

  const payload = JSON.stringify(envelope);
  const encoded = Buffer.from(payload).toString("base64");
  await client.sendMessage(encoded);
}

function authenticateSiteRequest(req, config) {
  if (!config.siteToken) {
    return true;
  }

  const suppliedToken = req.headers[SITE_TOKEN_HEADER];
  if (typeof suppliedToken !== "string") {
    return false;
  }

  const supplied = Buffer.from(suppliedToken, "utf8");
  const expected = Buffer.from(config.siteToken, "utf8");
  if (supplied.length !== expected.length) {
    return false;
  }

  return timingSafeEqual(supplied, expected);
}

module.exports = {
  authenticateSiteRequest,
  ValidationError,
  buildChatConfig,
  buildSiteEnvelope,
  enqueueMessage,
  loadConfig,
};
