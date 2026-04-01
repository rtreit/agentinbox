const {
  authenticateSiteRequest,
  ValidationError,
  buildSiteEnvelope,
  enqueueMessage,
  loadConfig,
} = require("../shared/site-chat");

module.exports = async function (context, req) {
  const config = loadConfig();

  if (!authenticateSiteRequest(req, config)) {
    context.res = {
      status: 401,
      headers: { "Content-Type": "application/json" },
      body: { error: "Unauthorized site send request." },
    };
    return;
  }

  if (!config.storageConnectionString) {
    context.res = {
      status: 500,
      headers: { "Content-Type": "application/json" },
      body: { error: "STORAGE_CONNECTION_STRING is not configured." },
    };
    return;
  }

  try {
    const body = req.body && typeof req.body === "string"
      ? JSON.parse(req.body)
      : req.body;
    const { envelope, targetQueue, targetAgent } = buildSiteEnvelope(body, config);

    await enqueueMessage(envelope, targetQueue, config.storageConnectionString);

    context.res = {
      status: 202,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
      },
      body: {
        queued: true,
        targetAgent,
        targetQueue,
        messageId: envelope.source.messageId,
        threadId: envelope.source.threadId,
      },
    };
  } catch (err) {
    const status = err instanceof ValidationError ? 400 : 500;
    if (!(err instanceof ValidationError)) {
      context.log.error("site-chat-send failed:", err.message);
    }

    context.res = {
      status,
      headers: { "Content-Type": "application/json" },
      body: { error: err.message || "Failed to enqueue site chat message." },
    };
  }
};
