const { authenticateSiteRequest, buildChatConfig, loadConfig } = require("../shared/site-chat");

module.exports = async function (context, req) {
  const config = loadConfig();
  if (!authenticateSiteRequest(req, config)) {
    context.res = {
      status: 401,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
      },
      body: { error: "Unauthorized site config request." },
    };
    return;
  }

  context.res = {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
    body: buildChatConfig(config),
  };
};
