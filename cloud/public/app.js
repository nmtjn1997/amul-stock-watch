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
  let r;
  try { r = await fetch(path, opt); }
  catch { throw new Error("Could not reach the server. Check your internet connection and try again."); }
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

const ABBR = new Set(["up", "ncr", "mp", "ap", "hp", "jk", "uk", "ne"]);
const title = (s) => String(s || "").replace(/[-_]/g, " ").replace(/\b\w+/g, (w) => ABBR.has(w.toLowerCase()) ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1));

function ago(ts) {
  if (!ts) return "not yet";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

// Views pass optional sections as null; drop them rather than rendering the word "null".
const present = (nodes) => nodes.flat().filter((n) => n !== null && n !== undefined && n !== false);
const show = (...nodes) => { $("#view").replaceChildren(...present(nodes)); window.scrollTo(0, 0); };
const go = (hash) => { if (location.hash === hash) render(); else location.hash = hash; };

function busy(btn, on, label) {
  if (!btn) return;
  if (on) { btn._label = btn.textContent; btn.textContent = label || "Working..."; btn.disabled = true; }
  else { btn.textContent = btn._label || btn.textContent; btn.disabled = false; }
}

let sheetOpener = null;
function openSheet(...nodes) {
  sheetOpener = document.activeElement;
  $("#sheet").replaceChildren(...present(nodes));
  $("#scrim").hidden = false;
  const first = $("#sheet").querySelector("input,button");
  if (first) setTimeout(() => first.focus(), 50);
}
function closeSheet() {
  $("#scrim").hidden = true;
  if (sheetOpener && sheetOpener.isConnected) sheetOpener.focus();
  sheetOpener = null;
}
$("#scrim")?.addEventListener("click", (e) => { if (e.target.id === "scrim") closeSheet(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

// ------------------------------------------------------------------ auth screens

function authView(mode) {
  const signup = mode === "signup";
  const err = h("div", { class: "err", role: "alert" });
  const params = new URLSearchParams((location.hash.split("?")[1] || ""));
  if (params.get("error")) err.textContent = AUTH_ERRORS[params.get("error")] || "Sign-in did not complete. Please try again.";
  const user = h("input", { type: "text", id: "u", "aria-describedby": "u-hint", autocomplete: "username", autocapitalize: "none", spellcheck: "false", maxlength: "20", required: true });
  const pass = h("input", { type: "password", id: "p", "aria-describedby": "p-hint", autocomplete: signup ? "new-password" : "current-password", maxlength: "128", required: true });
  const btn = h("button", { class: "primary wide", type: "submit" }, signup ? "Create account" : "Log in");
  const uHint = h("div", { class: "hint", id: "u-hint" }, signup ? "3 to 20 characters: lowercase letters, digits or _. No email needed." : "");
  const pHint = h("div", { class: "hint", id: "p-hint" }, signup ? "At least 8 characters." : "");
  const eye = h("button", { type: "button", class: "ghost eye", "aria-label": "Show password",
    onclick: () => { const showing = pass.type === "text"; pass.type = showing ? "password" : "text"; eye.textContent = showing ? "Show" : "Hide"; eye.setAttribute("aria-label", showing ? "Show password" : "Hide password"); } }, "Show");
  if (signup) {
    user.addEventListener("input", () => {
      user.value = user.value.toLowerCase().replace(/\s/g, "");
      const ok = /^[a-z0-9_]{3,20}$/.test(user.value);
      uHint.className = !user.value || ok ? "hint" : "hint bad";
      user.setAttribute("aria-invalid", String(Boolean(user.value) && !ok));
      uHint.textContent = !user.value || ok ? "3 to 20 characters: lowercase letters, digits or _. No email needed."
        : /[^a-z0-9_]/.test(user.value) ? "Only lowercase letters, digits and _ are allowed." : "Use 3 to 20 characters.";
    });
    pass.addEventListener("input", () => {
      const n = pass.value.length;
      pHint.className = !n || n >= 8 ? "hint" : "hint bad";
      pass.setAttribute("aria-invalid", String(n > 0 && n < 8));
      pHint.textContent = !n ? "At least 8 characters." : n < 8 ? `${8 - n} more character${8 - n > 1 ? "s" : ""} needed.` : "Looks good.";
    });
  }
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
      if (signup && !/^[a-z0-9_]{3,20}$/.test(user.value.trim())) { err.textContent = "Pick a username of 3 to 20 lowercase letters, digits or _."; user.focus(); return; }
      if (signup && pass.value.length < 8) { err.textContent = "The password needs at least 8 characters."; pass.focus(); return; }
      if (signup && CONFIG.turnstile && !humanToken) { err.textContent = "Please complete the \"Verify you are human\" check first."; return; }
      busy(btn, true, signup ? "Creating your account..." : "Logging in...");
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
    h("label", { class: "f", for: "u" }, "Username"), user, signup ? uHint : null,
    h("label", { class: "f", for: "p" }, "Password"), h("div", { class: "pwrow" }, pass, eye), signup ? pHint : null,
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
      h("p", { class: "lead" }, "Turn on notifications and you will get a message the moment something is back. Nothing to install."),
      h("div", { class: "card" }, notifySetup(true)),
      h("button", { class: "primary wide", onclick: () => go("#/") }, "Done, show my alerts"),
    ));
  }
}

function qr(text, px = 4) {
  if (!window.qrcode) return null;
  const q = window.qrcode(0, "M");
  q.addData(text);
  q.make();
  const size = q.getModuleCount() * px + px * 4;
  return h("img", { class: "qr", src: q.createDataURL(px, px * 2), width: String(size), height: String(size), alt: `QR code for ${text}` });
}

// One QR at a time, hidden until asked for. The first choice is shown first.
function qrReveal(choices) {
  const out = h("div", {});
  const pick = (i) => out.replaceChildren(
    choices.length > 1 ? h("div", { class: "seg" }, choices.map((c, j) =>
      h("button", { type: "button", class: j === i ? "on" : null, onclick: () => pick(j) }, c.label))) : null,
    h("div", { class: "qrwrap" }, qr(choices[i].text)),
    h("p", { class: "small muted" }, choices[i].hint));
  const btn = h("button", { type: "button", class: "ghost", "aria-expanded": "false", onclick: () => {
    const open = btn.getAttribute("aria-expanded") !== "true";
    btn.setAttribute("aria-expanded", String(open));
    btn.textContent = open ? "Hide QR codes" : "Show QR codes";
    if (open) pick(0); else out.replaceChildren();
  } }, "Show QR codes");
  return h("div", {}, btn, out);
}

async function copy(text, what) {
  try { await navigator.clipboard.writeText(text); toast(`${what} copied`); }
  catch { toast("Copy did not work here. Select the text and copy it.", true); }
}

function copyRow(text, what) {
  return h("div", { class: "copyrow" }, h("code", { class: "topic" }, text),
    h("button", { type: "button", class: "ghost", "aria-label": `Copy ${what}`, onclick: () => copy(text, what) }, "Copy"));
}

const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const standalone = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

function deviceLabel() {
  const ua = navigator.userAgent;
  const os = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) || isIOS() ? "iPad" : /Android/.test(ua) ? "Android" : /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : "Computer";
  const br = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "browser";
  return `${os} ${standalone() ? "app" : br}`;
}

