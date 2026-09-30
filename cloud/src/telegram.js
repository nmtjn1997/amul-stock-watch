// Telegram alerts through this site's own bot (TELEGRAM_BOT_TOKEN).
// Linking: Settings asks for a one-time code, the person opens t.me/<bot>?start=<code>,
// Telegram posts "/start <code>" to /telegram/hook, and that chat id is saved.
// The webhook and the bot's username are set up on first use, so there is nothing to run.

import { logEvent } from "./log.js";
import { allow, fail, now, randomToken, timingSafeEqual } from "./util.js";

export const telegramEnabled = (env) => Boolean(env.TELEGRAM_BOT_TOKEN);

async function call(env, method, body) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { status: res.status, data };
}

// Telegram echoes this in a header on every webhook call; derived so no extra secret exists.
async function hookSecret(env) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`hook:${env.TELEGRAM_BOT_TOKEN}`));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// The bot username, registering the webhook the first time (or after the token changes).
async function botName(env, origin) {
  const secret = await hookSecret(env);
  const row = await env.DB.prepare("SELECT value FROM meta WHERE key = 'tg_bot'").first();
  if (row) {
    const saved = JSON.parse(row.value);
    if (saved.secret === secret && saved.origin === origin) return saved.name;
  }
  const me = await call(env, "getMe", {});
  if (!me.data.ok) throw new Error(`Telegram getMe: ${me.data.description || me.status}`);
  const hook = await call(env, "setWebhook", { url: `${origin}/telegram/hook`, secret_token: secret, allowed_updates: ["message"] });
  if (!hook.data.ok) throw new Error(`Telegram setWebhook: ${hook.data.description || hook.status}`);
  const value = JSON.stringify({ name: me.data.result.username, secret, origin });
  await env.DB.prepare("INSERT INTO meta (key, value) VALUES ('tg_bot', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").bind(value).run();
  return me.data.result.username;
}

export async function linkStart(env, req, user) {
  if (!telegramEnabled(env)) fail(400, "Telegram is not set up on this site.");
  if (!(await allow(env.DB, `write:${user.id}`, 60, 60))) fail(429, "Slow down a little.");
  let name;
  try {
    name = await botName(env, new URL(req.url).origin);
  } catch (e) {
    await logEvent(env, { level: "error", kind: "telegram", user, detail: e.message });
    fail(503, "Could not reach Telegram just now. Try again in a minute.");
  }
  const code = randomToken(18);
  await env.DB.prepare("UPDATE users SET tg_code = ?, tg_code_exp = ? WHERE id = ?").bind(code, now() + 900, user.id).run();
  return { url: `https://t.me/${name}?start=${code}` };
}

export async function unlink(env, user) {
  await env.DB.prepare("UPDATE users SET tg_chat_id = NULL, tg_code = NULL WHERE id = ?").bind(user.id).run();
}

// A failed reply must not fail the webhook, or Telegram re-sends the same update.
const reply = (env, chat, text) => call(env, "sendMessage", { chat_id: chat, text }).catch((e) => console.error("telegram reply", e.message));

export async function hook(env, req) {
  if (!telegramEnabled(env)) return new Response("off", { status: 404 });
  if (!timingSafeEqual(req.headers.get("X-Telegram-Bot-Api-Secret-Token") || "", await hookSecret(env))) {
    return new Response("forbidden", { status: 403 });
  }
  const text = await req.text();
  if (text.length > 16384) return new Response("ok");
  let update;
  try { update = JSON.parse(text); } catch { return new Response("ok"); }
  const msg = update.message;
  if (!msg || !msg.chat || msg.chat.type !== "private" || typeof msg.text !== "string") return new Response("ok");
  const chat = String(msg.chat.id);
  const [cmd, code] = msg.text.trim().split(/\s+/);
  if (cmd === "/start" && code) {
    const user = await env.DB.prepare("SELECT id, username FROM users WHERE tg_code = ? AND tg_code_exp > ? AND disabled = 0").bind(code, now()).first();
    if (!user) {
      await reply(env, chat, "That link has expired. Open Settings on the site and press Connect Telegram again.");
    } else {
      await env.DB.prepare("UPDATE users SET tg_chat_id = ?, tg_code = NULL WHERE id = ?").bind(chat, user.id).run();
      await logEvent(env, { kind: "telegram", actor: user.username, detail: "connected" });
      await reply(env, chat, "Connected. You will get a message here when something you watch is back in stock. Send /stop to turn this off.");
    }
  } else if (cmd === "/stop") {
    await env.DB.prepare("UPDATE users SET tg_chat_id = NULL WHERE tg_chat_id = ?").bind(chat).run();
    await reply(env, chat, "Stopped. You will not get alerts here any more.");
  } else {
    await reply(env, chat, "To get alerts here, open Settings on the Back in Stock site and press Connect Telegram.");
  }
  return new Response("ok");
}

// Returns nothing on success; throws with a short reason otherwise.
export async function sendTelegram(env, user, alert) {
  const text = `${alert.title}\n${alert.message}${alert.url ? `\n${alert.url}` : ""}`;
  const r = await call(env, "sendMessage", { chat_id: user.tg_chat_id, text: text.slice(0, 4000) });
  if (r.data.ok) return;
  if (r.status === 403) {
    await env.DB.prepare("UPDATE users SET tg_chat_id = NULL WHERE id = ?").bind(user.id).run();
    throw new Error("the bot was blocked, reconnect in Settings");
  }
  throw new Error(`HTTP ${r.status} ${r.data.description || ""}`.trim());
}
