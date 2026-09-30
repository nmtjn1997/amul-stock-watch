// Where a user's alerts go. Every account gets a private, random ntfy topic, so phone
// alerts work with no setup beyond installing the ntfy app. A Discord or Slack webhook is
// optional. Nothing else is allowed: no arbitrary URLs, no email relay.

import { validWebhook } from "./util.js";

async function ntfy(env, topic, alert) {
  const server = (env.NTFY_SERVER || "https://ntfy.sh").replace(/\/$/, "");
  const res = await fetch(`${server}/${topic}`, {
    method: "POST",
    headers: {
      Title: alert.title.replace(/[^\x20-\x7e]/g, ""),
      Priority: alert.kind === "stock" ? "high" : "default",
      Tags: alert.kind === "stock" ? "shopping_cart" : "test_tube",
      ...(alert.url ? { Click: alert.url } : {}),
    },
    body: alert.message,
  });
  await res.body?.cancel();
  if (!res.ok) throw new Error(`ntfy HTTP ${res.status}`);
}

async function webhook(url, alert) {
  if (!validWebhook(url)) throw new Error("webhook not allowed");
  const text = `${alert.title}\n${alert.message}`;
  const body = url.includes("hooks.slack.com") ? { text } : { content: text.slice(0, 1900) };
  const res = await fetch(url, { method: "POST", redirect: "manual", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  await res.body?.cancel();
  if (!res.ok) throw new Error(`webhook HTTP ${res.status}`);
}

// Returns { ok, detail, used } where used is the number of outbound requests made.
export async function deliver(env, user, alert) {
  const results = [];
  let used = 0;
  try {
    used++;
    await ntfy(env, user.ntfy_topic, alert);
    results.push("phone: ok");
  } catch (e) {
    results.push(`phone: ${e.message}`);
  }
  if (user.webhook_url) {
    try {
      used++;
      await webhook(user.webhook_url, alert);
      results.push("chat: ok");
    } catch (e) {
      results.push(`chat: ${e.message}`);
    }
  }
  return { ok: results.some((r) => r.endsWith(": ok")), detail: results.join(", "), used };
}
