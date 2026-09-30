// JSON API. Access control is one rule, applied in every query: a user's rows are
// selected and changed only `WHERE user_id = <signed-in user>`. Admin routes check
// the role on the server; the UI hiding a tab is never the protection.

import { AmulClient } from "./amul.js";
import { changePassword, deleteAccount, googleEnabled, ownerName } from "./auth.js";
import { logEvent } from "./log.js";
import { deliver } from "./notify.js";
import { validSubscription } from "./webpush.js";
import { zoneFor } from "./poll.js";
import { ALIAS_RE, PINCODE_RE, allow, cleanName, fail, json, now, randomTopic, validWebhook } from "./util.js";

const maxWatches = (env) => Number(env.MAX_WATCHES_PER_USER || 10);
// Each distinct pincode costs the shared poller two extra requests per round, so one
// person cannot slow everyone down by spreading alerts over many pincodes.
const maxPincodes = (env) => Number(env.MAX_PINCODES_PER_USER || 3);

function publicUser(u, env) {
  return {
    id: u.id,
    name: u.display_name,
    username: u.username,
    email: u.email,
    role: u.role,
    ntfy_topic: u.ntfy_topic,
    ntfy_server: env.NTFY_SERVER || "https://ntfy.sh",
    webhook: u.webhook_url ? (u.webhook_url.includes("slack") ? "Slack" : "Discord") : null,
    ntfy_on: Boolean(u.ntfy_on),
    telegram: env.TELEGRAM_BOT_TOKEN ? Boolean(u.tg_chat_id) : null,
    has_password: Boolean(u.pass_hash),
    max_watches: maxWatches(env),
    max_pincodes: maxPincodes(env),
  };
}

async function myWatches(env, user) {
  return (
    await env.DB.prepare(
      `SELECT w.id, w.pincode, w.alias, w.enabled, p.label, p.enabled AS product_enabled,
              s.in_stock, s.qty, s.price, s.changed_at, z.store, z.valid, p.sort
       FROM watches w JOIN products p ON p.alias = w.alias
       LEFT JOIN stock s ON s.pincode = w.pincode AND s.alias = w.alias
       LEFT JOIN zones z ON z.pincode = w.pincode
       WHERE w.user_id = ? ORDER BY w.pincode, p.sort, p.label`,
    ).bind(user.id).all()
  ).results;
}

async function lastRun(env) {
  const row = await env.DB.prepare("SELECT value FROM meta WHERE key = 'last_run'").first();
  try {
    return row ? JSON.parse(row.value) : null;
  } catch {
    return null;
  }
}

// ------------------------------------------------------------------ user routes

export async function me(env, user) {
  const [products, subs, run, watches] = await Promise.all([
    env.DB.prepare("SELECT alias, label FROM products WHERE enabled = 1 ORDER BY sort, label").all().then((r) => r.results),
    env.DB.prepare("SELECT id, label, created_at, endpoint FROM push_subs WHERE user_id = ? ORDER BY created_at").bind(user.id).all().then((r) => r.results),
    lastRun(env),
    myWatches(env, user),
  ]);
  const devices = subs.map((d) => ({ id: d.id, label: d.label, created_at: d.created_at, endpoint_hash: d.endpoint.slice(-24) }));
  return json({
    user: publicUser(user, env),
    devices,
    watches,
    products,
    google: googleEnabled(env),
    last_check: run ? run.ts : null,
  });
}

// Is this a pincode Amul delivers to? Cached a day; a lookup costs two shop requests,
// so it is rate limited per person.
export async function checkPincode(env, user, pincode) {
  if (!PINCODE_RE.test(pincode)) fail(400, "A pincode is 6 digits.");
  const cached = await env.DB.prepare("SELECT valid, store FROM zones WHERE pincode = ?").bind(pincode).first();
  if (cached) return json({ pincode, valid: Boolean(cached.valid), region: cached.store });
  if (!(await allow(env.DB, `pin-check:${user.id}`, 20, 3600))) fail(429, "Too many pincode checks. Try again in an hour.");
  const budget = { left: 4 };
  const client = new AmulClient(env.DB, budget);
  let zone;
  try {
    zone = await zoneFor(env, client, pincode);
  } catch (e) {
    await logEvent(env, { level: "error", kind: "pincode_check", user, detail: `${pincode}: ${e.message}` });
    fail(503, "Could not reach Amul just now. Try again in a minute.");
  }
  await logEvent(env, { kind: "pincode_check", user, detail: `${pincode}: ${zone.valid ? zone.store : "not served"} (${4 - budget.left} Amul requests)` });
  return json({ pincode, valid: Boolean(zone.valid), region: zone.store });
}

