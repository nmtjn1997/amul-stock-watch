// The every-minute cron.
//
// Work is deduplicated: each distinct (pincode, product) among everyone's enabled
// watches is read once, however many people watch it. The free plan allows 50 outbound
// requests per run, so pincodes are visited round-robin from a saved cursor and a run
// stops early rather than exceed the budget; with many pincodes a full lap takes a few
// minutes. Alerts are per watch: `watches.alerted` is set once that person was told about
// the current restock and cleared when the product sells out.

import { AmulClient, AmulError, parseStock } from "./amul.js";
import { deliver } from "./notify.js";
import { allow, now } from "./util.js";

const BUDGET = 46; // of 50, leaving room for the session bootstrap retry
const NOTIFY_RESERVE = 8;
const ZONE_TTL = 86400;
const CHUNK = 20; // products per unit: 20 reads + lookup + 2 region calls fit in any run

async function getMeta(db, key, fallback) {
  const row = await db.prepare("SELECT value FROM meta WHERE key = ?").bind(key).first();
  if (!row) return fallback;
  try {
    return JSON.parse(row.value);
  } catch {
    return fallback;
  }
}

async function setMeta(db, key, value) {
  await db.prepare("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value")
    .bind(key, JSON.stringify(value)).run();
}

export async function zoneFor(env, client, pincode) {
  const cached = await env.DB.prepare("SELECT * FROM zones WHERE pincode = ?").bind(pincode).first();
  if (cached && now() - cached.checked_at < ZONE_TTL) return cached;
  const found = await client.lookupPincode(pincode);
  const row = { pincode, record_id: found?.record_id || null, store: found?.store || null, valid: found ? 1 : 0, checked_at: now() };
  await env.DB.prepare(
    `INSERT INTO zones (pincode, record_id, store, valid, checked_at) VALUES (?, ?, ?, ?, ?)
     ON CONFLICT(pincode) DO UPDATE SET record_id = excluded.record_id, store = excluded.store,
       valid = excluded.valid, checked_at = excluded.checked_at`,
  ).bind(row.pincode, row.record_id, row.store, row.valid, row.checked_at).run();
  return row;
}

async function readProduct(client, zone, alias) {
  await client.usePincode(zone.pincode, zone.store);
  try {
    return await client.product(alias, zone.record_id);
  } catch (e) {
    if (e instanceof AmulError && e.status === 409) {
      await client.usePincode(zone.pincode, zone.store); // session was renewed: select the pincode again
      return client.product(alias, zone.record_id);
    }
    throw e;
  }
}

