// Back in Stock: Amul Stock Watch, hosted on Cloudflare Workers.
//   fetch     -> the web app (public/) and its JSON API (/api/*), plus Google sign-in
//   scheduled -> the every-minute stock poll (src/poll.js)

import * as api from "./api.js";
import { currentUser, googleCallback, googleStart, login, logout, signup } from "./auth.js";
import { runPoll } from "./poll.js";
import { HttpError, json, secure } from "./util.js";

const MAX_BODY = 8 * 1024;

async function readBody(req) {
  const type = (req.headers.get("Content-Type") || "").split(";")[0].trim();
  // Writes must be JSON from this site: a cross-site form or no-cors fetch cannot send
  // that without a CORS preflight, which this API never answers.
  if (type !== "application/json") throw new HttpError(415, "Send JSON.");
  const origin = req.headers.get("Origin");
  if (origin && origin !== new URL(req.url).origin) throw new HttpError(403, "Cross-site request refused.");
  if (Number(req.headers.get("Content-Length") || 0) > MAX_BODY) throw new HttpError(413, "Request too large.");
  const text = await req.text();
  if (text.length > MAX_BODY) throw new HttpError(413, "Request too large.");
  try {
    const body = text ? JSON.parse(text) : {};
    if (typeof body !== "object" || Array.isArray(body) || body === null) throw new Error();
    return body;
  } catch {
    throw new HttpError(400, "Invalid JSON.");
  }
}

async function route(req, env) {
  const url = new URL(req.url);
  const path = url.pathname;
  const method = req.method;

  if (path === "/auth/google/start" && method === "GET") return googleStart(env, req);
  if (path === "/auth/google/callback" && method === "GET") return googleCallback(env, req);
  if (path === "/healthz") return json({ ok: true });

  if (!path.startsWith("/api/")) {
    // Static app; unknown paths fall back to the single page.
    const res = await env.ASSETS.fetch(req);
    return res.status === 404 ? env.ASSETS.fetch(new Request(new URL("/", url), req)) : res;
  }

  if (method !== "GET" && method !== "POST") throw new HttpError(405, "Method not allowed.");
  const body = method === "POST" ? await readBody(req) : {};

  if (path === "/api/signup" && method === "POST") return signup(env, req, body);
  if (path === "/api/login" && method === "POST") return login(env, req, body);
  if (path === "/api/logout" && method === "POST") return logout(env, req);
  if (path === "/api/config" && method === "GET") {
    return json({
      google: Boolean(env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET),
      turnstile: env.TURNSTILE_SECRET ? env.TURNSTILE_SITE_KEY || null : null,
      vapid: env.VAPID_PRIVATE_JWK ? env.VAPID_PUBLIC_KEY || null : null,
      max_watches: Number(env.MAX_WATCHES_PER_USER || 10),
    });
  }

  const user = await currentUser(env, req);
  if (!user) throw new HttpError(401, "Please log in.");

  if (path === "/api/me" && method === "GET") return api.me(env, user);
  let m = path.match(/^\/api\/pincode\/(\d{6})$/);
  if (m && method === "GET") return api.checkPincode(env, user, m[1]);
  if (path === "/api/watches" && method === "POST") return api.addWatches(env, user, body);
  m = path.match(/^\/api\/watches\/(\d+)$/);
  if (m && method === "POST") return body.delete ? api.deleteWatch(env, user, m[1]) : api.updateWatch(env, user, m[1], body);
  if (path === "/api/settings" && method === "POST") return api.updateSettings(env, user, body);
  if (path === "/api/test" && method === "POST") return api.sendTest(env, user);
  if (path === "/api/push/subscribe" && method === "POST") return api.addDevice(env, user, body);
  if (path === "/api/push/remove" && method === "POST") return api.removeDevice(env, user, body);
  if (path === "/api/history" && method === "GET") return api.history(env, user);
  if (path === "/api/password" && method === "POST") return api.password(env, req, user, body);
  if (path === "/api/account/delete" && method === "POST") return api.removeAccount(env, user, body);
  if (path === "/api/admin" && method === "GET") return api.adminOverview(env, user);
  m = path.match(/^\/api\/admin\/users\/(\d+)$/);
  if (m && method === "POST") return api.adminUser(env, user, m[1], body);
  if (path === "/api/admin/products" && method === "POST") return api.adminProduct(env, user, body);
  throw new HttpError(404, "Not found.");
}

export default {
  async fetch(req, env) {
    try {
      return secure(await route(req, env));
    } catch (e) {
      if (e instanceof HttpError) return secure(json({ error: e.message }, e.status));
      console.error("unhandled", e && e.stack ? e.stack : e);
      return secure(json({ error: "Something went wrong. Please try again." }, 500));
    }
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(
      runPoll(env).then(
        (s) => console.log("poll", JSON.stringify({ pins: s.pincodes, checks: s.checks, alerts: s.alerts, errors: s.errors.length })),
        (e) => console.error("poll failed", e && e.stack ? e.stack : e),
      ),
    );
  },
};
