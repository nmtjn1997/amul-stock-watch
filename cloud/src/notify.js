// Where a user's alerts go:
//   push     browser / home-screen notifications on each device the person turned on (default)
//   ntfy     the ntfy app, opt-in: ntfy.sh without an account shares a daily quota per
//            sending IP and all Workers share Cloudflare's IPs, so NTFY_TOKEN (an ntfy.sh
//            account token) is strongly advised when it is used
//   telegram this site's Telegram bot, once the person linked a chat
//   webhook  a Discord or Slack channel
// Nothing else is allowed: no arbitrary URLs, no email relay.

import { validWebhook } from "./util.js";
import { sendTelegram, telegramEnabled } from "./telegram.js";
import { pushEnabled, sendPush } from "./webpush.js";

async function ntfy(env, topic, alert) {
  const server = (env.NTFY_SERVER || "https://ntfy.sh").replace(/\/$/, "");
  const res = await fetch(`${server}/${topic}`, {
    method: "POST",
    headers: {
      ...(env.NTFY_TOKEN ? { Authorization: `Bearer ${env.NTFY_TOKEN}` } : {}),
      Title: alert.title.replace(/[^\x20-\x7e]/g, ""),
      Priority: alert.kind === "stock" ? "high" : "default",
      Tags: alert.kind === "stock" ? "shopping_cart" : "test_tube",
      ...(alert.url ? { Click: alert.url } : {}),
    },
    body: alert.message,
  });
  await res.body?.cancel();
  if (res.status === 429) throw new Error("ntfy daily limit reached (the shared free quota)");
  if (res.status === 522 || res.status === 524) throw new Error("ntfy.sh did not answer in time");
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
  if (pushEnabled(env)) {
    const subs = (await env.DB.prepare("SELECT * FROM push_subs WHERE user_id = ?").bind(user.id).all()).results;
    let sent = 0;
    const errors = [];
    for (const sub of subs) {
      used++;
      try {
        const r = await sendPush(env, sub, { title: alert.title, body: alert.message, url: alert.url || "/", kind: alert.kind });
        if (r === "gone") await env.DB.prepare("DELETE FROM push_subs WHERE id = ?").bind(sub.id).run();
        else sent++;
      } catch (e) {
        errors.push(e.message);
      }
    }
    if (subs.length) results.push(sent ? `${sent} device${sent > 1 ? "s" : ""}: ok` : `devices: ${errors[0] || "unsubscribed"}`);
  }
  if (user.ntfy_on) {
    try {
      used++;
      await ntfy(env, user.ntfy_topic, alert);
      results.push("ntfy: ok");
    } catch (e) {
      results.push(`ntfy: ${e.message}`);
    }
  }
  if (user.tg_chat_id && telegramEnabled(env)) {
    try {
      used++;
      await sendTelegram(env, user, alert);
      results.push("telegram: ok");
    } catch (e) {
      results.push(`telegram: ${e.message}`);
    }
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
  if (!results.length) results.push("nowhere to send: turn on notifications in Settings");
  return { ok: results.some((r) => r.endsWith(": ok")), detail: results.join(", "), used };
}
