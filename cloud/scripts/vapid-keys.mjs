// Make a Web Push (VAPID) key pair. Prints the public key for wrangler.toml and writes the
// private key to vapid-private.json (owner-only) for `wrangler secret put VAPID_PRIVATE_JWK`.
import { writeFileSync } from "node:fs";

const k = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
const pub = new Uint8Array(await crypto.subtle.exportKey("raw", k.publicKey));
const jwk = await crypto.subtle.exportKey("jwk", k.privateKey);
writeFileSync("vapid-private.json", JSON.stringify({ kty: jwk.kty, crv: jwk.crv, x: jwk.x, y: jwk.y, d: jwk.d }), { mode: 0o600 });
console.log(`VAPID_PUBLIC_KEY = "${Buffer.from(pub).toString("base64url")}"`);
console.log("private key written to vapid-private.json: store it, then delete the file");