function keyBytes(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  return Uint8Array.from(atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
}
const b64u = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function currentSubscription() {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.getRegistration("/");
  return reg ? reg.pushManager.getSubscription() : null;
}

async function turnOnPush() {
  if (!CONFIG.vapid) throw new Error("Notifications are not set up on the server yet.");
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error(perm === "denied"
    ? "Notifications are blocked for this site. Allow them in your browser's site settings, then try again."
    : "You did not allow notifications. Tap the button again and choose Allow.");
  const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(CONFIG.vapid) });
  await api("/api/push/subscribe", { endpoint: sub.endpoint, p256dh: b64u(sub.getKey("p256dh")), auth: b64u(sub.getKey("auth")), label: deviceLabel() });
}

async function turnOffPush() {
  const sub = await currentSubscription();
  if (sub) {
    await api("/api/push/remove", { endpoint: sub.endpoint }).catch(() => null);
    await sub.unsubscribe().catch(() => null);
  }
}

// Every channel in one place: whether it is set up, and what the last test did there.
function channelsCard() {
  const u = ME.user;
  const rows = [
    { name: "Browser", label: "Browser notifications", on: ME.devices.length > 0, state: ME.devices.length ? `${ME.devices.length} device${ME.devices.length > 1 ? "s" : ""}` : "Off" },
    u.telegram === null ? null : { name: "Telegram", label: "Telegram", on: u.telegram, state: u.telegram ? "Connected" : "Not connected" },
    { name: u.webhook || "Slack", label: "Slack or Discord", on: Boolean(u.webhook), state: u.webhook ? `${u.webhook} connected` : "Not set" },
    { name: "ntfy", label: "ntfy app", on: u.ntfy_on, state: u.ntfy_on ? "On" : "Off" },
  ].filter(Boolean);
  for (const r of rows) r.result = h("div", { class: "small" });
  const btn = h("button", { type: "button", class: "primary", onclick: async () => {
    busy(btn, true, "Sending...");
    for (const r of rows) r.result.replaceChildren();
    try {
      const res = await api("/api/test", {});
      for (const r of rows) {
        const c = (res.channels || []).find((x) => x.name === r.name);
        r.result.className = c ? (c.ok ? "small good" : "small bad") : "small muted";
        r.result.textContent = c ? (c.ok ? `✓ ${c.detail}` : `✕ ${c.detail}`) : "Skipped, not set up";
      }
      const sent = (res.channels || []).filter((c) => c.ok).length;
      toast(res.channels && res.channels.length ? `Sent to ${sent} of ${res.channels.length}` : "Nothing is set up yet", !sent);
    } catch (x) { toast(x.message, true); }
    finally { busy(btn, false); }
  } }, "Send a test to all");
  return h("div", { class: "card" }, h("h2", {}, "Where alerts go"),
    h("div", { class: "list" }, rows.map((r) => h("div", { class: "li chan" },
      h("div", { class: "main" }, h("div", {}, r.label), r.result),
      h("span", { class: r.on ? "good small" : "muted small" }, r.state)))),
    h("div", { class: "row" }, btn));
}

