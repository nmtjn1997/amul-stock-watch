// Accounts and sessions.
//
// Passwords: PBKDF2-SHA256 over HMAC(PASSWORD_PEPPER, password), 10k iterations, 16-byte
// salt. 10k keeps a login inside the free plan's 10 ms CPU budget; the pepper is a Worker
// secret that never touches the database, so a leaked database alone cannot be cracked.
// Older hashes (plain 100k, no pepper) still verify and are rewritten on the next login.
// Sessions: a random token in an HttpOnly, Secure, SameSite=Lax __Host- cookie; only
// its SHA-256 is stored, so a leaked database cannot be replayed as a login.
// Google: standard OAuth code flow with PKCE and a signed state cookie. Enabled only
// when GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set.

import { logEvent } from "./log.js";
import {
  USERNAME_RE,
  allow,
  cleanName,
  peek,
  clientIp,
  cookieValue,
  fail,
  json,
  now,
  randomToken,
  randomTopic,
  sha256b64,
  timingSafeEqual,
} from "./util.js";

const COOKIE = "__Host-sid";
const SESSION_DAYS = 30;
const ITERATIONS = 10000;

const b64 = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)));

async function pbkdf2(material, salt, iterations) {
  const key = await crypto.subtle.importKey("raw", material, "PBKDF2", false, ["deriveBits"]);
  return b64(await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, key, 256));
}

async function peppered(env, password) {
  if (!env.PASSWORD_PEPPER) throw new Error("PASSWORD_PEPPER is not set");
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.PASSWORD_PEPPER), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(password)));
}

export async function hashPassword(env, password) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  return `pbkdf2p$${ITERATIONS}$${b64(salt)}$${await pbkdf2(await peppered(env, password), salt, ITERATIONS)}`;
}

async function verifyPassword(env, password, stored) {
  const [scheme, iter, saltB64, hash] = String(stored || "").split("$");
  if (!saltB64 || !hash) return false;
  const salt = Uint8Array.from(atob(saltB64), (c) => c.charCodeAt(0));
  if (scheme === "pbkdf2p") return timingSafeEqual(await pbkdf2(await peppered(env, password), salt, Number(iter)), hash);
  if (scheme === "pbkdf2") return timingSafeEqual(await pbkdf2(new TextEncoder().encode(password), salt, Number(iter)), hash);
  return false;
}

const needsRehash = (stored) => !String(stored || "").startsWith(`pbkdf2p$${ITERATIONS}$`);

// Same cost as a real check, so an unknown username is not faster to reject.
const DUMMY = `pbkdf2p$${ITERATIONS}$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=`;

function checkPassword(password, username) {
  if (typeof password !== "string" || password.length < 8) fail(400, "Use at least 8 characters for the password.");
  if (password.length > 128) fail(400, "That password is too long.");
  if (username && password.toLowerCase().includes(username)) fail(400, "The password should not contain your username.");
}

export async function currentUser(env, req) {
  const token = cookieValue(req, COOKIE);
  if (!token) return null;
  const user = await env.DB.prepare(
    `SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
     WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0`,
  )
    .bind(await sha256b64(token), now())
    .first();
  if (!user) return null;
  // "Last active" for the admin People list; one write per person per 5 minutes at most.
  if (!user.last_seen_at || now() - user.last_seen_at > 300) {
    user.last_seen_at = now();
    await env.DB.prepare("UPDATE users SET last_seen_at = ? WHERE id = ?").bind(user.last_seen_at, user.id).run();
  }
  return user;
}

async function startSession(env, userId) {
  const token = randomToken(32);
  const t = now();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM sessions WHERE user_id = ? AND expires_at <= ?").bind(userId, t),
    env.DB.prepare("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)").bind(
      await sha256b64(token), userId, t, t + SESSION_DAYS * 86400,
    ),
    env.DB.prepare("UPDATE users SET last_login_at = ? WHERE id = ?").bind(t, userId),
  ]);
  return `${COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${SESSION_DAYS * 86400}`;
}