export async function addWatches(env, user, body) {
  const pincode = String(body.pincode || "").trim();
  if (!PINCODE_RE.test(pincode)) fail(400, "A pincode is 6 digits.");
  const aliases = [...new Set([].concat(body.products || []).map(String))];
  if (!aliases.length) fail(400, "Pick at least one product.");
  if (!(await allow(env.DB, `write:${user.id}`, 60, 60))) fail(429, "Slow down a little.");
  // The pincode must have passed the (rate-limited) delivery check first.
  const checked = JSON.parse(await (await checkPincode(env, user, pincode)).text());
  if (!checked.valid) fail(400, "Amul does not deliver to that pincode.");
  const known = new Set((await env.DB.prepare("SELECT alias FROM products WHERE enabled = 1").all()).results.map((r) => r.alias));
  for (const a of aliases) if (!known.has(a)) fail(400, "One of those products is not available.");
  const max = maxWatches(env);
  const mine = (await env.DB.prepare("SELECT pincode, alias FROM watches WHERE user_id = ?").bind(user.id).all()).results;
  const newOnes = aliases.filter((a) => !mine.some((w) => w.pincode === pincode && w.alias === a));
  const pins = new Set(mine.map((w) => w.pincode));
  if (!pins.has(pincode) && pins.size >= maxPincodes(env)) {
    fail(400, `You can watch up to ${maxPincodes(env)} pincodes. Remove the alerts for one to add another.`);
  }
  if (mine.length + newOnes.length > max) {
    fail(400, `That would make ${mine.length + newOnes.length} alerts; the limit is ${max}. You have ${max - mine.length} left.`);
  }
  let added = 0;
  for (const alias of aliases) {
    const existing = await env.DB.prepare("SELECT id FROM watches WHERE user_id = ? AND pincode = ? AND alias = ?")
      .bind(user.id, pincode, alias).first();
    if (existing) {
      await env.DB.prepare("UPDATE watches SET enabled = 1 WHERE id = ?").bind(existing.id).run();
      continue;
    }
    // The count check and the insert are one statement, so parallel requests cannot pass the cap.
    const res = await env.DB.prepare(
      `INSERT INTO watches (user_id, pincode, alias, created_at)
       SELECT ?1, ?2, ?3, ?4 WHERE (SELECT COUNT(*) FROM watches WHERE user_id = ?1) < ?5
       ON CONFLICT(user_id, pincode, alias) DO NOTHING`,
    ).bind(user.id, pincode, alias, now(), max).run();
    if (!res.meta.changes) fail(400, `You can have up to ${max} alerts. Remove one to add another.`);
    added++;
  }
  await logEvent(env, { kind: "watch_add", user, detail: `${pincode}: ${aliases.join(", ")}` });
  return json({ ok: true, added });
}

export async function updateWatch(env, user, id, body) {
  const res = await env.DB.prepare("UPDATE watches SET enabled = ? WHERE id = ? AND user_id = ?")
    .bind(body.enabled ? 1 : 0, Number(id), user.id).run();
  if (!res.meta.changes) fail(404, "No such alert.");
  await logEvent(env, { kind: "watch_toggle", user, detail: `alert ${id} ${body.enabled ? "on" : "paused"}` });
  return json({ ok: true });
}

export async function deleteWatch(env, user, id) {
  const res = await env.DB.prepare("DELETE FROM watches WHERE id = ? AND user_id = ?").bind(Number(id), user.id).run();
  if (!res.meta.changes) fail(404, "No such alert.");
  await logEvent(env, { kind: "watch_delete", user, detail: `alert ${id}` });
  return json({ ok: true });
}

export async function updateSettings(env, user, body) {
  if (!(await allow(env.DB, `write:${user.id}`, 60, 60))) fail(429, "Slow down a little.");
  if ("webhook" in body) {
    const url = String(body.webhook || "").trim();
    if (url && !validWebhook(url)) fail(400, "Paste a Discord or Slack incoming webhook URL (https://discord.com/api/webhooks/... or https://hooks.slack.com/...).");
    await env.DB.prepare("UPDATE users SET webhook_url = ? WHERE id = ?").bind(url || null, user.id).run();
  }
  if ("ntfy_on" in body) {
    await env.DB.prepare("UPDATE users SET ntfy_on = ? WHERE id = ?").bind(body.ntfy_on ? 1 : 0, user.id).run();
  }
  if (body.new_topic) {
    await env.DB.prepare("UPDATE users SET ntfy_topic = ? WHERE id = ?").bind(randomTopic(), user.id).run();
  }
  if (typeof body.name === "string") {
    const name = cleanName(body.name);
    if (!name) fail(400, "The name cannot be empty.");
    await env.DB.prepare("UPDATE users SET display_name = ? WHERE id = ?").bind(name, user.id).run();
  }
  await logEvent(env, { kind: "settings", user, detail: Object.keys(body).join(", ") });
  return json({ ok: true });
}