function notifySetup(compact) {
  const box = h("div", {});
  const status = h("div", { class: "hint" });
  const testBtn = h("button", { type: "button", onclick: async () => {
    busy(testBtn, true, "Sending...");
    try { const r = await api("/api/test", {}); toast(r.ok ? `Test sent: ${r.detail}` : `Not delivered: ${r.detail}`, !r.ok); }
    catch (x) { toast(x.message, true); } finally { busy(testBtn, false); }
  } }, "Send a test");

  async function draw() {
    const here = await currentSubscription().catch(() => null);
    const onHere = Boolean(here && ME.devices.some((d) => here.endpoint.endsWith(d.endpoint_hash)));
    const parts = [];
    if (!pushSupported()) {
      if (isIOS() && !standalone()) {
        parts.push(h("div", { class: "note" }, h("b", {}, "On iPhone or iPad: "),
          "tap the Share button, then ", h("b", {}, "Add to Home Screen"), ". Open Back in Stock from the Home Screen and come back here to turn notifications on. (iOS 16.4 or newer.)"));
      } else {
        parts.push(h("div", { class: "note" }, "This browser cannot show notifications. Use Chrome, Edge, Firefox or Safari, or use ntfy below."));
      }
    } else if (onHere) {
      parts.push(h("div", { class: "ok-line" }, `Notifications are on for this device (${deviceLabel()}).`),
        h("div", { class: "row" }, compact ? testBtn : null, h("button", { type: "button", class: "ghost", onclick: async () => { await turnOffPush(); await loadMe(); draw(); toast("Turned off on this device"); } }, "Turn off here")));
    } else {
      const on = h("button", { type: "button", class: "primary wide", onclick: async () => {
        status.className = "hint"; status.textContent = "";
        busy(on, true, "Turning on...");
        try { await turnOnPush(); await loadMe(); toast("Notifications on. Sending a test..."); await api("/api/test", {}).catch(() => null); draw(); }
        catch (x) { status.className = "err"; status.textContent = x.message; }
        finally { busy(on, false); }
      } }, "Turn on notifications on this device");
      parts.push(Notification.permission === "denied" ? null : on, status);
      if (Notification.permission === "denied") {
        status.className = "err";
        status.textContent = "Notifications are blocked for this site in this browser. Allow them in the browser's site settings (the icon left of the address), then reload this page.";
      }
    }
    const others = ME.devices.filter((d) => !(here && here.endpoint.endsWith(d.endpoint_hash)));
    if (others.length) {
      parts.push(h("div", { class: "list devices" }, others.map((d) => h("div", { class: "li" },
        h("div", { class: "main" }, h("div", {}, d.label), h("div", { class: "muted small" }, `added ${ago(d.created_at)}`)),
        h("button", { type: "button", class: "ghost", onclick: async () => { await api("/api/push/remove", { id: d.id }); await loadMe(); draw(); toast("Removed"); } }, "Remove")))));
      if (!onHere && compact) parts.push(h("div", { class: "row" }, testBtn));
    }
    if (!compact || !isPhone()) {
      parts.push(h("details", { class: "more" }, h("summary", {}, "Open this on your phone"),
        h("p", { class: "small muted" }, "Scan with the phone's camera, log in there, and turn notifications on."),
        h("div", { class: "qrwrap" }, qr(location.origin + "/")), copyRow(location.origin + "/", "Link")));
    }
    box.replaceChildren(...present(parts));
  }
  draw();
  return box;
}

const isPhone = () => /Android|iPhone|iPad|iPod/.test(navigator.userAgent) || isIOS();

