// Monitoring events. Written to D1 (the admin Monitor panel) and to console, which
// Cloudflare Workers Logs keeps for the dashboard. Never throws into the caller.

import { clientIp, now } from "./util.js";

async function netHash(req) {
  if (!req) return null;
  const day = Math.floor(Date.now() / 86400000);
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${day}:${clientIp(req)}`));
  return [...new Uint8Array(buf).slice(0, 4)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function logEvent(env, { level = "info", kind, user = null, actor = null, detail = "", req = null }) {
  const who = actor || (user ? user.username || user.email || user.display_name : null);
  const text = String(detail).slice(0, 500);
  console.log(JSON.stringify({ event: kind, level, user: user ? user.id : null, who, detail: text }));
  try {
    await env.DB.prepare("INSERT INTO events (ts, level, kind, user_id, actor, detail, net) VALUES (?, ?, ?, ?, ?, ?, ?)")
      .bind(now(), level, kind, user ? user.id : null, who, text, await netHash(req)).run();
  } catch (e) {
    console.error("event log failed", e && e.message);
  }
}
