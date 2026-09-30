/* Hosted demo: the real Amul Stock Watch UI, with its JSON API answered in the browser.
 *
 * Loaded before the UI's own script. It replaces fetch() for /api/* with an in-memory
 * copy of demo-data.js, so every button works, but nothing is saved (a reload resets it),
 * nothing is sent to anyone, and shop.amul.com is never contacted.
 */
(function () {
  "use strict";
  window.AMUL_DEMO = true;
  const data = JSON.parse(JSON.stringify(window.AMUL_DEMO_DATA));
  const S = data.state;
  let stock = data.stock;
  let paused = false;
  const now = () => Date.now() / 1000;
  const started = now();
  const history = [
    { kind: "stock", title: "Amul: Milk Shake Premix Mango in stock", product: "Milk Shake Premix Mango",
      alias: "amul-milk-shake-premix-mango-500-g", pincodes: ["110001"], qty: 7, price: 210, source: "poll",
      url: "https://shop.amul.com/en/product/amul-milk-shake-premix-mango-500-g",
      notifiers: ["me-phone"], results: { "me-phone": "ok" }, ts: started - 3600 * 5 },
    { kind: "stock", title: "Amul: High Protein Buttermilk in stock", product: "High Protein Buttermilk",
      alias: "amul-high-protein-buttermilk-200-ml-or-pack-of-30", pincodes: ["560001"], qty: 67, price: 900,
      source: "poll", url: "https://shop.amul.com/en/product/amul-high-protein-buttermilk-200-ml-or-pack-of-30",
      notifiers: ["mum-telegram"], results: { "mum-telegram": "ok" }, ts: started - 3600 * 26 },
  ];
  const logLines = [
    "INFO amul-watch poller started (every 60s)",
    "INFO config: polling 2 pincode(s)",
    "INFO spread poll: 7 tasks, 8.6s gap, ~60s cycle (2 pins)",
    "INFO poll done checks=5 alerts=0 errors=0",
  ];

  function daemon() {
    return { paused, running: !paused, heartbeat_age_seconds: Math.round((now() - started) % 60),
             pause_info: paused ? { reason: "paused in the demo" } : undefined, last_log: logLines.slice(-6) };
  }
  function product(aliasOrShort) {
    const k = String(aliasOrShort || "").toLowerCase();
    return S.products.find(p => p.alias === k || p.short === k);
  }
  function summaryFor(names) {
    return names.map(n => {
      const r = S.recipients.find(x => x.name === n);
      return r ? r.summary : n + ": MISSING";
    });
  }
  function routeFor(w) {
    return w.has_route ? w.recipients : S.default_alerts.slice();
  }
  function upsertWatch(pin, alias, recipients, enabled) {
    const p = product(alias);
    if (!p) throw new Error("unknown product " + alias);
    if (!/^\d{6}$/.test(pin)) throw new Error("invalid pincode: " + pin + " (six digits)");
    let addr = S.addresses.find(a => a.pincode === pin);
    if (!addr) {
      addr = { pincode: pin, label: "Pin " + pin, short: "", enabled: true, products: [], disabled_products: [] };
      S.addresses.push(addr);
    }
    let w = S.watches.find(x => x.pincode === pin && x.alias === p.alias);
    if (!w) {
      w = { pincode: pin, address_label: addr.label, alias: p.alias, product_short: p.short,
            product_label: p.label, route_key: pin + ":" + p.short, explicit: true, has_route: false,
            recipients: [], enabled: true };
      S.watches.push(w);
    }
    if (recipients !== null) {
      w.has_route = recipients.length > 0;
      w.recipients = recipients;
    }
    w.enabled = enabled;
    w.recipients = routeFor(w);
    w.recipients_summary = summaryFor(w.recipients);
    S.watches.sort((a, b) => (a.pincode + !a.enabled + a.product_label).localeCompare(b.pincode + !b.enabled + b.product_label));
    return { pincode: pin, alias: p.alias, enabled };
  }
  function refreshDefaults() {
    S.watches.forEach(w => { if (!w.has_route) { w.recipients = S.default_alerts.slice(); w.recipients_summary = summaryFor(w.recipients); } });
  }

  const routes = {
    "GET /api/state": () => ({ ...S, daemon: daemon() }),
    "GET /api/stock": () => ({ rows: stock, daemon: daemon() }),
    "GET /api/notifications": q => {
      const off = +q.offset || 0, lim = +q.limit || 25;
      const items = history.slice().sort((a, b) => b.ts - a.ts);
      return { items: items.slice(off, off + lim), total: items.length, offset: off, limit: lim };
    },
    "GET /api/logs": () => ({ file: "daemon", lines: logLines.map(l => "2026-01-01 09:00:00,000 " + l),
                              start: 0, end: 1, size: 1, has_older: false, rotated: false,
                              tail_cmd: "tail -100f ~/.config/amul-watch/data/amul-watch.log",
                              files: { daemon: { id: "daemon", label: "poller and alerts", exists: true, size: 1 } } }),
    "POST /api/watch": b => {
      const prods = [].concat(b.products || b.product || []);
      if (!prods.length) throw new Error("pick at least one product");
      const recips = b.recipients == null ? null : [].concat(b.recipients);
      if (b.address_label) {
        upsertWatch(String(b.pincode), prods[0], recips, true);
        S.addresses.find(a => a.pincode === String(b.pincode)).label = b.address_label;
      }
      return { ok: true, watches: prods.map(p => upsertWatch(String(b.pincode), p, recips, b.enabled !== false)) };
    },
    "POST /api/watch/toggle": b => {
      const w = S.watches.find(x => x.pincode === b.pincode && x.alias === b.product);
      if (!w) throw new Error("no such watch");
      w.enabled = !!b.enabled;
      return { ok: true };
    },
    "POST /api/watch/delete": b => {
      S.watches = S.watches.filter(x => !(x.pincode === b.pincode && x.alias === b.product));
      return { ok: true };
    },
    "POST /api/watch/fill-disabled": () => {
      let added = 0;
      S.addresses.forEach(a => S.products.forEach(p => {
        if (!S.watches.some(w => w.pincode === a.pincode && w.alias === p.alias)) { upsertWatch(a.pincode, p.alias, null, false); added++; }
      }));
      return { ok: true, added, pins: S.addresses.length };
    },
    "POST /api/address": b => {
      let a = S.addresses.find(x => x.pincode === b.pincode);
      if (!a) {
        if (!/^\d{6}$/.test(b.pincode || "")) throw new Error("invalid pincode (six digits)");
        a = { pincode: b.pincode, label: b.label || "Pin " + b.pincode, short: b.short || "", enabled: true, products: [], disabled_products: [] };
        S.addresses.push(a);
      }
      if (b.label) a.label = b.label;
      if (b.short) a.short = b.short;
      if (b.enabled != null) a.enabled = !!b.enabled;
      S.watches.forEach(w => { if (w.pincode === a.pincode) { w.address_label = a.label; if (b.enabled != null) w.enabled = a.enabled; } });
      return { ok: true };
    },
    "POST /api/address/delete": b => {
      S.addresses = S.addresses.filter(a => a.pincode !== b.pincode);
      S.watches = S.watches.filter(w => w.pincode !== b.pincode);
      return { ok: true };
    },
    "POST /api/product": b => {
      const alias = String(b.alias || "").split(/[?#]/)[0].replace(/\/$/, "").split("/product/").pop().toLowerCase();
      if (!/^[a-z0-9-]+$/.test(alias)) throw new Error("paste a product URL from shop.amul.com");
      let p = S.products.find(x => x.alias === alias);
      if (!p) { p = { alias, short: b.short || alias, label: b.label || alias, enabled: true, enquiry_name: "" }; S.products.push(p); }
      if (b.label) p.label = b.label;
      if (b.short) p.short = b.short;
      return { ok: true, product: p };
    },
    "POST /api/notifier": b => {
      const name = String(b.name || "");
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(name)) throw new Error("notifier name: letters, digits, - _ or .");
      const cfg = b.config || {};
      const detail = cfg.to ? [].concat(cfg.to).join(", ") : cfg.topic ? "topic " + cfg.topic : cfg.chat_id || cfg.channel || "";
      const existing = S.recipients.find(r => r.name === name);
      const entry = { name, type: b.type, enabled: true, config: { ...(existing ? existing.config : {}), ...cfg },
                      summary: name + ": " + b.type + (detail ? " " + detail : ""), problems: [] };
      S.recipients = S.recipients.filter(r => r.name !== name).concat([entry]);
      return { ok: true };
    },
    "POST /api/notifier/delete": b => {
      S.recipients = S.recipients.filter(r => r.name !== b.name);
      ["default_alerts", "system_alerts", "qty_update_alerts"].forEach(k => { S[k] = S[k].filter(n => n !== b.name); });
      S.watches.forEach(w => { w.recipients = w.recipients.filter(n => n !== b.name); if (!w.recipients.length) w.has_route = false; });
      refreshDefaults();
      return { ok: true };
    },
    "POST /api/notifier/test": b => ({ ok: true, results: { [b.name]: "ok" } }),
    "POST /api/name-list": b => { S[b.key] = [].concat(b.names || []); refreshDefaults(); return { ok: true }; },
    "POST /api/normalize": () => ({ ok: true, normalized_pincodes: [] }),
    "POST /api/simulate": b => {
      const w = S.watches.find(x => x.pincode === b.pincode && x.alias === b.product);
      const names = w ? w.recipients : S.default_alerts;
      const p = product(b.product) || { label: b.product };
      const lines = ["route " + b.pincode + ":" + (p.short || b.product) + ":"].concat(summaryFor(names).map(s => "  " + s), [""]);
      if (b.dry_run) lines.push("DRY RUN: nothing sent", "", "TEST alert (not a real restock): " + p.label);
      else { lines.push("(demo: nothing is really sent)"); names.forEach(n => lines.push("  " + n + ": ok")); }
      return { ok: true, output: lines.join("\n") };
    },
    "POST /api/poll": () => {
      if (paused) throw new Error("polling is paused: resume it first");
      stock = stock.map(r => r.in_stock === "yes" ? { ...r, qty: String(Math.max(1, +r.qty + Math.round(Math.random() * 6 - 3))) } : r);
      return { ok: true, summary: { checks: stock.length, alerts: 0 }, errors: [], rows: stock, daemon: daemon() };
    },
    "POST /api/control": b => {
      if (b.action === "pause") paused = true;
      else if (b.action === "resume") paused = false;
      else throw new Error("unknown action");
      return { ok: true, daemon: daemon() };
    },
  };

  const realFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const m = url.pathname.match(/\/api\/[a-z/-]+$/);
    if (!m) return realFetch(input, init);
    const method = (init && init.method) || "GET";
    const handler = routes[method + " " + m[0]];
    const reply = (body, status) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    await new Promise(r => setTimeout(r, method === "POST" && m[0] === "/api/poll" ? 900 : 60));
    if (!handler) return reply({ error: "not found" }, 404);
    try {
      const body = method === "POST" ? JSON.parse((init && init.body) || "{}") : Object.fromEntries(url.searchParams);
      return reply(handler(body), 200);
    } catch (e) {
      return reply({ error: e.message }, 400);
    }
  };

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.createElement("div");
    bar.setAttribute("role", "note");
    bar.style.cssText = "background:#2f6df6;color:#fff;font:600 13px/1.4 -apple-system,system-ui,sans-serif;" +
      "padding:8px 14px;text-align:center";
    bar.innerHTML = 'Live demo with sample data: try anything, nothing is saved or sent. ' +
      '<a href="../" style="color:#fff;text-decoration:underline">Get your own copy</a>';
    document.body.prepend(bar);
  });
})();
