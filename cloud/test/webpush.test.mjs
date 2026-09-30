// RFC 8291 Appendix A: encrypting the sample message with the sample keys and salt must
// produce exactly the sample body. Run: node test/webpush.test.mjs
import assert from "node:assert/strict";
import { encrypt, b64u, unb64u, validSubscription } from "../src/webpush.js";

const asPublic = unb64u("BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8");
const jwk = { kty: "EC", crv: "P-256", d: "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
  x: b64u(asPublic.slice(1, 33)), y: b64u(asPublic.slice(33, 65)), ext: true };
const asKeys = {
  privateKey: await crypto.subtle.importKey("jwk", jwk, { name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]),
  publicKey: await crypto.subtle.importKey("raw", asPublic, { name: "ECDH", namedCurve: "P-256" }, true, []),
};
const body = await encrypt(
  "When I grow up, I want to be a watermelon",
  "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
  "BTBZMqHH6r4Tts7J_aSIgg",
  { asKeys, salt: unb64u("DGv6ra1nlYgDCS1FRnbzlw") },
);
assert.equal(
  b64u(body),
  "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
);

const ok = { endpoint: "https://fcm.googleapis.com/fcm/send/abc", p256dh: "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4", auth: "BTBZMqHH6r4Tts7J_aSIgg" };
assert.equal(validSubscription(ok), true);
assert.equal(validSubscription({ ...ok, endpoint: "https://evil.example/push" }), false);
assert.equal(validSubscription({ ...ok, endpoint: "https://fcm.googleapis.com.evil.example/x" }), false);
assert.equal(validSubscription({ ...ok, endpoint: "https://user:pw@fcm.googleapis.com/x" }), false);
assert.equal(validSubscription({ ...ok, auth: "short" }), false);
console.log("webpush: RFC 8291 vector and subscription checks pass");
