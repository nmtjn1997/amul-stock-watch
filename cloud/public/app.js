// Back in Stock web app. No framework: views are functions that build DOM with h().
// All user data is inserted as text (never as HTML), which is what keeps it XSS-free.
"use strict";

const $ = (s) => document.querySelector(s);
function h(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) n.append(c instanceof Node ? c : String(c));
  return n;
}

let ME = null;       // /api/me payload
let CONFIG = { google: false, turnstile: null, max_watches: 10 };

const AUTH_ERRORS = {
  expired: "Sign-in expired. Please try again.",
  cancelled: "Google sign-in was cancelled.",
  unverified: "That Google account could not be verified.",
  busy: "Too many new accounts from this network. Try again later.",
  disabled: "This account is disabled.",
  full: "This service is full right now.",
};

// Cloudflare Turnstile, loaded only on the sign-up form and only when the server has it on.
let turnstileReady = null;
function loadTurnstile() {
  if (!turnstileReady) {
    turnstileReady = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      s.async = true;
      s.onload = () => resolve(window.turnstile);
      s.onerror = () => reject(new Error("The human check could not load. Check your connection."));
      document.head.append(s);
    });
  }
  return turnstileReady;
}

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({ error: "Unexpected answer from the server." }));
  if (!r.ok) {
    const e = new Error(data.error || `Error ${r.status}`);
    e.status = r.status;
    throw e;
  }
  return data;
}

function toast(msg, err) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), err ? 4500 : 2500);
}

const title = (s) => String(s || "").replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

