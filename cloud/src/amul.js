// shop.amul.com client for Workers. Same protocol as amul_watch/client.py:
//   * an anonymous session cookie from the homepage (no login)
//   * a `tid` header signed like the shop's own page does
//   * the session's region (store + geolocation) selects whose stock is returned,
//     so it is switched to each pincode before that pincode's products are read
// The cookie jar lives in D1 `meta` so every cron run reuses one session.

const BASE = "https://shop.amul.com";
const STORE_ID = "62fa94df8c13af2e242eba16";
const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36";

export class AmulError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
  }
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// fetch() rejects with a bare TypeError on DNS, TLS or connection failures; give it a
// message a person can act on, and keep it an AmulError so callers handle it.
async function net(url, init) {
  try {
    return await fetch(url, init);
  } catch (e) {
    throw new AmulError(`could not reach shop.amul.com (${e.message || "network error"})`, 503);
  }
}

export class AmulClient {
  constructor(db, budget) {
    this.db = db;
    this.budget = budget; // shared subrequest counter: { left: n }
    this.jar = {};
    this.activePin = null;
    this.gapMs = 0; // minimum time between two requests to the shop
    this.lastAt = 0;
  }

  async pace() {
    const wait = this.lastAt + this.gapMs - Date.now();
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    this.lastAt = Date.now();
  }

  async load() {
    const row = await this.db.prepare("SELECT value FROM meta WHERE key = 'amul_jar'").first();
    if (row) {
      try {
        const saved = JSON.parse(row.value);
        this.jar = saved.jar || {};
        this.activePin = saved.pin || null;
      } catch {
        this.jar = {};
      }
    }
  }

  saveStmt() {
    return this.db
      .prepare("INSERT INTO meta (key, value) VALUES ('amul_jar', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value")
      .bind(JSON.stringify({ jar: this.jar, pin: this.activePin }));
  }

  async save() {
    await this.saveStmt().run();
  }

  spend() {
    if (this.budget.left <= 0) throw new AmulError("subrequest budget used up for this run");
    this.budget.left -= 1;
  }

  absorb(res) {
    const all = typeof res.headers.getSetCookie === "function" ? res.headers.getSetCookie() : [];
    for (const line of all) {
      const pair = line.split(";")[0];
      const i = pair.indexOf("=");
      if (i > 0) this.jar[pair.slice(0, i).trim()] = pair.slice(i + 1);
    }
  }

  cookie() {
    return Object.entries(this.jar).map(([k, v]) => `${k}=${v}`).join("; ");
  }

  async headers(referer, json) {
    const ts = String(Date.now());
    const rand = String(100 + Math.floor(Math.random() * 900));
    const h = {
      "User-Agent": UA,
      Accept: "application/json, text/plain, */*",
      "Accept-Language": "en-GB,en;q=0.9",
      frontend: "1",
      tid: `${ts}:${rand}:${await sha256Hex(`${STORE_ID}:${ts}:${rand}:bootstrap`)}`,
      base_url: referer,
      Referer: referer,
      Origin: BASE,
      "x-amul-b2c-access-key": "shop.amul.com",
      Cookie: this.cookie(),
    };
    if (json) h["Content-Type"] = "application/json";
    return h;
  }

  async bootstrap() {
    this.spend();
    this.jar = {};
    this.activePin = null;
    await this.pace();
    const res = await net(`${BASE}/en/`, { headers: { "User-Agent": UA }, redirect: "manual" });
    this.absorb(res);
    await res.body?.cancel();
    if (!this.jar.jsessionid) throw new AmulError(`could not start an Amul session (HTTP ${res.status})`, res.status);
  }

