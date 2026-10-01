// One-time Gmail setup for the email channel.
//
//   node scripts/gmail-auth.mjs ~/Downloads/client_secret_XXXX.json
//
// Opens Google's consent page for the gmail.send scope only (send mail, never read it),
// catches the redirect on 127.0.0.1, and stores GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET and
// GMAIL_REFRESH_TOKEN as Worker secrets through `wrangler secret put`. Nothing is printed
// or written to disk.

import { spawn, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";

const file = process.argv[2];
if (!file) {
  console.error("Usage: node scripts/gmail-auth.mjs <downloaded OAuth client JSON>");
  process.exit(1);
}
const raw = JSON.parse(readFileSync(file, "utf8"));
const client = raw.installed || raw.web;
if (!client?.client_id || !client?.client_secret) {
  console.error("That file has no client_id/client_secret. Download the JSON of a Desktop app OAuth client.");
  process.exit(1);
}

const SCOPE = "https://www.googleapis.com/auth/gmail.send";

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  if (url.pathname !== "/") return res.writeHead(404).end();
  const code = url.searchParams.get("code");
  if (!code) {
    res.writeHead(400, { "Content-Type": "text/plain" }).end(`Google said: ${url.searchParams.get("error") || "no code"}`);
    return finish(1);
  }
  try {
    const tok = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        code, client_id: client.client_id, client_secret: client.client_secret,
        redirect_uri: redirect, grant_type: "authorization_code",
      }),
    }).then((r) => r.json());
    if (!tok.refresh_token) throw new Error(tok.error_description || tok.error || "no refresh token returned");
    if (!String(tok.scope || "").split(" ").every((s) => s === SCOPE)) throw new Error(`unexpected scope: ${tok.scope}`);
    res.writeHead(200, { "Content-Type": "text/plain" }).end("Done. You can close this tab and go back to the terminal.");
    for (const [name, value] of [["GMAIL_CLIENT_ID", client.client_id], ["GMAIL_CLIENT_SECRET", client.client_secret], ["GMAIL_REFRESH_TOKEN", tok.refresh_token]]) {
      const r = spawnSync("npx", ["wrangler", "secret", "put", name], { input: value, stdio: ["pipe", "inherit", "inherit"] });
      if (r.status !== 0) throw new Error(`wrangler secret put ${name} failed`);
    }
    console.log("\nSaved. Email alerts are ready to use.");
    finish(0);
  } catch (e) {
    res.writeHead(500, { "Content-Type": "text/plain" }).end(`Failed: ${e.message}`);
    console.error(`Failed: ${e.message}`);
    finish(1);
  }
});

let redirect;
function finish(codeOut) { server.close(); process.exitCode = codeOut; }

server.listen(0, "127.0.0.1", () => {
  redirect = `http://127.0.0.1:${server.address().port}`;
  const auth = "https://accounts.google.com/o/oauth2/v2/auth?" + new URLSearchParams({
    client_id: client.client_id, redirect_uri: redirect, response_type: "code",
    scope: SCOPE, access_type: "offline", prompt: "consent",
  });
  console.log("Opening Google. Sign in as the alerts Gmail account and allow \"Send email on your behalf\".");
  console.log(`If no browser opens, visit:\n${auth}\n`);
  spawn(process.platform === "darwin" ? "open" : process.platform === "win32" ? "explorer" : "xdg-open", [auth], { stdio: "ignore", detached: true }).unref();
});