function ago(ts) {
  if (!ts) return "not yet";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

const show = (...nodes) => { const v = $("#view"); v.replaceChildren(...nodes); window.scrollTo(0, 0); };
const go = (hash) => { if (location.hash === hash) render(); else location.hash = hash; };

function busy(btn, on, label) {
  if (!btn) return;
  if (on) { btn._label = btn.textContent; btn.textContent = label || "Working..."; btn.disabled = true; }
  else { btn.textContent = btn._label || btn.textContent; btn.disabled = false; }
}

function openSheet(...nodes) {
  $("#sheet").replaceChildren(...nodes);
  $("#scrim").hidden = false;
  const first = $("#sheet").querySelector("input,button");
  if (first) setTimeout(() => first.focus(), 50);
}
function closeSheet() { $("#scrim").hidden = true; }
$("#scrim")?.addEventListener("click", (e) => { if (e.target.id === "scrim") closeSheet(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

// ------------------------------------------------------------------ auth screens

function authView(mode) {
  const signup = mode === "signup";
  const err = h("div", { class: "err" });
  const params = new URLSearchParams((location.hash.split("?")[1] || ""));
  if (params.get("error")) err.textContent = AUTH_ERRORS[params.get("error")] || "Sign-in did not complete. Please try again.";
  const user = h("input", { type: "text", id: "u", autocomplete: "username", autocapitalize: "none", spellcheck: "false", maxlength: "20", required: true });
  const pass = h("input", { type: "password", id: "p", autocomplete: signup ? "new-password" : "current-password", maxlength: "128", required: true });
  const btn = h("button", { class: "primary wide", type: "submit" }, signup ? "Create account" : "Log in");
  const human = h("div", { class: "human" });
  let humanToken = "";
  let widget = null;
  if (signup && CONFIG.turnstile) {
    loadTurnstile().then((ts) => {
      widget = ts.render(human, { sitekey: CONFIG.turnstile, callback: (t) => { humanToken = t; }, "expired-callback": () => { humanToken = ""; } });
    }).catch((x) => { err.textContent = x.message; });
  }
  const form = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = "";
      busy(btn, true);
      try {
        await api(signup ? "/api/signup" : "/api/login", { username: user.value.trim().toLowerCase(), password: pass.value, turnstile: humanToken });
        await loadMe();
        go(ME.watches.length ? "#/" : "#/setup");
      } catch (x) {
        err.textContent = x.message;
        if (widget !== null && window.turnstile) { window.turnstile.reset(widget); humanToken = ""; }
      } finally {
        busy(btn, false);
      }
    },
  },
    h("label", { class: "f", for: "u" }, "Username"), user,
    signup ? h("div", { class: "hint" }, "3 to 20 characters: lowercase letters, digits or _. No email needed.") : null,
    h("label", { class: "f", for: "p" }, "Password"), pass,
    signup ? h("div", { class: "hint" }, "At least 8 characters.") : null,
    signup && CONFIG.turnstile ? human : null,
    err, btn,
  );
  show(h("div", { class: "page" },
    h("h1", {}, signup ? "Create your account" : "Welcome back"),
    h("p", { class: "lead" }, "Get a phone alert the moment an Amul product is back in stock at your pincode. Free, takes a minute."),
    h("div", { class: "card" },
      CONFIG.google ? h("a", { class: "btn wide", href: "/auth/google/start" }, "Continue with Google") : null,
      CONFIG.google ? h("div", { class: "or" }, "or with a username") : null,
      form,
      h("div", { class: "switchlink" }, signup ? "Already have an account? " : "New here? ",
        h("a", { href: signup ? "#/login" : "#/signup" }, signup ? "Log in" : "Create an account")),
    ),
    h("p", { class: "muted small" }, "Unofficial, not affiliated with Amul. It never logs in to Amul or buys anything. ",
      h("a", { href: "https://github.com/nmtjn1997/amul-stock-watch" }, "Open source"), "."),
  ));
  setTimeout(() => user.focus(), 50);
}

// ------------------------------------------------------------------ first-run wizard

const wizard = { step: 1, pincode: "", region: "" };

function stepsBar(n) {
  return h("div", { class: "steps", "aria-label": `Step ${n} of 3` }, [1, 2, 3].map((i) => h("span", { class: i <= n ? "on" : "" })));
}

function pincodeField(value, onValid) {
  const input = h("input", { inputmode: "numeric", pattern: "[0-9]*", maxlength: "6", autocomplete: "postal-code", value: value || "", id: "pin", placeholder: "e.g. 560001" });
  const out = h("div", { class: "err" });
  async function check() {
    out.className = "err";
    out.textContent = "";
    const pin = input.value.replace(/\D/g, "");
    if (!/^[1-9]\d{5}$/.test(pin)) { out.textContent = "A pincode is 6 digits."; return null; }
    out.className = "hint";
    out.textContent = "Checking with Amul...";
    try {
      const r = await api(`/api/pincode/${pin}`);
      if (!r.valid) { out.className = "err"; out.textContent = "Amul does not deliver to that pincode."; return null; }
      out.className = "ok-line";
      out.textContent = `Amul delivers here (${title(r.region)} region).`;
      onValid && onValid(pin, r.region);
      return pin;
    } catch (x) {
      out.className = "err";
      out.textContent = x.message;
      return null;
    }
  }
  return { input, out, check };
}

function productPicker(taken) {
  const box = h("div", { class: "pick" });
  for (const p of ME.products) {
    const already = taken.has(p.alias);
    box.append(h("label", {},
      h("input", { type: "checkbox", value: p.alias, checked: already, disabled: already }),
      h("span", {}, p.label),
      already ? h("span", { class: "sub" }, "already watching") : null));
  }
  return box;
}

function setupView() {
  const s = wizard.step;
  if (s === 1) {
    const pf = pincodeField(wizard.pincode, (pin, region) => { wizard.pincode = pin; wizard.region = region; });
    const btn = h("button", { class: "primary wide", type: "submit" }, "Continue");
    show(h("div", { class: "page" }, stepsBar(1),
      h("h1", {}, "Where should we check?"),
      h("p", { class: "lead" }, "Stock is different in every region, so start with the pincode you order to."),
      h("form", { class: "card", onsubmit: async (e) => {
        e.preventDefault(); busy(btn, true, "Checking...");
        const ok = await pf.check(); busy(btn, false);
        if (ok) { wizard.step = 2; setupView(); }
      } }, h("label", { class: "f", for: "pin" }, "Pincode"), pf.input, pf.out, btn),
      ME.watches.length ? h("p", { class: "switchlink" }, h("a", { href: "#/" }, "Skip, go to my alerts")) : null,
    ));
    setTimeout(() => pf.input.focus(), 50);
  } else if (s === 2) {
    const taken = new Set(ME.watches.filter((w) => w.pincode === wizard.pincode).map((w) => w.alias));
    const picker = productPicker(taken);
    const err = h("div", { class: "err" });
    const btn = h("button", { class: "primary wide", type: "submit" }, "Watch these");
    const left = ME.user.max_watches - ME.watches.length;
    show(h("div", { class: "page" }, stepsBar(2),
      h("h1", {}, "What are you waiting for?"),
      h("p", { class: "lead" }, `Pick the products to watch at ${wizard.pincode}. You can have ${left} more alerts, across up to ${ME.user.max_pincodes} pincodes.`),
      h("form", { class: "card", onsubmit: async (e) => {
        e.preventDefault();
        const products = [...picker.querySelectorAll("input:checked:not(:disabled)")].map((i) => i.value);
        if (!products.length) { err.textContent = "Tick at least one product."; return; }
        busy(btn, true, "Saving...");
        try {
          await api("/api/watches", { pincode: wizard.pincode, products });
          await loadMe();
          wizard.step = 3; setupView();
        } catch (x) { err.textContent = x.message; } finally { busy(btn, false); }
      } }, picker, err, btn),
      h("p", { class: "switchlink" }, h("a", { href: "#/setup", onclick: (e) => { e.preventDefault(); wizard.step = 1; setupView(); } }, "Change pincode")),
    ));
  } else {
    show(h("div", { class: "page" }, stepsBar(3),
      h("h1", {}, "Where should we tell you?"),
      h("p", { class: "lead" }, "Alerts arrive as phone notifications through the free ntfy app. No account needed."),
      h("div", { class: "card" }, phoneSetup(true)),
      h("button", { class: "primary wide", onclick: () => go("#/") }, "Done, show my alerts"),
    ));
  }
}

function phoneSetup(compact) {
  const u = ME.user;
  const web = `${u.ntfy_server}/${u.ntfy_topic}`;
  const deep = `ntfy://${u.ntfy_server.replace(/^https?:\/\//, "")}/${u.ntfy_topic}`;
  const testBtn = h("button", { class: "primary", onclick: async () => {
    busy(testBtn, true, "Sending...");
    try { const r = await api("/api/test", {}); toast(r.ok ? "Sent. Check your phone." : `Could not send: ${r.detail}`, !r.ok); }
    catch (x) { toast(x.message, true); } finally { busy(testBtn, false); }
  } }, "Send a test");
  return h("div", {},
    h("ol", { class: "how" },
      h("li", {}, "Install ntfy: ",
        h("a", { href: "https://play.google.com/store/apps/details?id=io.heckel.ntfy", rel: "noopener", target: "_blank" }, "Android"), " or ",
        h("a", { href: "https://apps.apple.com/app/ntfy/id1625396347", rel: "noopener", target: "_blank" }, "iPhone"), "."),
      h("li", {}, "On the phone, tap ", h("a", { href: deep }, "Subscribe in ntfy"),
        ". Or open ntfy, tap +, and enter this topic:", h("div", { class: "topic" }, u.ntfy_topic)),
      h("li", {}, "Send yourself a test, then keep the app's notifications on."),
    ),
    h("div", { class: "row" }, testBtn,
      h("button", { class: "ghost", onclick: async () => { try { await navigator.clipboard.writeText(u.ntfy_topic); toast("Topic copied"); } catch { toast("Copy it by hand from above", true); } } }, "Copy topic"),
      compact ? null : h("a", { class: "btn ghost", href: web, target: "_blank", rel: "noopener" }, "Open on the web")),
    h("p", { class: "hint" }, "Keep the topic private: anyone who knows it can read your alerts."),
  );
}

// ------------------------------------------------------------------ my alerts

function status(w) {
  if (!w.product_enabled) return h("span", { class: "badge bad" }, "No longer offered");
  if (w.valid === 0) return h("span", { class: "badge bad" }, "Amul does not deliver here");
  if (w.in_stock === null || w.in_stock === undefined) return h("span", { class: "badge wait" }, "Checking soon");
  if (w.in_stock) return h("span", { class: "badge in" }, `In stock${w.qty ? `, ${w.qty} left` : ""}`);
  return h("span", { class: "badge out" }, "Out of stock");
}

function alertsView() {
  const ws = ME.watches;
  const max = ME.user.max_watches;
  const addBtn = h("button", { class: "primary", onclick: addSheet, disabled: ws.length >= max }, "+ Add alert");
  const list = h("div", { class: "card" });
  if (!ws.length) {
    list.append(h("div", { class: "empty" }, "No alerts yet.", h("br"), h("button", { class: "primary wide", onclick: () => { wizard.step = 1; go("#/setup"); } }, "Set up my first alert")));
  }
  for (const w of ws) {
    const sw = h("button", { class: "switch", role: "switch", "aria-checked": String(!!w.enabled), "aria-label": `${w.enabled ? "Pause" : "Resume"} ${w.label} at ${w.pincode}`,
      onclick: async () => {
        try { await api(`/api/watches/${w.id}`, { enabled: !w.enabled }); w.enabled = !w.enabled; sw.setAttribute("aria-checked", String(!!w.enabled)); toast(w.enabled ? "Alert on" : "Alert paused"); }
        catch (x) { toast(x.message, true); }
      } });
    const del = h("button", { class: "icon-btn", "aria-label": `Delete ${w.label} at ${w.pincode}`, title: "Delete",
      onclick: async () => {
        if (!confirm(`Stop watching ${w.label} at ${w.pincode}?`)) return;
        try { await api(`/api/watches/${w.id}`, { delete: true }); await loadMe(); render(); toast("Deleted"); }
        catch (x) { toast(x.message, true); }
      } }, "✕");
    list.append(h("div", { class: "alert" },
      h("div", { class: "name" }, w.label),
      h("div", { class: "acts" }, sw, del),
      h("div", { class: "meta" }, status(w), `Pincode ${w.pincode}${w.store ? ` (${title(w.store)})` : ""}`),
    ));
  }
  const phoneReady = ME.user.webhook ? `phone and ${ME.user.webhook}` : "phone (ntfy)";
  show(
    h("div", { class: "summary card" },
      h("div", { class: "grow" },
        h("div", { class: "count" }, `${ws.length} of ${max} alerts`, h("span", { class: "muted small" }, ` · ${new Set(ws.map((w) => w.pincode)).size} of ${ME.user.max_pincodes} pincodes`)),
        h("div", { class: "muted small" }, `Alerts go to your ${phoneReady}. Last check: ${ago(ME.last_check)}.`)),
      addBtn),
    list,
    h("p", { class: "muted small" }, "You get one message when a product goes from out of stock to in stock. Nothing while it stays in stock."),
  );
}

function addSheet() {
  const last = ME.watches.length ? ME.watches[ME.watches.length - 1].pincode : "";
  let ready = last;
  const pf = pincodeField(last, (pin) => { ready = pin; refresh(); });
  const pickWrap = h("div", {});
  const err = h("div", { class: "err" });
  const save = h("button", { class: "primary wide", type: "submit" }, "Add");
  function refresh() {
    const taken = new Set(ME.watches.filter((w) => w.pincode === ready).map((w) => w.alias));
    pickWrap.replaceChildren(h("label", { class: "f" }, "Products"), productPicker(taken));
  }
  refresh();
  pf.input.addEventListener("input", () => { ready = ""; });
  openSheet(h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    busy(save, true, "Saving...");
    try {
      const pin = ready || (await pf.check());
      if (!pin) return;
      const products = [...pickWrap.querySelectorAll("input:checked:not(:disabled)")].map((i) => i.value);
      if (!products.length) { err.textContent = "Tick at least one product."; return; }
      await api("/api/watches", { pincode: pin, products });
      closeSheet(); await loadMe(); render(); toast(products.length > 1 ? `${products.length} alerts added` : "Alert added");
    } catch (x) { err.textContent = x.message; } finally { busy(save, false); }
  } },
    h("h2", {}, "New alert"),
    h("label", { class: "f", for: "pin" }, "Pincode"),
    h("div", { class: "row" }, h("div", { class: "grow" }, pf.input), h("button", { type: "button", onclick: pf.check }, "Check")),
    pf.out, pickWrap, err, save,
    h("button", { type: "button", class: "ghost wide", onclick: closeSheet }, "Cancel"),
  ));
}