  async request(method, path, { params, body, referer = `${BASE}/` } = {}) {
    if (!this.jar.jsessionid) await this.bootstrap();
    const url = `${BASE}${path}${params ? "?" + new URLSearchParams(params) : ""}`;
    for (let attempt = 0; attempt < 2; attempt++) {
      this.spend();
      await this.pace();
      const res = await net(url, {
        method,
        headers: await this.headers(referer, body !== undefined),
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      this.absorb(res);
      const text = await res.text();
      if (res.status === 401 || res.status === 403 || text === "Unauthorized") {
        if (attempt === 0) {
          // Anonymous sessions expire; a fresh one is one homepage fetch away. It starts in
          // the default region, so the caller's pincode must be selected again.
          const wanted = this.activePin;
          await this.bootstrap();
          if (wanted && path.includes("ms.products")) throw new AmulError("session renewed, reselect pincode", 409);
          continue;
        }
        throw new AmulError("Amul refused the session", res.status);
      }
      if (res.status >= 400) throw new AmulError(`HTTP ${res.status}`, res.status);
      if (!text.trim()) return {};
      try {
        return JSON.parse(text);
      } catch {
        if (res.status < 300) return { message: text.slice(0, 80) };
        throw new AmulError("non-JSON answer from Amul", res.status);
      }
    }
    throw new AmulError("request failed");
  }

  async lookupPincode(pincode) {
    const data = await this.request("GET", "/entity/pincode", {
      params: {
        limit: "50",
        "filters[0][field]": "pincode",
        "filters[0][value]": pincode,
        "filters[0][operator]": "regex",
        cf_cache: "1h",
      },
    });
    const rec = (data.records || [])[0];
    if (!rec || !rec._id) return null;
    const sub = rec.substore;
    const store = typeof sub === "object" && sub ? sub.alias || sub.name || sub._id : sub;
    if (!store) return null;
    return { record_id: String(rec._id), store: String(store) };
  }

  async usePincode(pincode, store) {
    if (this.activePin === pincode) return;
    this.activePin = null; // unknown until both calls succeed
    const ref = `${BASE}/en/cart`;
    await this.request("PUT", "/entity/ms.settings/_/setPreferences", { body: { data: { store } }, referer: ref });
    await this.request("PUT", "/entity/ms.settings/_/setPreferences", {
      body: { data: { geolocation: { zip: pincode, zip_code: pincode, postal_code: pincode, pin_code: pincode, source: "browser", time: Date.now() } } },
      referer: ref,
    });
    this.activePin = pincode;
  }

  async product(alias, recordId) {
    const data = await this.request("GET", "/api/1/entity/ms.products", {
      // Only the fields parseStock reads: about 1.2 KB instead of 7.4 KB, which matters
      // inside the free plan's 10 ms CPU budget per run.
      params: {
        q: JSON.stringify({ alias }), limit: "1", substore: recordId, v: "5",
        "fields[name]": "1", "fields[alias]": "1", "fields[sku]": "1", "fields[available]": "1",
        "fields[inventory_quantity]": "1", "fields[price]": "1", "fields[variants]": "1",
      },
      referer: `${BASE}/en/product/${alias}`,
    });
    return (data.data || data.records || [])[0] || null;
  }
}

// Same rule as amul_watch/stock.py: prefer the pack-of-30 variant when there is one,
// otherwise the in-stock variant with the most units.
export function parseStock(product) {
  const variants = (product.variants && product.variants.length ? product.variants : [product]).map((v) => {
    let qty = Number(v.inventory_quantity ?? v.quantity ?? 0) || 0;
    const available = v.available === true || Number(v.available) > 0 || (v.available === undefined && qty > 0);
    if (qty <= 0 && available) qty = 1;
    const name = `${v.name || product.name || ""} ${v.sku || ""}`;
    return { inStock: available && qty > 0, qty, price: v.price != null ? Number(v.price) : null,
             pack30: /pack[\s-]*of[\s-]*30|_30\b/i.test(name) };
  });
  const pack = variants.find((v) => v.pack30);
  const best = pack && pack.inStock ? pack : variants.filter((v) => v.inStock).sort((a, b) => b.qty - a.qty)[0];
  return best ? { inStock: true, qty: best.qty, price: best.price } : { inStock: false, qty: 0, price: null };
}