function telegramSetup() {
  const u = ME.user;
  const box = h("div", {});
  const refresh = async () => { await loadMe(); render(); };
  const connect = h("button", { type: "button", class: "primary", onclick: async () => {
    busy(connect, true, "Opening...");
    try {
      const { url } = await api("/api/telegram/link", {});
      if (isPhone()) { location.href = url; }
      box.replaceChildren(
        h("p", { class: "small" }, isPhone() ? "In Telegram, press Start. Then come back here." : "Scan this with your phone, or open the link, then press Start in Telegram:"),
        isPhone() ? null : h("div", { class: "qrwrap" }, qr(url)),
        h("p", { class: "small" }, h("a", { href: url, target: "_blank", rel: "noopener" }, "Open in Telegram")),
        h("button", { type: "button", onclick: refresh }, "I pressed Start"));
    } catch (x) { toast(x.message, true); }
    finally { busy(connect, false); }
  } }, "Connect Telegram");
  if (u.telegram) {
    return h("div", {},
      h("p", { class: "small" }, "Connected. Alerts also go to your Telegram chat with our bot."),
      h("button", { type: "button", class: "ghost", onclick: async () => { await api("/api/telegram/unlink", {}); await refresh(); toast("Telegram disconnected"); } }, "Disconnect"));
  }
  return h("div", {},
    h("p", { class: "small muted" }, "Free, works on any phone with the Telegram app. One tap to link, nothing to copy."),
    connect, box);
}