const clearCookie = `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;

export async function signup(env, req, body) {
  const username = String(body.username || "").trim().toLowerCase();
  const password = body.password;
  if (!USERNAME_RE.test(username)) fail(400, "Usernames are 3 to 64 characters: letters, digits and . _ @ + - (an email address works). No spaces.");
  checkPassword(password, username);
  const ip = clientIp(req);
  await preAuthGate(env, ip);
  await verifyHuman(env, req, body);
  if (!(await allow(env.DB, `signup-ip:${ip}`, 3, 3600))) fail(429, "Too many new accounts from this network. Try again in an hour.");
  // Counted only when an account is really created, so failed attempts cannot hold it shut.
  if ((await peek(env.DB, "signup-all", 3600)) >= 30) fail(429, "Sign-ups are busy right now. Try again in a bit.");
  const max = Number(env.MAX_USERS || 100);
  const hash = await hashPassword(env, password);
  // One statement, so two sign-ups at once cannot both slip past the user cap.
  const res = await env.DB.prepare(
    `INSERT INTO users (username, pass_hash, display_name, ntfy_topic, created_at)
     SELECT ?1, ?2, ?1, ?3, ?4 WHERE (SELECT COUNT(*) FROM users) < ?5
     ON CONFLICT(username) DO NOTHING`,
  )
    .bind(username, hash, randomTopic(), now(), max)
    .run();
  if (!res.meta.changes) {
    const taken = await env.DB.prepare("SELECT 1 FROM users WHERE username = ?").bind(username).first();
    if (taken) fail(409, "That username is taken.");
    fail(403, `This service is full (${max} people). Ask the admin, or run your own copy from GitHub.`);
  }
  await allow(env.DB, "signup-all", 1000000, 3600);
  const user = await env.DB.prepare("SELECT id FROM users WHERE username = ?").bind(username).first();
  await logEvent(env, { kind: "signup", user: { id: user.id, username }, detail: "new account", req });
  return json({ ok: true }, 200, { "Set-Cookie": await startSession(env, user.id) });
}

// Guessing is limited three ways, none of which lets a stranger lock the owner out:
//   per network:            30 attempts per 15 minutes
//   per account + network:  8 attempts per 15 minutes (only that network is blocked)
//   per account, anywhere:  60 wrong passwords per hour, the backstop for many networks
// Each counter is one atomic upsert, so parallel guesses cannot slip past it.
export async function login(env, req, body) {
  const username = String(body.username || "").trim().toLowerCase();
  const password = String(body.password || "");
  const ip = clientIp(req);
  const generic = "Wrong username or password.";
  await preAuthGate(env, ip);
  if (!(await allow(env.DB, `login-ip:${ip}`, 30, 900))) fail(429, "Too many attempts from this network. Wait 15 minutes.");
  const user = USERNAME_RE.test(username)
    ? await env.DB.prepare("SELECT * FROM users WHERE username = ?").bind(username).first()
    : null;
  if (!user || !user.pass_hash) {
    await verifyPassword(env, password, DUMMY);
    fail(401, generic);
  }
  // Counted before the check, in one atomic statement, so parallel guesses from one
  // network cannot all slip through; a successful login clears it.
  const tooMany = "Too many wrong passwords. Wait a few minutes and try again.";
  if (!(await allow(env.DB, `login-fail:${user.id}:${ip}`, 8, 900))) {
    await logEvent(env, { level: "warn", kind: "login_blocked", user, detail: "too many attempts from one network", req });
    fail(429, tooMany);
  }
  if ((await peek(env.DB, `login-fail-all:${user.id}`, 3600)) >= 60) fail(429, tooMany);
  if (!(await verifyPassword(env, password, user.pass_hash))) {
    await allow(env.DB, `login-fail-all:${user.id}`, 60, 3600);
    await logEvent(env, { level: "warn", kind: "login_fail", user, detail: "wrong password", req });
    fail(401, generic);
  }
  if (user.disabled) {
    await logEvent(env, { level: "warn", kind: "login_fail", user, detail: "account disabled", req });
    fail(401, generic);
  }
  await logEvent(env, { kind: "login", user, detail: "password", req });
  await env.DB.prepare("DELETE FROM rate WHERE key = ?").bind(`login-fail:${user.id}:${ip}`).run();
  if (needsRehash(user.pass_hash)) {
    await env.DB.prepare("UPDATE users SET pass_hash = ? WHERE id = ?").bind(await hashPassword(env, password), user.id).run();
  }
  return json({ ok: true }, 200, { "Set-Cookie": await startSession(env, user.id) });
}

// Before any D1 write: the Workers rate limiter (no database cost) turns away floods of
// sign-up and login attempts from one network.
async function preAuthGate(env, ip) {
  if (!env.AUTH_LIMIT) return;
  const { success } = await env.AUTH_LIMIT.limit({ key: `auth:${ip}` });
  if (!success) fail(429, "Too many attempts from this network. Wait a minute.");
}

// Cloudflare Turnstile on sign-up, when TURNSTILE_SITE_KEY and TURNSTILE_SECRET are set.
// It stops scripted sign-ups, the one way to fill the 100 seats quickly.
async function verifyHuman(env, req, body) {
  if (!env.TURNSTILE_SECRET) return;
  const token = String(body.turnstile || "");
  if (!token) fail(400, "Please complete the check above the button.");
  const res = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    body: new URLSearchParams({ secret: env.TURNSTILE_SECRET, response: token, remoteip: req.headers.get("CF-Connecting-IP") || "" }),
  });
  const out = await res.json().catch(() => ({}));
  if (!out.success) fail(400, "The human check failed. Please try again.");
}

export async function logout(env, req) {
  const token = cookieValue(req, COOKIE);
  if (token) await env.DB.prepare("DELETE FROM sessions WHERE token_hash = ?").bind(await sha256b64(token)).run();
  return json({ ok: true }, 200, { "Set-Cookie": clearCookie });
}

export async function changePassword(env, req, user, body) {
  if (!user.pass_hash) fail(400, "This account signs in with Google.");
  if (!(await allow(env.DB, `pw-check:${user.id}`, 10, 900))) fail(429, "Too many tries. Wait 15 minutes.");
  if (!(await verifyPassword(env, String(body.current || ""), user.pass_hash))) fail(401, "The current password is wrong.");
  checkPassword(body.password, user.username);
  await logEvent(env, { kind: "password_change", user, detail: "other devices signed out", req });
  const keep = await sha256b64(cookieValue(req, COOKIE));
  await env.DB.batch([
    env.DB.prepare("UPDATE users SET pass_hash = ? WHERE id = ?").bind(await hashPassword(env, body.password), user.id),
    // Every other device is signed out.
    env.DB.prepare("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?").bind(user.id, keep),
  ]);
  return json({ ok: true });
}

export const ownerName = (env) => String(env.OWNER_USERNAME || "namitjain").toLowerCase();

export async function deleteAccount(env, user, body) {
  if (user.username === ownerName(env)) fail(403, "The owner account cannot be deleted.");
  if (user.pass_hash) {
    if (!(await allow(env.DB, `pw-check:${user.id}`, 10, 900))) fail(429, "Too many tries. Wait 15 minutes.");
    if (!(await verifyPassword(env, String(body.password || ""), user.pass_hash))) fail(401, "The password is wrong.");
  }
  if (user.role === "admin") {
    const admins = await env.DB.prepare("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = 0").first();
    if (admins.n <= 1) fail(400, "You are the only admin. Make someone else admin first.");
  }
  await env.DB.prepare("DELETE FROM users WHERE id = ?").bind(user.id).run();
  await logEvent(env, { kind: "account_delete", user, detail: "deleted own account" });
  return json({ ok: true }, 200, { "Set-Cookie": clearCookie });
}

// ---------------------------------------------------------------- Google sign-in

export const googleEnabled = (env) => Boolean(env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET);

async function hmac(env, text) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.SESSION_SECRET || env.GOOGLE_CLIENT_SECRET),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(text));
  return btoa(String.fromCharCode(...new Uint8Array(sig)));
}

async function pkceChallenge(verifier) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return btoa(String.fromCharCode(...new Uint8Array(d))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function googleStart(env, req) {
  if (!googleEnabled(env)) fail(404, "Google sign-in is not set up.");
  const origin = new URL(req.url).origin;
  const state = randomToken(16);
  const verifier = randomToken(32);
  const params = new URLSearchParams({
    client_id: env.GOOGLE_CLIENT_ID,
    redirect_uri: `${origin}/auth/google/callback`,
    response_type: "code",
    scope: "openid email profile",
    state,
    code_challenge: await pkceChallenge(verifier),
    code_challenge_method: "S256",
    prompt: "select_account",
  });
  const payload = `${state}.${verifier}`;
  const cookie = `__Host-g=${payload}.${await hmac(env, payload)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=600`;
  return new Response(null, { status: 302, headers: { Location: `https://accounts.google.com/o/oauth2/v2/auth?${params}`, "Set-Cookie": cookie } });
}

