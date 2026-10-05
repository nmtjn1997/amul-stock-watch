// Small shared helpers: responses, security headers, crypto, validation.

export const now = () => Math.floor(Date.now() / 1000);

const CSP = [
  "default-src 'self'",
  "script-src 'self' https://challenges.cloudflare.com",
  "frame-src https://challenges.cloudflare.com",
  "style-src 'self'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "form-action 'self' https://accounts.google.com",
  "frame-ancestors 'none'",
  "base-uri 'none'",
  "object-src 'none'",
  "worker-src 'self'",
  "manifest-src 'self'",
].join("; ");

// Every response, API or page, carries these.
export function secure(res) {
  const r = new Response(res.body, res);
  r.headers.set("Content-Security-Policy", CSP);
  r.headers.set("X-Content-Type-Options", "nosniff");
  r.headers.set("X-Frame-Options", "DENY");
  r.headers.set("Referrer-Policy", "same-origin");
  r.headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()");
  r.headers.set("Strict-Transport-Security", "max-age=31536000; includeSubDomains");
  r.headers.set("Cross-Origin-Opener-Policy", "same-origin");
  return r;
}

export function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...extra },
  });
}

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export const fail = (status, message) => {
  throw new HttpError(status, message);
};

export function randomToken(bytes = 32) {
  const b = crypto.getRandomValues(new Uint8Array(bytes));
  return btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function randomTopic() {
  const alphabet = "abcdefghijkmnpqrstuvwxyz23456789";
  const b = crypto.getRandomValues(new Uint8Array(18));
  return "bis-" + [...b].map((x) => alphabet[x % alphabet.length]).join("");
}

export async function sha256b64(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return btoa(String.fromCharCode(...new Uint8Array(buf)));
}

export function timingSafeEqual(a, b) {
  const ea = new TextEncoder().encode(a);
  const eb = new TextEncoder().encode(b);
  let diff = ea.length ^ eb.length;
  for (let i = 0; i < Math.max(ea.length, eb.length); i++) diff |= (ea[i] || 0) ^ (eb[i] || 0);
  return diff === 0;
}

export function cookieValue(req, name) {
  for (const part of (req.headers.get("Cookie") || "").split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return v.join("=");
  }
  return "";
}

// Lowercase letters, digits and . _ @ + - so an email address works as a username; no spaces.
export const USERNAME_RE = /^[a-z0-9._@+-]{3,64}$/;
export const PINCODE_RE = /^[1-9][0-9]{5}$/;
export const ALIAS_RE = /^[a-z0-9-]{3,120}$/;

// Only these hosts can receive webhooks, so a user cannot point the service at an
// arbitrary URL (spam relay, probing internal addresses).
export function validWebhook(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== "https:" || u.username || u.password || u.port) return false;
    if ((u.hostname === "discord.com" || u.hostname === "discordapp.com") && u.pathname.startsWith("/api/webhooks/")) return true;
    if (u.hostname === "hooks.slack.com" && u.pathname.startsWith("/services/")) return true;
    return false;
  } catch {
    return false;
  }
}

// The network a request comes from. IPv6 users get a whole /64 each, so keying on the
// full address would let one person rotate through billions of "networks".
export function clientIp(req) {
  const ip = req.headers.get("CF-Connecting-IP") || "0.0.0.0";
  if (!ip.includes(":")) return ip;
  const [head] = ip.split("::");
  const groups = head.split(":").filter(Boolean);
  return (groups.slice(0, 4).join(":") || "0") + "::/64";
}

// Read a counter without adding to it.
export async function peek(db, key, windowSec) {
  const t = now();
  const row = await db.prepare("SELECT count FROM rate WHERE key = ? AND window_start = ?").bind(key, t - (t % windowSec)).first();
  return row ? row.count : 0;
}

// Names are shown to the admin: drop control and bidi characters so one account cannot
// display as another.
export function cleanName(s, max = 40) {
  return String(s || "").replace(/[\p{C}]/gu, "").trim().slice(0, max);
}

// Fixed-window counter in D1. Returns true while under the limit.
export async function allow(db, key, limit, windowSec) {
  const t = now();
  const start = t - (t % windowSec);
  const row = await db
    .prepare(
      `INSERT INTO rate (key, count, window_start) VALUES (?1, 1, ?2)
       ON CONFLICT(key) DO UPDATE SET
         count = CASE WHEN rate.window_start = ?2 THEN rate.count + 1 ELSE 1 END,
         window_start = ?2
       RETURNING count`,
    )
    .bind(key, start)
    .first();
  return (row?.count ?? 1) <= limit;
}