function ntfySetup() {
  const u = ME.user;
  const deep = `ntfy://${u.ntfy_server.replace(/^https?:\/\//, "")}/${u.ntfy_topic}`;
  const toggle = h("button", { type: "button", class: "switch", role: "switch", "aria-checked": String(u.ntfy_on), "aria-label": "Also send to the ntfy app",
    onclick: async () => {
      try { await api("/api/settings", { ntfy_on: !u.ntfy_on }); await loadMe(); render(); toast(!u.ntfy_on ? "ntfy on" : "ntfy off"); }
      catch (x) { toast(x.message, true); }
    } });
  return h("div", {},
    h("div", { class: "row" }, h("div", { class: "grow" }, h("b", {}, "ntfy app"), h("div", { class: "muted small" }, "Free phone app. Handy if browser notifications are not an option.")), toggle),
    u.ntfy_on ? h("div", {},
      h("ol", { class: "how" },
        h("li", {}, "Install ntfy: ",
          h("a", { href: "https://play.google.com/store/apps/details?id=io.heckel.ntfy", rel: "noopener", target: "_blank" }, "Android"), " or ",
          h("a", { href: "https://apps.apple.com/app/ntfy/id1625396347", rel: "noopener", target: "_blank" }, "iPhone"), "."),
        h("li", {}, isPhone() ? ["Tap ", h("a", { href: deep }, "Subscribe in ntfy"), ", or add this topic in the app:"] : "Subscribe to your topic in the app:"),
      ),
      isPhone() ? null : qrReveal([
        { label: "iPhone app", text: "https://apps.apple.com/app/ntfy/id1625396347", hint: "Opens ntfy in the App Store." },
        { label: "Android app", text: "https://play.google.com/store/apps/details?id=io.heckel.ntfy", hint: "Opens ntfy in the Play Store." },
        { label: "Subscribe (Android)", text: deep, hint: "After installing, scan this to open the ntfy app on your topic. On iPhone, tap + in the app and paste the topic below." },
      ]),
      copyRow(u.ntfy_topic, "Topic"),
      h("p", { class: "hint" }, "Keep the topic private: anyone who knows it can read these alerts. The free ntfy.sh service often does not answer this site; Telegram or browser notifications are more reliable."),
    ) : null,
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
      h("div", { class: "meta" }, status(w), h("span", { class: "nowrap" }, `${w.pincode}${w.store ? ` · ${title(w.store)}` : ""}`)),
    ));
  }
  const channels = [];
  if (ME.devices.length) channels.push(ME.devices.length === 1 ? "1 device" : `${ME.devices.length} devices`);
  if (ME.user.ntfy_on) channels.push("ntfy");
  if (ME.user.telegram) channels.push("Telegram");
  if (ME.user.webhook) channels.push(ME.user.webhook);
  show(
    h("div", { class: "summary card" },
      h("div", { class: "grow" },
        h("div", { class: "count" }, `${ws.length} of ${max} alerts `, h("span", { class: "muted small nowrap" }, `· ${new Set(ws.map((w) => w.pincode)).size} of ${ME.user.max_pincodes} pincodes`)),
        h("div", { class: "muted small" }, channels.length ? `Alerts go to: ${channels.join(", ")}. Last check: ${ago(ME.last_check)}.` : `Last check: ${ago(ME.last_check)}.`)),
      addBtn),
    channels.length ? null : h("div", { class: "card warn" }, h("b", {}, "You will not get alerts yet. "),
      "Turn on notifications so we can tell you when something is back.",
      h("a", { class: "btn primary wide", href: "#/settings" }, "Turn on notifications")),
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
  const count = () => {
    const n = pickWrap.querySelectorAll("input:checked:not(:disabled)").length;
    save.textContent = n > 1 ? `Add ${n} alerts` : "Add alert";
  };
  pickWrap.addEventListener("change", count);
  function refresh() {
    const taken = new Set(ME.watches.filter((w) => w.pincode === ready).map((w) => w.alias));
    pickWrap.replaceChildren(h("label", { class: "f" }, "Products"), productPicker(taken));
    count();
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
    h("h2", { id: "sheet-title" }, "New alert"),
    h("label", { class: "f", for: "pin" }, "Pincode"),
    h("div", { class: "row" }, h("div", { class: "grow" }, pf.input), h("button", { type: "button", onclick: pf.check }, "Check")),
    pf.out, pickWrap,
    h("div", { class: "sheet-foot" }, err, save,
      h("button", { type: "button", class: "ghost wide", onclick: closeSheet }, "Cancel")),
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
    channelsCard(),
    h("div", { class: "card" }, h("h2", {}, "Browser notifications"), notifySetup(false)),
    u.telegram === null ? null : h("div", { class: "card" }, h("h2", {}, "Telegram (optional)"), telegramSetup()),
    h("div", { class: "card" }, h("h2", {}, "Other ways to get alerts (optional)"), ntfySetup(),
      h("details", { class: "more" }, h("summary", { class: "small" }, "Someone else knows my ntfy topic"),
        h("p", { class: "small muted" }, "Get a new private topic. You will need to subscribe to the new one in the ntfy app."),
        h("button", { onclick: async () => { if (!confirm("Replace your topic? The old one stops getting alerts.")) return; await api("/api/settings", { new_topic: true }); await loadMe(); render(); toast("New topic ready. Subscribe to it in ntfy."); } }, "Get a new topic"))),
    h("div", { class: "card" }, h("h2", {}, "Slack or Discord (optional)"),
      h("p", { class: "small muted" }, u.webhook ? `Connected to ${u.webhook}. Use "Send a test" above to check it.` : "Paste an incoming webhook URL to get alerts in a channel too."),
      h("details", { class: "more" }, h("summary", { class: "small" }, "How do I get a Discord webhook URL?"),
        h("ol", { class: "how small" },
          h("li", {}, "In Discord, open the server, then ", h("b", {}, "Server Settings > Integrations > Webhooks"), " (you need Manage Webhooks permission)."),
          h("li", {}, "Click ", h("b", {}, "New Webhook"), ", pick the channel, then ", h("b", {}, "Copy Webhook URL"), "."),
          h("li", {}, "Paste it below and press Save. It starts with https://discord.com/api/webhooks/.")),
        h("a", { href: "https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks", target: "_blank", rel: "noopener" }, "Discord's guide")),
      h("details", { class: "more" }, h("summary", { class: "small" }, "How do I get a Slack webhook URL?"),
        h("ol", { class: "how small" },
          h("li", {}, "Open ", h("a", { href: "https://api.slack.com/apps?new_app=1", target: "_blank", rel: "noopener" }, "api.slack.com/apps"), " and create an app ", h("b", {}, "From scratch"), " in your workspace."),
          h("li", {}, "In the app, open ", h("b", {}, "Incoming Webhooks"), ", switch it on, then ", h("b", {}, "Add New Webhook to Workspace"), " and pick a channel."),
          h("li", {}, "Copy the URL (it starts with https://hooks.slack.com/services/) and paste it below.")),
        h("a", { href: "https://api.slack.com/messaging/webhooks", target: "_blank", rel: "noopener" }, "Slack's guide")),
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
    const row = (i) => h("div", { class: "li" },
      h("div", { class: "main" }, h("div", {}, i.kind === "test" ? "Test message" : `${i.product} at ${i.pincodes}`),
        h("div", { class: "muted small" }, `${ago(i.ts)}: ${i.result}`)));
    const more = r.items.length > 5 ? h("button", { type: "button", class: "ghost", onclick: () => { historyBox.replaceChildren(...r.items.map(row)); } }, `Show all ${r.items.length}`) : null;
    historyBox.replaceChildren(...(r.items.length ? [...r.items.slice(0, 5).map(row), more].filter(Boolean) : [h("div", { class: "muted small" }, "Nothing sent yet.")]));
  } catch { historyBox.replaceChildren(h("div", { class: "muted small" }, "Could not load.")); }
}

function deleteSheet() {
  const pw = h("input", { type: "password", autocomplete: "current-password", id: "delpw" });
  const err = h("div", { class: "err" });
  openSheet(h("form", { onsubmit: async (e) => {
    e.preventDefault();
    try { await api("/api/account/delete", { password: pw.value }); closeSheet(); ME = null; go("#/signup"); toast("Your account and alerts were deleted."); }
    catch (x) { err.textContent = x.message; }
  } }, h("h2", { id: "sheet-title" }, "Delete your account?"),
    h("p", { class: "muted" }, "Your alerts and history are removed right away. This cannot be undone."),
    ME.user.has_password ? [h("label", { class: "f", for: "delpw" }, "Password"), pw] : null, err,
    h("button", { class: "primary wide", type: "submit" }, "Delete everything"),
    h("button", { type: "button", class: "ghost wide", onclick: closeSheet }, "Keep my account")));
}

// ------------------------------------------------------------------ admin

const adminState = { section: "monitor", group: "", search: "", events: [], more: false };

function svgEl(tag, attrs) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}

