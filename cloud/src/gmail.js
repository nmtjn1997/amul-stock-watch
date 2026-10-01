// Email through the site's own Gmail account, with a gmail.send-only OAuth token
// (GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN; see scripts/gmail-auth.mjs).
// That token can send mail but never read it.

import { allow } from "./util.js";

export const emailEnabled = (env) => Boolean(env.GMAIL_CLIENT_ID && env.GMAIL_CLIENT_SECRET && env.GMAIL_REFRESH_TOKEN);

// Whole-site cap, well under Gmail's ~500 recipients a day for a regular account.
const DAILY_CAP = 150;

export const EMAIL_RE = /^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$/;

let cached = { token: "", exp: 0 }; // per isolate; an access token lives an hour

async function accessToken(env) {
  if (cached.token && Date.now() < cached.exp - 60000) return cached.token;
  const r = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: env.GMAIL_CLIENT_ID, client_secret: env.GMAIL_CLIENT_SECRET,
      refresh_token: env.GMAIL_REFRESH_TOKEN, grant_type: "refresh_token",
    }),
  });
  const d = await r.json().catch(() => ({}));
  if (!d.access_token) throw new Error(`Gmail sign-in failed (${d.error || r.status})`);
  cached = { token: d.access_token, exp: Date.now() + (d.expires_in || 3600) * 1000 };
  return cached.token;
}

const b64u = (s) => btoa(String.fromCharCode(...new TextEncoder().encode(s))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const header = (s) => `=?UTF-8?B?${btoa(String.fromCharCode(...new TextEncoder().encode(s)))}?=`;

export async function sendEmail(env, to, subject, text) {
  if (!EMAIL_RE.test(to)) throw new Error("bad address");
  if (!(await allow(env.DB, "email-day", DAILY_CAP, 86400))) throw new Error("the site's daily email limit is reached");
  const mime = [
    `To: ${to}`,
    `Subject: ${header(subject.replace(/[\r\n]/g, " "))}`,
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=UTF-8",
    "Content-Transfer-Encoding: 8bit",
    "",
    text,
  ].join("\r\n");
  const r = await fetch("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", {
    method: "POST",
    headers: { Authorization: `Bearer ${await accessToken(env)}`, "Content-Type": "application/json" },
    body: JSON.stringify({ raw: b64u(mime) }),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(`Gmail HTTP ${r.status} ${d.error?.message || ""}`.trim());
  }
  await r.body?.cancel();
}