// A device (browser or home-screen app) that turned on notifications.
export async function addDevice(env, user, body) {
  const sub = { endpoint: String(body.endpoint || ""), p256dh: String(body.p256dh || ""), auth: String(body.auth || "") };
  if (!validSubscription(sub)) fail(400, "This browser gave an unexpected push address. Try another browser.");
  if (!(await allow(env.DB, `write:${user.id}`, 60, 60))) fail(429, "Slow down a little.");
  const label = String(body.label || "This device").replace(/[\p{C}]/gu, "").slice(0, 40);
  const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM push_subs WHERE user_id = ?").bind(user.id).first();
  const mine = await env.DB.prepare("SELECT user_id FROM push_subs WHERE endpoint = ?").bind(sub.endpoint).first();
  if (!mine && count.n >= 5) fail(400, "You can have notifications on up to 5 devices. Remove one first.");
  // The same browser signing in as someone else moves the subscription to them.
  await env.DB.prepare(
    `INSERT INTO push_subs (user_id, endpoint, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth, label = excluded.label`,
  ).bind(user.id, sub.endpoint, sub.p256dh, sub.auth, label, now()).run();
  await logEvent(env, { kind: "device_add", user, detail: `${label} via ${new URL(sub.endpoint).hostname}` });
  return json({ ok: true });
}

export async function removeDevice(env, user, body) {
  const res = body.endpoint
    ? await env.DB.prepare("DELETE FROM push_subs WHERE endpoint = ? AND user_id = ?").bind(String(body.endpoint), user.id).run()
    : await env.DB.prepare("DELETE FROM push_subs WHERE id = ? AND user_id = ?").bind(Number(body.id), user.id).run();
  if (res.meta.changes) await logEvent(env, { kind: "device_remove", user, detail: "notifications turned off on a device" });
  return json({ ok: true, removed: res.meta.changes });
}

export async function sendTest(env, user) {
  if (!(await allow(env.DB, `test:${user.id}`, 5, 3600))) fail(429, "That is 5 test messages this hour. Try again later.");
  const res = await deliver(env, user, {
    kind: "test",
    title: "Test from Back in Stock",
    message: "It works. You will get a message like this when something you watch comes back.",
    url: "",
  });
  await env.DB.prepare("INSERT INTO alert_log (user_id, ts, kind, product, pincodes, result) VALUES (?, ?, 'test', 'test', '', ?)")
    .bind(user.id, now(), res.detail).run();
  await logEvent(env, { level: res.ok ? "info" : "warn", kind: "test_sent", user, detail: res.detail });
  return json({ ok: res.ok, detail: res.detail, channels: res.channels });
}

export async function history(env, user) {
  const rows = (await env.DB.prepare(
    "SELECT ts, kind, product, pincodes, result FROM alert_log WHERE user_id = ? ORDER BY ts DESC LIMIT 30",
  ).bind(user.id).all()).results;
  return json({ items: rows });
}

export const password = (env, req, user, body) => changePassword(env, req, user, body);
export const removeAccount = (env, user, body) => deleteAccount(env, user, body);

// ------------------------------------------------------------------ admin routes

function requireAdmin(user) {
  if (user.role !== "admin") fail(403, "Admins only.");
}

export async function adminOverview(env, user) {
  requireAdmin(user);
  const db = env.DB;
  const [users, watches, pairs, alerts24] = await db.batch([
    db.prepare("SELECT id, username, email, display_name, role, disabled, created_at, last_login_at, (SELECT COUNT(*) FROM watches w WHERE w.user_id = users.id) AS watches FROM users ORDER BY created_at DESC"),
    db.prepare("SELECT COUNT(*) AS n FROM watches"),
    db.prepare("SELECT COUNT(*) AS n FROM (SELECT DISTINCT pincode, alias FROM watches WHERE enabled = 1)"),
    db.prepare("SELECT COUNT(*) AS n FROM alert_log WHERE kind = 'stock' AND ts > ?").bind(now() - 86400),
  ]);
  const products = (await db.prepare(
    "SELECT p.alias, p.label, p.enabled, p.sort, (SELECT COUNT(*) FROM watches w WHERE w.alias = p.alias) AS watches FROM products p ORDER BY p.sort, p.label",
  ).all()).results;
  const recent = (await db.prepare(
    "SELECT a.ts, a.kind, a.product, a.pincodes, a.result, u.display_name FROM alert_log a JOIN users u ON u.id = a.user_id ORDER BY a.ts DESC LIMIT 25",
  ).all()).results;
  return json({
    owner: ownerName(env),
    users: users.results,
    counts: { users: users.results.length, max_users: Number(env.MAX_USERS || 100), watches: watches.results[0].n, pairs: pairs.results[0].n, alerts_24h: alerts24.results[0].n },
    products,
    recent,
    last_run: await lastRun(env),
  });
}