// Bars for a series over time. `rows` = [{ key, value, title }]; empty slots are drawn flat.
function barChart(rows, { height = 90, color = "var(--accent)", label = "", from = "", to = "" } = {}) {
  const peak = Math.max(0, ...rows.map((r) => r.value));
  const max = Math.max(1, peak);
  const w = 100 / Math.max(rows.length, 1);
  const svg = svgEl("svg", { viewBox: `0 0 100 ${height}`, preserveAspectRatio: "none", class: "chart", role: "img", "aria-label": label });
  rows.forEach((r, i) => {
    const bh = r.value ? Math.max(1.5, (r.value / max) * (height - 4)) : 0.8;
    const rect = svgEl("rect", { x: String(i * w + w * 0.15), y: String(height - bh), width: String(w * 0.7), height: String(bh), rx: "0.6",
      fill: r.value ? color : "var(--line)" });
    const t = svgEl("title", {});
    t.textContent = r.title;
    rect.append(t);
    svg.append(rect);
  });
  return h("div", { class: "chartbox" },
    h("div", { class: "chart-head" }, h("span", {}, label), h("span", { class: "muted small" }, peak ? `peak ${peak}` : "none")), svg,
    from || to ? h("div", { class: "chart-foot" }, h("span", {}, from), h("span", {}, to)) : null);
}

function hourlySeries(hourly, now, field) {
  const byHour = new Map(hourly.map((r) => [r.hour, r]));
  const cur = Math.floor(now / 3600);
  const out = [];
  for (let hh = cur - 23; hh <= cur; hh++) {
    const r = byHour.get(hh);
    const v = r ? r[field] || 0 : 0;
    const d = new Date(hh * 3600 * 1000);
    out.push({ key: hh, value: v, title: `${d.toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}: ${v}` });
  }
  return out;
}

function dailySeries(daily, now) {
  const byDay = new Map(daily.map((r) => [r.day, r]));
  const today = Math.floor(now / 86400);
  const out = [];
  for (let d = today - 6; d <= today; d++) {
    const r = byDay.get(d);
    out.push({ key: d, value: r ? r.amul : 0, title: `${new Date(d * 86400000).toLocaleDateString()}: ${r ? r.amul : 0} Amul requests, ${r ? r.alerts : 0} alerts` });
  }
  return out;
}

async function adminView() {
  const sections = [["monitor", "Monitor"], ["people", "People"], ["products", "Products"], ["activity", "Activity"]];
  const nav = h("div", { class: "seg", role: "tablist" }, sections.map(([k, label]) =>
    h("button", { type: "button", role: "tab", "aria-selected": String(adminState.section === k), class: adminState.section === k ? "on" : "",
      onclick: () => { adminState.section = k; adminView(); } }, label)));
  const body = h("div", {}, h("div", { class: "card" }, "Loading..."));
  show(nav, body);
  try {
    if (adminState.section === "monitor") body.replaceChildren(...present(await monitorSection()));
    else if (adminState.section === "activity") body.replaceChildren(...present(await activitySection()));
    else body.replaceChildren(...present(await peopleProductsSection(adminState.section)));
  } catch (x) {
    body.replaceChildren(h("div", { class: "card warn" }, x.message));
  }
}