// ------------------------------------------------------------------ settings

async function settingsView() {
  const u = ME.user;
  const hook = h("input", { type: "url", id: "hook", placeholder: "https://discord.com/api/webhooks/...", autocomplete: "off" });
  const hookErr = h("div", { class: "err" });
  const historyBox = h("div", { class: "list" }, h("div", { class: "muted small" }, "Loading..."));
  const account = [];
  if (u.has_password) {
    const cur = h("input", { type: "password", autocomplete: "current-password", id: "cur" });
    const nw = h("input", { type: "password", autocomplete: "new-password", id: "nw" });
    const perr = h("div", { class: "err" });
    account.push(h("details", { class: "more" }, h("summary", {}, "Change password"),
      h("form", { onsubmit: async (e) => {
        e.preventDefault(); perr.textContent = "";
        try { await api("/api/password", { current: cur.value, password: nw.value }); cur.value = nw.value = ""; toast("Password changed. Other devices were signed out."); }
        catch (x) { perr.textContent = x.message; }
      } }, h("label", { class: "f", for: "cur" }, "Current password"), cur, h("label", { class: "f", for: "nw" }, "New password"), nw, perr,
        h("button", { class: "primary wide", type: "submit" }, "Change password"))));
  }
  account.push(h("div", { class: "row", style: null },
    h("button", { onclick: async () => { await api("/api/logout", {}); ME = null; go("#/login"); } }, "Log out"),
    h("button", { class: "danger", onclick: deleteSheet }, "Delete my account")));

  show(
    h("div", { class: "card" }, h("h2", {}, "Phone alerts"), phoneSetup(false),
      h("details", { class: "more" }, h("summary", { class: "small" }, "Someone else knows my topic"),
        h("p", { class: "small muted" }, "Get a new private topic. You will need to subscribe to the new one in the ntfy app."),
        h("button", { onclick: async () => { if (!confirm("Replace your topic? The old one stops getting alerts.")) return; await api("/api/settings", { new_topic: true }); await loadMe(); render(); toast("New topic ready. Subscribe to it in ntfy."); } }, "Get a new topic"))),
    h("div", { class: "card" }, h("h2", {}, "Also send to Discord or Slack (optional)"),
      h("p", { class: "small muted" }, u.webhook ? `Connected to ${u.webhook}.` : "Paste an incoming webhook URL to get alerts in a channel too."),
      h("form", { onsubmit: async (e) => {
        e.preventDefault(); hookErr.textContent = "";
        try { await api("/api/settings", { webhook: hook.value.trim() }); hook.value = ""; await loadMe(); render(); toast("Saved"); }
        catch (x) { hookErr.textContent = x.message; }
      } }, hook, hookErr,
        h("div", { class: "row" }, h("button", { class: "primary", type: "submit" }, "Save"),
          u.webhook ? h("button", { type: "button", class: "ghost", onclick: async () => { await api("/api/settings", { webhook: "" }); await loadMe(); render(); toast("Removed"); } }, "Remove") : null))),
    h("div", { class: "card" }, h("h2", {}, "Recent messages"), historyBox),
    h("div", { class: "card" }, h("h2", {}, "Account"),
      h("p", { class: "small muted" }, `Signed in as ${u.username || u.email || u.name}.`), ...account),
  );
  try {
    const r = await api("/api/history");
    historyBox.replaceChildren(...(r.items.length ? r.items.map((i) => h("div", { class: "li" },
      h("div", { class: "main" }, h("div", {}, i.kind === "test" ? "Test message" : `${i.product} at ${i.pincodes}`),
        h("div", { class: "muted small" }, `${ago(i.ts)}: ${i.result}`)))) : [h("div", { class: "muted small" }, "Nothing sent yet.")]));
  } catch { historyBox.replaceChildren(h("div", { class: "muted small" }, "Could not load.")); }
}