export async function runPoll(env) {
  const db = env.DB;
  const started = now();
  const pairs = (
    await db.prepare(
      `SELECT DISTINCT w.pincode, w.alias, p.label FROM watches w
       JOIN users u ON u.id = w.user_id JOIN products p ON p.alias = w.alias
       WHERE w.enabled = 1 AND u.disabled = 0 AND p.enabled = 1
       ORDER BY w.pincode, w.alias`,
    ).all()
  ).results;
  // Units of at most CHUNK products for one pincode, so a pincode with many watched
  // products can never be too big for one run's budget and stall the rotation.
  const byPin = new Map();
  for (const p of pairs) {
    const list = [...byPin.entries()].filter(([k]) => k.startsWith(p.pincode + "#")).map(([, v]) => v).pop();
    if (list && list.length < CHUNK) list.push(p);
    else byPin.set(`${p.pincode}#${byPin.size}`, [p]);
  }
  const pins = [...byPin.keys()];
  const summary = { ts: started, pincodes_total: pins.length, pairs_total: pairs.length, pincodes: 0, checks: 0, alerts: 0, errors: [] };
  if (!pins.length) {
    await setMeta(db, "last_run", summary);
    return summary;
  }

  const budget = { left: BUDGET };
  const client = new AmulClient(db, budget);
  await client.load();
  let cursor = Number(await getMeta(db, "poll_cursor", 0)) % pins.length;
  const fresh = []; // pairs now in stock, to alert on

  for (let visited = 0; visited < pins.length; visited++) {
    const items = byPin.get(pins[cursor]);
    const pin = items[0].pincode;
    if (budget.left - NOTIFY_RESERVE < items.length + 3) break; // lookup + 2 region calls
    cursor = (cursor + 1) % pins.length;
    try {
      const zone = await zoneFor(env, client, pin);
      if (!zone.valid) continue;
      summary.pincodes++;
      for (const item of items) {
        const raw = await readProduct(client, zone, item.alias);
        summary.checks++;
        if (!raw) {
          summary.errors.push(`${pin}/${item.alias}: not found`);
          continue;
        }
        const s = parseStock(raw);
        const prev = await db.prepare("SELECT in_stock, qty FROM stock WHERE pincode = ? AND alias = ?").bind(pin, item.alias).first();
        if (!prev || prev.in_stock !== Number(s.inStock) || prev.qty !== s.qty) {
          await db.prepare(
            `INSERT INTO stock (pincode, alias, in_stock, qty, price, changed_at) VALUES (?, ?, ?, ?, ?, ?)
             ON CONFLICT(pincode, alias) DO UPDATE SET in_stock = excluded.in_stock, qty = excluded.qty,
               price = excluded.price, changed_at = excluded.changed_at`,
          ).bind(pin, item.alias, Number(s.inStock), s.qty, s.price, now()).run();
        }
        if (s.inStock) fresh.push({ ...item, qty: s.qty, price: s.price });
        else await db.prepare("UPDATE watches SET alerted = 0 WHERE pincode = ? AND alias = ? AND alerted = 1").bind(pin, item.alias).run();
      }
    } catch (e) {
      summary.errors.push(`${pin}: ${e.message}`);
      if (budget.left <= 0) break;
    }
  }
  await setMeta(db, "poll_cursor", cursor);
  await client.save();

  // Alerts: one message per person per product, listing every pincode that came back.
  const pending = new Map();
  for (const f of fresh) {
    const rows = (
      await db.prepare(
        `SELECT w.id, w.user_id FROM watches w JOIN users u ON u.id = w.user_id
         WHERE w.pincode = ? AND w.alias = ? AND w.enabled = 1 AND w.alerted = 0 AND u.disabled = 0`,
      ).bind(f.pincode, f.alias).all()
    ).results;
    for (const r of rows) {
      const key = `${r.user_id}|${f.alias}`;
      if (!pending.has(key)) pending.set(key, { userId: r.user_id, label: f.label, alias: f.alias, pins: [], where: [], ids: [], price: f.price });
      const p = pending.get(key);
      p.pins.push(f.pincode);
      p.where.push(f.qty ? `${f.pincode} (${f.qty} left)` : f.pincode);
      p.ids.push(r.id);
    }
  }
  for (const p of pending.values()) {
    if (budget.left < 2) break; // the rest go out next minute
    // A person whose channels keep failing is retried every 5 minutes, not every minute.
    if (!(await allow(db, `notify-retry:${p.userId}:${p.alias}`, 1, 300))) continue;
    const user = await db.prepare("SELECT * FROM users WHERE id = ?").bind(p.userId).first();
    if (!user) continue; // deleted while this run was going
    const url = `https://shop.amul.com/en/product/${p.alias}`;
    const alert = {
      kind: "stock",
      title: `Back in stock: ${p.label}`,
      message: `${p.label} is in stock at ${p.where.join(", ")}` +
        (p.price ? `. Price Rs ${Math.round(p.price)}` : "") + `.\nTap to open the shop.`,
      url,
    };
    const res = await deliver(env, user, alert);
    budget.left -= res.used;
    const stmts = [
      db.prepare("INSERT INTO alert_log (user_id, ts, kind, product, pincodes, result) VALUES (?, ?, 'stock', ?, ?, ?)")
        .bind(p.userId, now(), p.label, p.pins.join(","), res.detail),
    ];
    if (res.ok) {
      summary.alerts++;
      stmts.push(db.prepare(`UPDATE watches SET alerted = 1 WHERE id IN (${p.ids.map(() => "?").join(",")})`).bind(...p.ids));
      stmts.push(db.prepare("DELETE FROM rate WHERE key = ?").bind(`notify-retry:${p.userId}:${p.alias}`));
    }
    await db.batch(stmts);
  }

  summary.errors = summary.errors.slice(0, 10);
  summary.ms = Date.now() - started * 1000;
  await setMeta(db, "last_run", summary);
  await db.prepare("DELETE FROM rate WHERE window_start < ?").bind(now() - 86400).run();
  await db.prepare("DELETE FROM alert_log WHERE ts < ?").bind(now() - 30 * 86400).run();
  return summary;
}