async function monitorSection() {
  const m = await api("/api/admin/monitor");
  const st = m.health.status;
  const label = { ok: "Healthy", late: "Running late", failing: "Failing", down: "Not running", unknown: "No runs yet" }[st];
  const age = m.health.last_run_age;
  const lr = m.health.last_run || {};
  const d = m.last_day, hr = m.last_hour;
  const ev = m.events_24h;
  const tile = (value, text, cls = "") => h("div", { class: `tile ${cls}` }, h("b", {}, String(value)), h("span", {}, text));
  const amulPerMin = hr.runs ? Math.round(hr.amul / hr.runs) : 0;
  const runsRows = m.runs.map((r) => h("div", { class: "li run" },
    h("div", { class: "main" },
      h("div", {}, h("span", { class: `dotstat ${r.errors.length ? (r.checks ? "warn" : "bad") : "ok"}` }), new Date(r.ts * 1000).toLocaleTimeString(),
        h("span", { class: "muted small" }, `  ${r.pincodes}/${r.units_total} groups, ${r.checks} checks, ${r.amul_requests} Amul requests, ${r.alerts} alerts, ${r.ms} ms`)),
      r.errors.length ? h("div", { class: "small errtext" }, r.errors.slice(0, 3).join("; ")) : null)));
  return [
    h("div", { class: `card health ${st}` },
      h("div", { class: "row" },
        h("span", { class: `dotstat big ${st === "ok" ? "ok" : st === "late" ? "warn" : "bad"}` }),
        h("div", { class: "grow" }, h("b", {}, `Poller: ${label}`),
          h("div", { class: "small muted" }, age === null ? "The every-minute check has not run yet." :
            `Last run ${ago(m.now - age)}: ${lr.pincodes ?? 0} of ${lr.pincodes_total ?? 0} groups, ${lr.checks ?? 0} product checks, ${lr.amul_requests ?? 0} Amul requests.`)),
        h("a", { class: "btn ghost", href: m.dashboard, target: "_blank", rel: "noopener" }, "Raw logs"))),
    h("div", { class: "card" }, h("h2", {}, "Last 24 hours"),
      h("div", { class: "kv" },
        tile(d.amul, "requests to Amul"),
        tile(`${amulPerMin}/min`, "Amul load, last hour"),
        tile(d.checks, "product checks"),
        tile(d.alerts, "restock alerts sent"),
        tile(ev.alert_failed || 0, "alerts not delivered", ev.alert_failed ? "warn" : ""),
        tile(m.errors_24h, "errors", m.errors_24h ? "bad" : ""),
        tile(m.active_users_24h, "active people"),
        tile(ev.signup || 0, "new sign-ups"),
        tile(ev.login_fail || 0, "failed logins", (ev.login_fail || 0) > 20 ? "warn" : ""),
        tile(`${d.avg_ms} ms`, `avg run, max ${d.max_ms} ms`),
        tile(`${d.runs}/1440`, "runs completed"),
        tile(d.max_units, "pincode groups waiting")),
      h("p", { class: "small muted" }, `Budget: at most ${m.limits.amul_per_run} Amul requests per run, one run a minute, shared by everyone. Each pincode and product is read once for all the people watching it.`)),
    h("div", { class: "card" }, h("h2", {}, "Per hour"),
      barChart(hourlySeries(m.hourly, m.now, "amul"), { label: "Amul requests", from: "24 h ago", to: "now" }),
      barChart(hourlySeries(m.hourly, m.now, "checks"), { label: "Product checks", color: "var(--ok)", from: "24 h ago", to: "now" }),
      barChart(hourlySeries(m.hourly, m.now, "alerts"), { label: "Alerts sent", color: "#d68a00", from: "24 h ago", to: "now" }),
      barChart(hourlySeries(m.hourly, m.now, "error_runs"), { label: "Runs with errors", color: "var(--bad)", from: "24 h ago", to: "now" })),
    h("div", { class: "card" }, h("h2", {}, "Last 7 days"),
      barChart(dailySeries(m.daily, m.now), { label: "Amul requests per day", from: "7 days ago", to: "today" })),
    h("div", { class: "card" }, h("h2", {}, "Recent runs"), h("div", { class: "list" }, runsRows.length ? runsRows : h("div", { class: "muted small" }, "No runs yet."))),
  ];
}

const KIND_LABEL = {
  signup: "Signed up", login: "Logged in", login_fail: "Login failed", login_blocked: "Login blocked", password_change: "Changed password",
  account_delete: "Deleted account", watch_add: "Added alert", watch_delete: "Deleted alert", watch_toggle: "Paused or resumed alert",
  settings: "Changed settings", device_add: "Turned on notifications", device_remove: "Removed a device", pincode_check: "Checked a pincode",
  test_sent: "Test message", alert_sent: "Alert sent", alert_failed: "Alert not delivered", admin: "Admin action", poll_error: "Poller error", error: "Server error",
};

async function activitySection() {
  const load = async (reset) => {
    const q = new URLSearchParams();
    if (adminState.group) q.set("group", adminState.group);
    if (adminState.search) q.set("search", adminState.search);
    if (!reset && adminState.events.length) q.set("before", adminState.events[adminState.events.length - 1].id);
    const r = await api(`/api/admin/events?${q}`);
    adminState.events = reset ? r.items : adminState.events.concat(r.items);
    adminState.more = r.more;
  };
  await load(true);
  const list = h("div", { class: "list" });
  const moreBtn = h("button", { class: "ghost wide", onclick: async () => { await load(false); draw(); } }, "Load older");
  function draw() {
    list.replaceChildren(...(adminState.events.length ? adminState.events.map((e) => h("div", { class: "li" },
      h("span", { class: `lvl ${e.level}` }, e.level),
      h("div", { class: "main" },
        h("div", {}, h("b", {}, KIND_LABEL[e.kind] || e.kind), " ", h("span", { class: "muted small" }, e.actor || "")),
        h("div", { class: "small muted" }, `${new Date(e.ts * 1000).toLocaleString()}${e.net ? `  network ${e.net}` : ""}`),
        e.detail ? h("div", { class: "small detail" }, e.detail) : null))) : [h("div", { class: "muted small" }, "Nothing matches.")]));
    moreBtn.hidden = !adminState.more;
  }
  draw();
  const groups = [["", "All"], ["errors", "Problems"], ["alerts", "Alerts"], ["signins", "Sign-ins"], ["changes", "Changes"], ["admin", "Admin"], ["system", "System"]];
  const search = h("input", { type: "text", placeholder: "Search user, product, pincode...", value: adminState.search, id: "evsearch" });
  let timer;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(async () => { adminState.search = search.value.trim(); await load(true); draw(); }, 300); });
  return [
    h("div", { class: "card" },
      h("div", { class: "chips" }, groups.map(([k, label]) => h("button", { type: "button", class: adminState.group === k ? "chip on" : "chip",
        onclick: async () => { adminState.group = k; adminView(); } }, label))),
      search, list, moreBtn,
      h("p", { class: "small muted" }, "Kept for 30 days. \"Network\" is a one-way hash that changes daily: it shows when many events come from one place, without storing IP addresses.")),
  ];
}

