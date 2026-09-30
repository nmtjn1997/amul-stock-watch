# Design notes

Decisions worth knowing before changing things, what this project deliberately does not
do, and what is left.

## Background

This started as a script for one household: a handful of pincodes, three high protein
products, and alerts to the family. It grew a web UI and per-person routing while in
daily use. Making it something anyone can install meant removing everything that
assumed that one machine.

| The personal build assumed | This version |
|---|---|
| Config, data and logs inside the source checkout | One home directory per OS, `AMUL_WATCH_HOME`, `--home` |
| A cookie copied from the browser, refreshed by hand when it expired (and an alert asking for it) | The client keeps its own anonymous session and renews it; a cookie can still be imported |
| Hardcoded account id, phone and analytics id as fallbacks | None; the analytics header value is generated |
| Gmail through a Google Workspace CLI and two named accounts | SMTP, which works with any provider including Gmail app passwords |
| Slack through the author's own helper scripts, with a people search | Slack webhook or bot token, plus a generic `command` notifier for anything personal |
| macOS popups and Chrome via AppleScript | Desktop notifications on macOS, Linux and Windows; the default browser |
| A fixed set of product shorthands in code | `short:` per product in config |
| A background daemon made with `fork`, a pid file, and launchd restarts to apply config | One process; config re-read every cycle; pause is a flag file; liveness is a heartbeat |
| A launchd agent for the poller and another for the UI | `amul-watch service install` for launchd, systemd or Task Scheduler; Docker |
| A Gmail watcher for Amul's own "back in stock" mails | Removed (see below) |
| Auto add-to-cart and payment page opening | Removed (see below) |
| `/usr/bin/curl` | `curl` from PATH, or `AMUL_CURL` |

## Decisions

**One process, config re-read every cycle.** The earlier split (a forked daemon plus a
separate UI process, with the UI restarting the daemon to apply edits) produced the only
serious bug this project has had: a restart raced the service manager, left a second
daemon running with an old config, and that daemon alerted for pincodes that had been
switched off. Re-reading config at the top of every cycle removes the need to restart at
all, so the race cannot exist.

**The dead-session guard.** When every product in a cycle returns an empty body, the
cycle is thrown away. Out of stock products still return a record, so "all empty" means
the session died. Treating it as "all out of stock" would reset every alert gate and fire
a round of false restock alerts on the next good cycle.

**curl as the only transport.** Python's TLS handshake is more often challenged by
Cloudflare than curl's. A single transport also means one place to fix a corporate proxy
(`CURL_CA_BUNDLE`) for both the shop and the notifiers.

**One session per caller.** The shop decides what stock to return from the session's
selected region, so the poller, one-off CLI commands and the UI's inline poll each keep
their own cookie jar, and the poller switches region per pincode. Sharing one session
would let a CLI `stock` run change what the poller reads mid-cycle.

**Routes do not merge.** The most specific matching route wins outright. Merging
`pin:product` with `*:product` would make "who gets this?" require reading several lines
and reasoning about precedence. `amul-watch alerts` prints the resolved answer.

**Watches are parked, not deleted, when disabled.** Turning a watch off moves the product
to `disabled_products` and keeps its route, so turning it back on is one click.

**Comment-preserving YAML writes.** The UI edits the same files people hand edit;
ruamel.yaml keeps their comments and layout.

**Private label vs public short name.** `label` ("Mum's place") never leaves the UI.
Messages use `short` ("Bangalore"), because alerts get forwarded and posted in group chats.

**Stdlib web server, one HTML file.** Nothing to build, nothing to keep patched, and the
whole UI can be read in one sitting.

## Deliberately not included

- **Auto checkout.** The personal build could add the product to the cart and open the
  payment page. It needs a logged-in session, it is the kind of automation that gets
  accounts blocked, and a mistake costs money. Alerts link straight to the product page;
  buying stays a human action.
- **Watching the inbox for Amul's own emails.** Amul emails "back in stock" to people who
  registered interest (`register-enquiries` still does that). Reading those emails needed
  a Gmail API client and OAuth setup, which is heavy for a small speed gain over polling.
  A plain IMAP reader would be the portable way to bring it back; see the roadmap.
- **Logging in.** Stock data is public. Nothing here needs an Amul account.

## Known limits

- The shop API is undocumented. If Amul changes it, `client.py` and `stock.py` are the
  two places to update, and `amul-watch doctor` is the fastest way to see what broke.
- The `desktop` notifier cannot reach you from inside Docker; use ntfy or Telegram there.
- Windows service mode uses the Startup folder, so it runs while you are logged in, not at boot.
- The UI has one shared password when enabled, not user accounts. Put it behind a reverse
  proxy with real auth if it is on the internet.
- One process per home directory. Two `serve` processes on the same home would both poll
  and both alert.

## Roadmap

- IMAP watcher for Amul's restock emails, as an optional second signal.
- Prebuilt image on GitHub Container Registry, so Docker users skip the build.
- PyPI release, so `pipx install amul-stock-watch` works without git.
- A lock file per home directory, so a second process refuses to start.
- More notifier types if asked for (WhatsApp Cloud API, Pushover, Matrix).
- A short demo GIF in the README.
