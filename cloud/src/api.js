// JSON API. Access control is one rule, applied in every query: a user's rows are
// selected and changed only `WHERE user_id = <signed-in user>`. Admin routes check
// the role on the server; the UI hiding a tab is never the protection.

import { AmulClient } from "./amul.js";
import { changePassword, deleteAccount, googleEnabled } from "./auth.js";
import { deliver } from "./notify.js";
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
    has_password: Boolean(u.pass_hash),
    max_watches: maxWatches(env),
    max_pincodes: maxPincodes(env),
  };
}

async function myWatches(env, user) {
  return (
    await env.DB.prepare(
      `SELECT w.id, w.pincode, w.alias, w.enabled, p.label, p.enabled AS product_enabled,
              s.in_stock, s.qty, s.price, s.changed_at, z.store, z.valid
       FROM watches w JOIN products p ON p.alias = w.alias
       LEFT JOIN stock s ON s.pincode = w.pincode AND s.alias = w.alias
       LEFT JOIN zones z ON z.pincode = w.pincode
       WHERE w.user_id = ? ORDER BY w.pincode, p.label`,
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
  const products = (await env.DB.prepare("SELECT alias, label FROM products WHERE enabled = 1 ORDER BY label").all()).results;
  const run = await lastRun(env);
  return json({
    user: publicUser(user, env),
    watches: await myWatches(env, user),
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
  const client = new AmulClient(env.DB, { left: 4 });
  let zone;
  try {
    zone = await zoneFor(env, client, pincode);
  } catch (e) {
    fail(503, "Could not reach Amul just now. Try again in a minute.");
  }
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
  return json({ ok: true, added });
}

export async function updateWatch(env, user, id, body) {
  const res = await env.DB.prepare("UPDATE watches SET enabled = ? WHERE id = ? AND user_id = ?")
    .bind(body.enabled ? 1 : 0, Number(id), user.id).run();
  if (!res.meta.changes) fail(404, "No such alert.");
  return json({ ok: true });
}

export async function deleteWatch(env, user, id) {
  const res = await env.DB.prepare("DELETE FROM watches WHERE id = ? AND user_id = ?").bind(Number(id), user.id).run();
  if (!res.meta.changes) fail(404, "No such alert.");
  return json({ ok: true });
}

export async function updateSettings(env, user, body) {
  if (!(await allow(env.DB, `write:${user.id}`, 60, 60))) fail(429, "Slow down a little.");
  if ("webhook" in body) {
    const url = String(body.webhook || "").trim();
    if (url && !validWebhook(url)) fail(400, "Paste a Discord or Slack incoming webhook URL (https://discord.com/api/webhooks/... or https://hooks.slack.com/...).");
    await env.DB.prepare("UPDATE users SET webhook_url = ? WHERE id = ?").bind(url || null, user.id).run();
  }
  if (body.new_topic) {
    await env.DB.prepare("UPDATE users SET ntfy_topic = ? WHERE id = ?").bind(randomTopic(), user.id).run();
  }
  if (typeof body.name === "string") {
    const name = cleanName(body.name);
    if (!name) fail(400, "The name cannot be empty.");
    await env.DB.prepare("UPDATE users SET display_name = ? WHERE id = ?").bind(name, user.id).run();
  }
  return json({ ok: true });
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
  return json({ ok: res.ok, detail: res.detail });
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
    "SELECT p.alias, p.label, p.enabled, (SELECT COUNT(*) FROM watches w WHERE w.alias = p.alias) AS watches FROM products p ORDER BY p.label",
  ).all()).results;
  const recent = (await db.prepare(
    "SELECT a.ts, a.kind, a.product, a.pincodes, a.result, u.display_name FROM alert_log a JOIN users u ON u.id = a.user_id ORDER BY a.ts DESC LIMIT 25",
  ).all()).results;
  return json({
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
  if (target.id === user.id && (body.disabled || body.role === "user" || body.delete)) fail(400, "You cannot lock yourself out.");
  if (target.role === "admin" && (body.disabled || body.role === "user" || body.delete)) {
    const admins = await env.DB.prepare("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = 0").first();
    if (admins.n <= 1) fail(400, "That is the last active admin.");
  }
  if (body.delete) {
    await env.DB.prepare("DELETE FROM users WHERE id = ?").bind(target.id).run();
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
  return json({ ok: true });
}

export async function adminProduct(env, user, body) {
  requireAdmin(user);
  let alias = String(body.alias || "").trim().split(/[?#]/)[0].replace(/\/$/, "");
  alias = alias.split("/product/").pop().toLowerCase();
  if (!ALIAS_RE.test(alias)) fail(400, "Paste the product URL from shop.amul.com.");
  if (body.remove) {
    await env.DB.prepare("UPDATE products SET enabled = 0 WHERE alias = ?").bind(alias).run();
    return json({ ok: true });
  }
  const label = String(body.label || "").trim().slice(0, 80) || alias.replace(/-/g, " ");
  await env.DB.prepare(
    `INSERT INTO products (alias, label, enabled, created_at) VALUES (?, ?, 1, ?)
     ON CONFLICT(alias) DO UPDATE SET label = excluded.label, enabled = 1`,
  ).bind(alias, label, now()).run();
  return json({ ok: true, alias });
}