async function peopleProductsSection(which) {
  const d = await api("/api/admin");
  const c = d.counts;
  const act = (id, body, confirmText) => async () => {
    if (confirmText && !confirm(confirmText)) return;
    try { await api(`/api/admin/users/${id}`, body); toast("Done"); adminView(); }
    catch (x) { alert(x.message); }
  };
  if (which === "people") {
    const users = d.users.map((u) => {
      const owner = u.username === d.owner;
      return h("div", { class: "li" },
        h("div", { class: "main" },
          h("div", {}, h("b", {}, u.display_name), " ", h("span", { class: "muted small" }, u.username ? `@${u.username}` : u.email || ""), " ",
            owner ? h("span", { class: "pill admin" }, "owner") : u.role === "admin" ? h("span", { class: "pill admin" }, "admin") : null,
            u.disabled ? h("span", { class: "pill off" }, "disabled") : null),
          h("div", { class: "muted small" }, `${u.watches} alerts, joined ${ago(u.created_at)}, last login ${ago(u.last_login_at)}`)),
        owner || u.id === ME.user.id ? h("span", { class: "muted small" }, u.id === ME.user.id ? "you" : "protected") : h("div", { class: "row" },
          h("button", { onclick: act(u.id, { disabled: !u.disabled }) }, u.disabled ? "Enable" : "Disable"),
          h("button", { class: "ghost", onclick: act(u.id, { role: u.role === "admin" ? "user" : "admin" }, u.role === "admin" ? null : `Make ${u.display_name} an admin? Admins see and manage everyone.`) }, u.role === "admin" ? "Remove admin" : "Make admin"),
          h("button", { class: "danger", onclick: act(u.id, { delete: true }, `Delete ${u.display_name} and all their alerts? This cannot be undone.`) }, "Delete")));
    });
    return [h("div", { class: "card" }, h("h2", {}, `People (${c.users} of ${c.max_users})`),
      h("p", { class: "small muted" }, `${c.watches} alerts in total, ${c.pairs} distinct pincode and product checks.`), h("div", { class: "list" }, users))];
  }
  const purl = h("input", { type: "url", placeholder: "https://shop.amul.com/en/product/...", id: "purl" });
  const plabel = h("input", { type: "text", placeholder: "Short name people will see", id: "plabel", maxlength: "80" });
  // One request at a time per button; errors show instead of failing silently.
  const pAct = (body) => async (e) => {
    const b = e.currentTarget;
    busy(b, true, "…");
    try { await api("/api/admin/products", body); adminView(); }
    catch (x) { toast(x.message, true); busy(b, false); }
  };
  const last = d.products.length - 1;
  const products = d.products.map((p, i) => h("div", { class: "li" },
    h("div", { class: "main" }, h("div", {}, p.label, " ", p.enabled ? null : h("span", { class: "pill off" }, "hidden")),
      h("div", { class: "muted small" }, `${p.watches} watching`)),
    h("div", { class: "row" },
      h("button", { class: "ghost", "aria-label": `Move ${p.label} up`, title: "Show earlier", disabled: i === 0, onclick: pAct({ alias: p.alias, move: "up" }) }, "↑"),
      h("button", { class: "ghost", "aria-label": `Move ${p.label} down`, title: "Show later", disabled: i === last, onclick: pAct({ alias: p.alias, move: "down" }) }, "↓"),
      p.enabled ? h("button", { class: "ghost", onclick: pAct({ alias: p.alias, remove: true }) }, "Hide")
        : h("button", { onclick: pAct({ alias: p.alias, label: p.label }) }, "Show"))));
  return [h("div", { class: "card" }, h("h2", {}, "Products people can pick"), h("p", { class: "small muted" }, "Shown in this order. Use the arrows to reorder."),
    h("div", { class: "list" }, products),
    h("form", { onsubmit: async (e) => {
      e.preventDefault();
      try { await api("/api/admin/products", { alias: purl.value, label: plabel.value }); purl.value = plabel.value = ""; toast("Added"); adminView(); } catch (x) { toast(x.message, true); }
    } }, h("label", { class: "f", for: "purl" }, "Add a product"), purl, h("label", { class: "f", for: "plabel" }, "Name"), plabel,
      h("button", { class: "primary wide", type: "submit" }, "Add product")))];
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
