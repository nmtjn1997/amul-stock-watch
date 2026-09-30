// Web Push (browser and home-screen app notifications) with no third-party service:
// the message is encrypted for the browser (RFC 8291, aes128gcm) and the request is signed
// with this site's VAPID key (RFC 8292). The browser vendor's push service (Google, Apple,
// Mozilla, Microsoft) only relays ciphertext.

const enc = new TextEncoder();

export function b64u(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function unb64u(s) {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  return Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + pad), (c) => c.charCodeAt(0));
}

function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let i = 0;
  for (const p of parts) {
    out.set(p, i);
    i += p.length;
  }
  return out;
}

async function hmac(key, data) {
  const k = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, data));
}

// Push services browsers actually use. Anything else is refused, so a subscription can
// never make the Worker send requests to an arbitrary host.
const PUSH_HOSTS = [/^fcm\.googleapis\.com$/, /^updates\.push\.services\.mozilla\.com$/, /^web\.push\.apple\.com$/,
  /\.push\.apple\.com$/, /\.notify\.windows\.com$/, /^android\.googleapis\.com$/];

export function validSubscription(sub) {
  try {
    const u = new URL(sub.endpoint);
    if (u.protocol !== "https:" || u.username || u.password || u.port) return false;
    if (!PUSH_HOSTS.some((re) => re.test(u.hostname))) return false;
    return unb64u(sub.p256dh).length === 65 && unb64u(sub.auth).length === 16;
  } catch {
    return false;
  }
}

// RFC 8291 section 3.4 and 4. `asKeys` and `salt` are injectable for the RFC test vector.
export async function encrypt(payload, p256dhB64, authB64, { asKeys, salt } = {}) {
  const uaPublic = unb64u(p256dhB64);
  const authSecret = unb64u(authB64);
  const keys = asKeys || (await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]));
  const asPublic = new Uint8Array(await crypto.subtle.exportKey("raw", keys.publicKey));
  const uaKey = await crypto.subtle.importKey("raw", uaPublic, { name: "ECDH", namedCurve: "P-256" }, false, []);
  const ecdh = new Uint8Array(await crypto.subtle.deriveBits({ name: "ECDH", public: uaKey }, keys.privateKey, 256));
  const prkKey = await hmac(authSecret, ecdh);
  const ikm = await hmac(prkKey, concat(enc.encode("WebPush: info\0"), uaPublic, asPublic, new Uint8Array([1])));
  const s = salt || crypto.getRandomValues(new Uint8Array(16));
  const prk = await hmac(s, ikm);
  const cek = (await hmac(prk, concat(enc.encode("Content-Encoding: aes128gcm\0"), new Uint8Array([1])))).slice(0, 16);
  const nonce = (await hmac(prk, concat(enc.encode("Content-Encoding: nonce\0"), new Uint8Array([1])))).slice(0, 12);
  const key = await crypto.subtle.importKey("raw", cek, "AES-GCM", false, ["encrypt"]);
  const plain = concat(typeof payload === "string" ? enc.encode(payload) : payload, new Uint8Array([2]));
  const cipher = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, key, plain));
  const header = new Uint8Array(21);
  header.set(s, 0);
  new DataView(header.buffer).setUint32(16, 4096);
  header[20] = asPublic.length;
  return concat(header, asPublic, cipher);
}

// VAPID: an ES256 JWT for the push service's origin, signed with this site's private key.
async function vapidHeader(env, endpoint) {
  const jwk = JSON.parse(env.VAPID_PRIVATE_JWK);
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const head = b64u(enc.encode(JSON.stringify({ typ: "JWT", alg: "ES256" })));
  const body = b64u(enc.encode(JSON.stringify({
    aud: new URL(endpoint).origin,
    exp: Math.floor(Date.now() / 1000) + 12 * 3600,
    sub: env.VAPID_SUBJECT || "https://github.com/nmtjn1997/amul-stock-watch",
  })));
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, enc.encode(`${head}.${body}`));
  return `vapid t=${head}.${body}.${b64u(sig)}, k=${env.VAPID_PUBLIC_KEY}`;
}

export const pushEnabled = (env) => Boolean(env.VAPID_PUBLIC_KEY && env.VAPID_PRIVATE_JWK);

// Returns "ok", "gone" (the browser unsubscribed: delete it) or throws.
export async function sendPush(env, sub, message) {
  const body = await encrypt(JSON.stringify(message), sub.p256dh, sub.auth);
  const res = await fetch(sub.endpoint, {
    method: "POST",
    redirect: "manual",
    headers: {
      Authorization: await vapidHeader(env, sub.endpoint),
      "Content-Encoding": "aes128gcm",
      "Content-Type": "application/octet-stream",
      TTL: "86400",
      Urgency: "high",
    },
    body,
  });
  const text = await res.text();
  if (res.status === 404 || res.status === 410) return "gone";
  if (res.status < 200 || res.status >= 300) throw new Error(`push HTTP ${res.status} ${text.slice(0, 80)}`);
  return "ok";
}
