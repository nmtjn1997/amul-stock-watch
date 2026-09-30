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
  const reason = res.ok ? "" : (await res.text()).slice(0, 80);
  if (reason === "messages_tab_disabled") {
    throw new Error("Slack could not post: this webhook points at the app's own DM and its Messages tab is off. Make a webhook for a channel instead");
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}${reason ? ` ${reason}` : ""}`);
  await res.body?.cancel();
}

// Returns { ok, detail, used, channels } where used is the number of outbound requests made
// and channels is one { name, ok, detail } per channel that was tried.
export async function deliver(env, user, alert) {
  const channels = [];
  let used = 0;
  const attempt = async (name, fn) => {
    used++;
    try {
      await fn();
      channels.push({ name, ok: true, detail: "sent" });
    } catch (e) {
      channels.push({ name, ok: false, detail: e.message });
    }
  };
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
    if (subs.length) {
      channels.push(sent
        ? { name: "Browser", ok: true, detail: `sent to ${sent} of ${subs.length} device${subs.length > 1 ? "s" : ""}` }
        : { name: "Browser", ok: false, detail: errors[0] || "the device turned notifications off" });
    }
  }
  if (user.ntfy_on) await attempt("ntfy", () => ntfy(env, user.ntfy_topic, alert));
  if (user.tg_chat_id && telegramEnabled(env)) await attempt("Telegram", () => sendTelegram(env, user, alert));
  if (user.webhook_url) await attempt(user.webhook_url.includes("hooks.slack.com") ? "Slack" : "Discord", () => webhook(user.webhook_url, alert));
  const detail = channels.length
    ? channels.map((c) => `${c.name}: ${c.ok ? "ok" : c.detail}`).join(", ")
    : "nowhere to send: turn on notifications in Settings";
  return { ok: channels.some((c) => c.ok), detail, used, channels };
}