export async function adminUser(env, user, id, body) {
  requireAdmin(user);
  const target = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(Number(id)).first();
  if (!target) fail(404, "No such user.");
  const removing = Boolean(body.disabled || body.role === "user" || body.delete);
  // The owner account is fixed: no admin, including the owner, can remove it.
  if (target.username === ownerName(env) && removing) fail(403, "The owner account cannot be disabled, demoted or deleted.");
  if (target.id === user.id && removing) fail(400, "You cannot lock yourself out.");
  // Only removing an *active* admin can leave the service with none.
  if (target.role === "admin" && !target.disabled && removing) {
    const admins = await env.DB.prepare("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = 0").first();
    if (admins.n <= 1) fail(400, "That is the last active admin.");
  }
  const who = target.username || target.email || target.display_name;
  if (body.delete) {
    await env.DB.prepare("DELETE FROM users WHERE id = ?").bind(target.id).run();
    await logEvent(env, { kind: "admin", user, detail: `deleted ${who}` });
    return json({ ok: true });
  }
  if ("disabled" in body) {
    await env.DB.batch([
      env.DB.prepare("UPDATE users SET disabled = ? WHERE id = ?").bind(body.disabled ? 1 : 0, target.id),
      env.DB.prepare("DELETE FROM sessions WHERE user_id = ?").bind(body.disabled ? target.id : -1),
    ]);
  }
  if (body.role === "admin" || body.role === "user") {
    await env.DB.prepare("UPDATE users SET role = ? WHERE id = ?").bind(body.role, target.id).run();
  }
  await logEvent(env, { kind: "admin", user, detail: `${who}: ${"disabled" in body ? (body.disabled ? "disabled" : "enabled") : `role ${body.role}`}` });
  return json({ ok: true });
}

