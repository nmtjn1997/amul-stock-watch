# Amul Stock Watch

Get a message the moment an Amul product is back in stock at *your* pincode.

Popular items on [shop.amul.com](https://shop.amul.com) (the high protein lassi and
buttermilk packs, for example) sell out within minutes of a restock, and stock differs
by delivery region. Amul Stock Watch checks the products you care about, for every
pincode you care about, about once a minute, and notifies the right people as soon as
something flips from out of stock to in stock.

**Two ways to use it:**

- **[Use the hosted version](https://amul.backinstock.workers.dev)**: create an account, add
  up to 10 alerts, get phone notifications. Nothing to install. Runs free on Cloudflare
  ([how](docs/HOSTED.md)).
- **Run your own copy** on a laptop, a server or Docker (Quick start below), with every
  notifier type and no limits. Try its UI first in the
  [live demo](https://nmtjn1997.github.io/amul-stock-watch/demo/) (sample data, nothing saved).

![Watches: who is alerted for which product at which pincode](docs/images/watches.png)

## Features

- **Per pincode, per product, per person.** "Tell me and my sister when Rose Lassi is
  back in Bangalore; tell Dad only about Buttermilk in Delhi." Each of those is one
  *watch*.
- **Ten ways to be notified**: email (SMTP, Gmail app passwords work), phone push via
  [ntfy](https://ntfy.sh) (free, no account), Telegram, Discord, Slack (webhook or bot,
  channel or DM), any webhook as JSON, any local command, desktop notification, or just
  open the product page.
- **Web UI** to add watches, people and products, see live stock, send test alerts, read
  logs and the history of every alert sent. Or edit two commented YAML files; both stay
  in sync.
- **No login, no cookie copying.** It keeps its own anonymous shop session and restarts
  it when it expires. A human is only told when that fails.
- **Alerts once per restock.** Out-of-stock to in-stock sends one alert; staying in stock
  does not repeat it; selling out re-arms it. Optional quiet quantity updates.
- **Gentle on the shop.** Requests are spread evenly across the poll interval instead of
  fired in bursts, with jitter and backoff.
- **Runs anywhere.** macOS, Linux, Windows, or Docker. One process, no database server;
  state is a SQLite file.

![Live stock per pincode](docs/images/live-stock.png)

## Do I need an account, token or cookie?

No. Amul Stock Watch never logs in to Amul and needs no API key. It opens the shop
homepage once to get an ordinary anonymous session, signs each request the way the shop's
own page does, and renews the session by itself when it expires.

Tokens only come in for some ways of *sending* alerts, and only if you choose them:

| Notifier | Needs |
|---|---|
| ntfy (phone push), desktop, browser, webhook, command | nothing |
| Telegram | a bot token from @BotFather |
| Slack, Discord | a webhook URL (or a Slack bot token for DMs) |
| Email | an SMTP password (a Gmail app password works) |

## Quick start

You need Python 3.10+, curl (built into macOS and Windows 10+) and, for the one-line
installers, git. On Debian and Ubuntu also `python3-venv`. The installers create your
config too, so there is no separate `init` step.

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/nmtjn1997/amul-stock-watch/main/scripts/install.sh | bash
```

```bash
amul-watch serve
```

If the shell says `command not found`, open a new terminal, or run
`~/.local/bin/amul-watch serve` (the installer prints the exact path).

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/nmtjn1997/amul-stock-watch/main/scripts/install.ps1 | iex
```

Open a **new** PowerShell window (so the updated PATH is picked up), then:

```powershell
amul-watch serve
```

**Docker**

```bash
git clone https://github.com/nmtjn1997/amul-stock-watch.git && cd amul-stock-watch
```

```bash
docker compose up -d --build
```

Then open <http://127.0.0.1:8847>. Inside a container the desktop and browser notifiers
cannot reach you, so add an **ntfy** (or Telegram, email) notifier first.

**From source**

```bash
git clone https://github.com/nmtjn1997/amul-stock-watch.git && cd amul-stock-watch
```

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install . && amul-watch init && amul-watch serve
```

(A plain `pip install .` is refused by Homebrew and recent Debian/Ubuntu Pythons; the
venv avoids that. `pipx install .` works too.)

The first run comes with one example watch: Rose Lassi at pincode 110001, alerting the
`desktop` and `browser` notifiers. Delete it once you have added your own, or it keeps
being polled.

To keep it running after you close the terminal:

```bash
amul-watch service install
```

That registers `amul-watch serve` with launchd (macOS), a systemd user unit (Linux) or
the Startup folder (Windows, no admin rights needed). Docker needs nothing extra: the container restarts itself.

## Use it from your phone

The alerts already reach your phone (ntfy, Telegram, email). To also manage watches from
the phone, let the UI listen on your network and give it a password, in the `.env` file
(`amul-watch paths` shows where it is):

```bash
AMUL_WATCH_HOST=0.0.0.0
AMUL_WATCH_UI_PASSWORD=pick-a-long-password
```

Restart `amul-watch serve` (or run `amul-watch service install` again). It prints the
address to open on a phone on the same Wi-Fi, such as `http://192.168.1.20:8847`. The UI
is built for phone screens, and "Add to Home Screen" makes it open like an app. For Docker,
change the port line in `docker-compose.yml` to `"8847:8847"` and set the password there.
To reach it away from home, put it behind a VPN such as Tailscale rather than opening a
port on your router.

## Your first real watch

1. **Notifiers tab**: add a way to reach you. The fastest is `ntfy`: install the ntfy app
   on your phone, subscribe to a long random topic name, and enter the same topic here.
   Press **Test**; your phone should buzz.
2. **Watches tab** > **Add watch**: enter your pincode, tick the products, tick your
   notifier. Save.
3. Press **Test** on the watch to see exactly who would be alerted.

No alert when you expected one? Alerts fire only when a product goes from out of stock
to in stock, not while it stays in stock. `amul-watch alerts` shows who each watch alerts,
and [Troubleshooting](docs/HOW-TO.md#troubleshooting) covers the usual causes.

That is it. The poller picks up changes on its next cycle; there is nothing to restart.

More walkthroughs, including Telegram, Gmail and Slack setup and adding a product that
is not in the example list: **[docs/HOW-TO.md](docs/HOW-TO.md)**.

## How it works

```mermaid
flowchart LR
    subgraph process["amul-watch serve (one process)"]
        UI["Web UI<br/>:8847"] -->|writes| CFG[("config.yaml<br/>notifications.yaml")]
        POLL["Poller thread<br/>every ~60s"] -->|re-reads each cycle| CFG
        POLL --> DB[("SQLite<br/>last known stock")]
        POLL --> ROUTE["Routing<br/>pincode:product to notifiers"]
        ROUTE --> N["Notifiers"]
    end
    POLL <-->|curl| AMUL["shop.amul.com API"]
    N --> OUT["email · ntfy · Telegram · Slack<br/>Discord · webhook · desktop"]
```

A watch is a pincode, a product and a list of notifiers. Every cycle the poller asks the
shop for each watched product in each pincode's delivery zone, compares with the last
answer stored in SQLite, and on an out-of-stock to in-stock change sends one alert to the
notifiers routed for that pincode and product.

The full walkthrough, with sequence diagrams for the poll cycle, session handling and a
UI edit: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Commands

| Command | What it does |
|---|---|
| `amul-watch serve` | Web UI and poller in one process |
| `amul-watch run` | Poller only, no UI (for servers) |
| `amul-watch status` | Is it polling, when did it last poll |
| `amul-watch stock` | Live stock table from the shop, no alerts |
| `amul-watch alerts` | Every active watch and who it alerts |
| `amul-watch simulate stock --pincode 560001 --dry-run` | Who would be alerted, send nothing |
| `amul-watch pause` / `resume` | Stop and start polling |
| `amul-watch doctor` | Check the whole setup end to end |
| `amul-watch service install` | Start in the background at every login |
| `amul-watch notifiers` | List notifier types and their settings |

`amul-watch --help` lists everything.

## Where things live

Everything is under one directory: `~/.config/amul-watch` on macOS and Linux,
`%APPDATA%\amul-watch` on Windows, `/data` in Docker. Override it with `--home` or
`AMUL_WATCH_HOME`. `amul-watch paths` prints it.

```
config.yaml          what to watch: pincodes, products, polling
notifications.yaml   who to tell: notifiers and routes
.env                 secrets (bot tokens, SMTP password), owner-only permissions
data/                SQLite state, logs, alert history, session cookie jar
```

Logs are in `data/amul-watch.log` (also in the UI's Controls tab), or
`docker compose logs -f` in Docker.

Every key is documented in **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

## Update, stop, uninstall

| | Installed with the script | Docker |
|---|---|---|
| Update | run the same one-line installer again | `git pull && docker compose up -d --build` |
| Stop | Ctrl+C, or `amul-watch service uninstall` if you installed the service | `docker compose down` |
| Uninstall | `amul-watch service uninstall`, then delete `~/.local/share/amul-watch` and `~/.local/bin/amul-watch` (Windows: `%LOCALAPPDATA%\amul-watch`) | `docker compose down -v` (also deletes your config) |

Your config and history stay in the home directory (`amul-watch paths`) until you delete
it yourself.

## Security notes

- The web UI binds to `127.0.0.1` by default and only answers to `localhost` names, and
  it refuses requests from other web pages, so a site you visit cannot change your
  config. To use it from other devices (Docker on a home server, `--host 0.0.0.0`), set
  `AMUL_WATCH_UI_PASSWORD`; without one, requests to any other host name are refused.
- Secrets typed into the UI are shown masked afterwards, and are passed to curl through
  a private file rather than the command line.
- Keep secrets in `.env` and reference them from YAML as `${NAME}`.
- An ntfy topic is effectively a password: anyone who knows it can read your alerts.
  Use a long random one.

## Documentation

- [HOW-TO.md](docs/HOW-TO.md): add a person, a product, a pincode; set up each notifier
- [ARCHITECTURE.md](docs/ARCHITECTURE.md): how it works inside, with diagrams
- [CONFIGURATION.md](docs/CONFIGURATION.md): every config key
- [EXTENDING.md](docs/EXTENDING.md): add a notifier type, run the tests
- [DESIGN-NOTES.md](docs/DESIGN-NOTES.md): decisions, trade-offs, known limits, roadmap
- [HOSTED.md](docs/HOSTED.md): the multi-user Cloudflare edition, its limits and abuse protection, and how to deploy your own

## Disclaimer

This is an unofficial personal project. It is not affiliated with or endorsed by Amul or
GCMMF. It reads the same public product data the shop's own web page loads, at a polite
rate. It never logs in, adds to cart or places orders. Please keep the poll interval
reasonable.

## License

MIT