function deleteSheet() {
  const pw = h("input", { type: "password", autocomplete: "current-password", id: "delpw" });
  const err = h("div", { class: "err" });
  openSheet(h("form", { onsubmit: async (e) => {
    e.preventDefault();
    try { await api("/api/account/delete", { password: pw.value }); closeSheet(); ME = null; go("#/signup"); toast("Your account and alerts were deleted."); }
    catch (x) { err.textContent = x.message; }
  } }, h("h2", {}, "Delete your account?"),
    h("p", { class: "muted" }, "Your alerts and history are removed right away. This cannot be undone."),
    ME.user.has_password ? [h("label", { class: "f", for: "delpw" }, "Password"), pw] : null, err,
    h("button", { class: "primary wide", type: "submit" }, "Delete everything"),
    h("button", { type: "button", class: "ghost wide", onclick: closeSheet }, "Keep my account")));
}

// ------------------------------------------------------------------ admin

async function adminView() {
  show(h("div", { class: "card" }, "Loading..."));
  let d;
  try { d = await api("/api/admin"); } catch (x) { show(h("div", { class: "card" }, x.message)); return; }
  const c = d.counts, run = d.last_run;
  const act = (id, body, confirmText) => async () => {
    if (confirmText && !confirm(confirmText)) return;
    try { await api(`/api/admin/users/${id}`, body); toast("Done"); adminView(); } catch (x) { toast(x.message, true); }
  };
  const users = d.users.map((u) => h("div", { class: "li" },
    h("div", { class: "main" },
      h("div", {}, h("b", {}, u.display_name), " ", h("span", { class: "muted small" }, u.username ? `@${u.username}` : u.email || ""), " ",
        u.role === "admin" ? h("span", { class: "pill admin" }, "admin") : null, u.disabled ? h("span", { class: "pill off" }, "disabled") : null),
      h("div", { class: "muted small" }, `${u.watches} alerts, joined ${ago(u.created_at)}, last login ${ago(u.last_login_at)}`)),
    u.id === ME.user.id ? h("span", { class: "muted small" }, "you") : h("div", { class: "row" },
      h("button", { onclick: act(u.id, { disabled: !u.disabled }) }, u.disabled ? "Enable" : "Disable"),
      h("button", { class: "ghost", onclick: act(u.id, { role: u.role === "admin" ? "user" : "admin" }, u.role === "admin" ? null : `Make ${u.display_name} an admin? Admins see and manage everyone.`) }, u.role === "admin" ? "Remove admin" : "Make admin"),
      h("button", { class: "danger", onclick: act(u.id, { delete: true }, `Delete ${u.display_name} and all their alerts?`) }, "Delete"))));
  const purl = h("input", { type: "url", placeholder: "https://shop.amul.com/en/product/...", id: "purl" });
  const plabel = h("input", { type: "text", placeholder: "Short name people will see", id: "plabel", maxlength: "80" });
  const products = d.products.map((p) => h("div", { class: "li" },
    h("div", { class: "main" }, h("div", {}, p.label, " ", p.enabled ? null : h("span", { class: "pill off" }, "hidden")),
      h("div", { class: "muted small" }, `${p.watches} watching`)),
    p.enabled ? h("button", { class: "ghost", onclick: async () => { await api("/api/admin/products", { alias: p.alias, remove: true }); adminView(); } }, "Hide")
      : h("button", { onclick: async () => { await api("/api/admin/products", { alias: p.alias, label: p.label }); adminView(); } }, "Show")));
  show(
    h("div", { class: "card" }, h("h2", {}, "Overview"),
      h("div", { class: "kv" },
        h("div", { class: "tile" }, h("b", {}, `${c.users}/${c.max_users}`), h("span", {}, "people")),
        h("div", { class: "tile" }, h("b", {}, c.watches), h("span", {}, "alerts")),
        h("div", { class: "tile" }, h("b", {}, c.pairs), h("span", {}, "pincode x product checks")),
        h("div", { class: "tile" }, h("b", {}, c.alerts_24h), h("span", {}, "alerts sent in 24 h"))),
      h("p", { class: "small muted" }, run ? `Last check ${ago(run.ts)}: ${run.pincodes} of ${run.pincodes_total} pincodes, ${run.checks} products, ${run.alerts} alerts${run.errors.length ? `, errors: ${run.errors.join("; ")}` : ""}.` : "No check has run yet.")),
    h("div", { class: "card" }, h("h2", {}, `People (${c.users})`), h("div", { class: "list" }, users)),
    h("div", { class: "card" }, h("h2", {}, "Products people can pick"), h("div", { class: "list" }, products),
      h("form", { onsubmit: async (e) => {
        e.preventDefault();
        try { await api("/api/admin/products", { alias: purl.value, label: plabel.value }); purl.value = plabel.value = ""; toast("Added"); adminView(); } catch (x) { toast(x.message, true); }
      } }, h("label", { class: "f", for: "purl" }, "Add a product"), purl, h("div", { style: null }, h("label", { class: "f", for: "plabel" }, "Name"), plabel),
        h("button", { class: "primary wide", type: "submit" }, "Add product"))),
    h("div", { class: "card" }, h("h2", {}, "Recent alerts"), h("div", { class: "list" },
      d.recent.length ? d.recent.map((r) => h("div", { class: "li" }, h("div", { class: "main" },
        h("div", {}, `${r.display_name}: ${r.kind === "test" ? "test message" : `${r.product} at ${r.pincodes}`}`),
        h("div", { class: "muted small" }, `${ago(r.ts)}: ${r.result}`)))) : h("div", { class: "muted small" }, "None yet."))),
  );
}