export async function adminProduct(env, user, body) {
  requireAdmin(user);
  let alias = String(body.alias || "").trim().split(/[?#]/)[0].replace(/\/$/, "");
  alias = alias.split("/product/").pop().toLowerCase();
  if (!ALIAS_RE.test(alias)) fail(400, "Paste the product URL from shop.amul.com.");
  if (body.remove) {
    await env.DB.prepare("UPDATE products SET enabled = 0 WHERE alias = ?").bind(alias).run();
    await logEvent(env, { kind: "admin", user, detail: `hid product ${alias}` });
    return json({ ok: true });
  }
  if (body.move === "up" || body.move === "down") {
    // Renumber in the current order, then swap with the neighbour: stable even when
    // several products share a sort value.
    const all = (await env.DB.prepare("SELECT alias FROM products ORDER BY sort, label").all()).results.map((r) => r.alias);
    const i = all.indexOf(alias);
    const j = body.move === "up" ? i - 1 : i + 1;
    if (i < 0 || j < 0 || j >= all.length) return json({ ok: true });
    [all[i], all[j]] = [all[j], all[i]];
    await env.DB.batch(all.map((a, n) => env.DB.prepare("UPDATE products SET sort = ? WHERE alias = ?").bind(n + 1, a)));
    return json({ ok: true });
  }
  const label = String(body.label || "").trim().slice(0, 80) || alias.replace(/-/g, " ");
  await env.DB.prepare(
    `INSERT INTO products (alias, label, enabled, created_at, sort) VALUES (?, ?, 1, ?, 1000)
     ON CONFLICT(alias) DO UPDATE SET label = excluded.label, enabled = 1`,
  ).bind(alias, label, now()).run();
  await logEvent(env, { kind: "admin", user, detail: `added or showed product ${alias}` });
  return json({ ok: true, alias });
}

// ------------------------------------------------------------------ monitoring

export async function adminMonitor(env, user) {
  requireAdmin(user);
  const db = env.DB;
  const t = now();
  const sums = (since) => db.prepare(
    `SELECT COUNT(*) AS runs, COALESCE(SUM(amul_requests),0) AS amul, COALESCE(SUM(checks),0) AS checks,
            COALESCE(SUM(alerts),0) AS alerts, COALESCE(SUM(notify_requests),0) AS notify,
            COALESCE(SUM(CASE WHEN errors != '[]' THEN 1 ELSE 0 END),0) AS error_runs,
            COALESCE(ROUND(AVG(ms)),0) AS avg_ms, COALESCE(MAX(ms),0) AS max_ms, COALESCE(MAX(units_total),0) AS max_units, MIN(ts) AS first_ts
     FROM runs WHERE ts > ?`).bind(since);
  const [h1, h24, hourly, daily, recent, kinds, active, last] = await db.batch([
    sums(t - 3600),
    sums(t - 86400),
    db.prepare(
      `SELECT ts / 3600 AS hour, SUM(amul_requests) AS amul, SUM(checks) AS checks, SUM(alerts) AS alerts,
              SUM(CASE WHEN errors != '[]' THEN 1 ELSE 0 END) AS error_runs, COUNT(*) AS runs
       FROM runs WHERE ts > ? GROUP BY hour ORDER BY hour`).bind(t - 86400),
    db.prepare(
      `SELECT ts / 86400 AS day, SUM(amul_requests) AS amul, SUM(alerts) AS alerts, COUNT(*) AS runs
       FROM runs GROUP BY day ORDER BY day`),
    db.prepare("SELECT * FROM runs ORDER BY ts DESC LIMIT 40"),
    db.prepare("SELECT kind, level, COUNT(*) AS n FROM events WHERE ts > ? GROUP BY kind, level").bind(t - 86400),
    db.prepare("SELECT COUNT(DISTINCT user_id) AS n FROM events WHERE ts > ? AND user_id IS NOT NULL AND kind NOT IN ('login_fail','login_blocked','alert_sent','alert_failed')").bind(t - 86400),
    db.prepare("SELECT value FROM meta WHERE key = 'last_run'"),
  ]);
  let lastRun = null;
  try { lastRun = last.results[0] ? JSON.parse(last.results[0].value) : null; } catch { lastRun = null; }
  const age = lastRun ? t - lastRun.ts : null;
  const recentRuns = recent.results;
  const failing = recentRuns.slice(0, 3).length === 3 && recentRuns.slice(0, 3).every((r) => r.errors !== "[]" && !r.checks && r.units_total > 0);
  const status = age === null ? "unknown" : age > 300 ? "down" : failing ? "failing" : age > 150 ? "late" : "ok";
  const byKind = {};
  for (const k of kinds.results) byKind[k.kind] = (byKind[k.kind] || 0) + k.n;
  const errors24 = kinds.results.filter((k) => k.level === "error").reduce((n, k) => n + k.n, 0);
  return json({
    now: t,
    health: { status, last_run_age: age, last_run: lastRun },
    last_hour: h1.results[0],
    last_day: h24.results[0],
    hourly: hourly.results,
    daily: daily.results,
    runs: recentRuns.map((r) => ({ ...r, errors: JSON.parse(r.errors || "[]") })),
    events_24h: byKind,
    errors_24h: errors24,
    active_users_24h: active.results[0].n,
    limits: {
      amul_per_run: 46,
      runs_per_day: 1440,
      max_users: Number(env.MAX_USERS || 100),
    },
    dashboard: "https://dash.cloudflare.com/?to=/:account/workers/services/view/amul/production/observability/logs",
  });
}

const EVENT_GROUPS = {
  errors: "level IN ('warn','error')",
  alerts: "kind IN ('alert_sent','alert_failed','test_sent')",
  signins: "kind IN ('signup','login','login_fail','login_blocked','password_change','account_delete')",
  changes: "kind IN ('watch_add','watch_delete','watch_toggle','settings','device_add','device_remove','pincode_check')",
  admin: "kind = 'admin'",
  system: "kind IN ('poll_error','error')",
};

export async function adminEvents(env, user, q) {
  requireAdmin(user);
  const where = [];
  const binds = [];
  if (q.group && EVENT_GROUPS[q.group]) where.push(EVENT_GROUPS[q.group]);
  if (q.search) {
    where.push("(actor LIKE ? OR detail LIKE ? OR kind LIKE ?)");
    const like = `%${String(q.search).slice(0, 60).replace(/[%_]/g, "")}%`;
    binds.push(like, like, like);
  }
  if (q.before) {
    where.push("id < ?");
    binds.push(Number(q.before));
  }
  const rows = (await env.DB.prepare(
    `SELECT id, ts, level, kind, user_id, actor, detail, net FROM events ${where.length ? "WHERE " + where.join(" AND ") : ""} ORDER BY id DESC LIMIT 50`,
  ).bind(...binds).all()).results;
  return json({ items: rows, more: rows.length === 50 });
}