export async function googleCallback(env, req) {
  if (!googleEnabled(env)) fail(404, "Google sign-in is not set up.");
  const url = new URL(req.url);
  const [state, verifier, sig] = cookieValue(req, "__Host-g").split(".");
  // Only a short code goes in the URL; the page maps it to its own text, so a crafted
  // link cannot make the login page display arbitrary words.
  const back = (code) => new Response(null, {
    status: 302,
    headers: { Location: `/#/login?error=${code}`, "Set-Cookie": "__Host-g=; Path=/; Secure; Max-Age=0" },
  });
  if (!state || !timingSafeEqual(sig || "", await hmac(env, `${state}.${verifier}`)) || url.searchParams.get("state") !== state) {
    return back("expired");
  }
  const code = url.searchParams.get("code");
  if (!code) return back("cancelled");
  const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: env.GOOGLE_CLIENT_ID,
      client_secret: env.GOOGLE_CLIENT_SECRET,
      redirect_uri: `${url.origin}/auth/google/callback`,
      grant_type: "authorization_code",
      code_verifier: verifier,
    }),
  });
  const tokens = await tokenRes.json().catch(() => ({}));
  if (!tokens.id_token) return back("unverified");
  // The ID token came straight from Google's token endpoint over TLS, so its claims can
  // be read without re-verifying the signature (OpenID Connect Core 3.1.3.7).
  const claims = JSON.parse(atob(tokens.id_token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  if (claims.aud !== env.GOOGLE_CLIENT_ID || !claims.sub || claims.email_verified !== true) {
    return back("unverified");
  }
  let user = await env.DB.prepare("SELECT * FROM users WHERE google_sub = ?").bind(claims.sub).first();
  if (!user) {
    if (!(await allow(env.DB, `signup-ip:${clientIp(req)}`, 3, 3600))) return back("busy");
    const max = Number(env.MAX_USERS || 100);
    const admins = String(env.ADMIN_EMAILS || "").toLowerCase().split(",").map((s) => s.trim()).filter(Boolean);
    const role = admins.includes(String(claims.email).toLowerCase()) ? "admin" : "user";
    const res = await env.DB.prepare(
      `INSERT INTO users (google_sub, email, display_name, role, ntfy_topic, created_at)
       SELECT ?, ?, ?, ?, ?, ? WHERE (SELECT COUNT(*) FROM users) < ?`,
    )
      .bind(claims.sub, claims.email, cleanName(claims.given_name || claims.name) || "friend", role, randomTopic(), now(), max)
      .run();
    if (!res.meta.changes) return back("full");
    user = await env.DB.prepare("SELECT * FROM users WHERE google_sub = ?").bind(claims.sub).first();
  }
  if (user.disabled) return back("disabled");
  await logEvent(env, { kind: "login", user, detail: "google", req });
  const headers = new Headers({ Location: "/#/" });
  headers.append("Set-Cookie", await startSession(env, user.id));
  headers.append("Set-Cookie", "__Host-g=; Path=/; Secure; Max-Age=0");
  return new Response(null, { status: 302, headers });
}