// ------------------------------------------------------------------ router

async function loadMe() {
  try { ME = await api("/api/me"); } catch (x) { if (x.status === 401) ME = null; else throw x; }
  return ME;
}

function chrome() {
  const signedIn = Boolean(ME);
  $("#tabs").hidden = !signedIn;
  $("#who").hidden = !signedIn;
  if (signedIn) {
    $("#who").textContent = ME.user.name;
    $("#adminTab").hidden = ME.user.role !== "admin";
  }
  const route = (location.hash.slice(2).split("?")[0] || "");
  for (const a of document.querySelectorAll("#tabs a")) {
    const on = (a.dataset.tab === "alerts" && route === "") || a.dataset.tab === route;
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
}

async function render() {
  closeSheet();
  const route = location.hash.slice(2).split("?")[0];
  if (!ME) { chrome(); return authView(route === "login" ? "login" : "signup"); }
  if (route === "login" || route === "signup") return go("#/");
  chrome();
  if (route === "setup") return setupView();
  if (route === "settings") return settingsView();
  if (route === "admin" && ME.user.role === "admin") return adminView();
  return alertsView();
}

window.addEventListener("hashchange", render);
(async function boot() {
  try { CONFIG = await api("/api/config"); } catch { /* defaults */ }
  await loadMe().catch(() => null);
  if (ME && !ME.watches.length && !location.hash.includes("settings") && !location.hash.includes("admin")) { location.hash = "#/setup"; }
  if (!ME && !/^#\/(login|signup)/.test(location.hash)) { location.hash = "#/signup"; }
  render();
  setInterval(async () => { if (ME && !document.hidden && (location.hash === "#/" || location.hash === "")) { await loadMe(); if (ME) alertsView(); } }, 60000);
})();
