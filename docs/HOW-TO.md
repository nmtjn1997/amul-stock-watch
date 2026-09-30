# How to

Every task here can be done in the web UI (`amul-watch serve`, then
<http://127.0.0.1:8847>) or by editing the YAML files. Both are shown. Changes apply on
the next poll; there is nothing to restart.

- [Add a person who should be alerted](#add-a-person-who-should-be-alerted)
- [Set up each notifier type](#set-up-each-notifier-type)
- [Add a pincode](#add-a-pincode)
- [Add a product](#add-a-product)
- [Add a watch](#add-a-watch)
- [Test before a real restock](#test-before-a-real-restock)
- [Pause and resume](#pause-and-resume)
- [Run it in the background](#run-it-in-the-background)
- [Move to another machine](#move-to-another-machine)
- [Troubleshooting](#troubleshooting)

## Add a person who should be alerted

A person is reached through a **notifier**: a named destination such as "mum-telegram" or
"me-phone". One person can have several; one notifier can be on many watches.

**UI**: Notifiers tab > pick a type > give it a name > fill the fields > Add notifier >
press **Test** on its row.

**YAML** (`notifications.yaml`):

```yaml
notifiers:
  mum-telegram:
    type: telegram
    bot_token: ${TELEGRAM_BOT_TOKEN}
    chat_id: "123456789"
```

Then put the watch on it: `alerts: {"560001:rose-lassi": [mum-telegram]}`.

## Set up each notifier type

`amul-watch notifiers` lists every type and its required settings. Put secrets in the
`.env` file next to `notifications.yaml` and refer to them as `${NAME}`.

### ntfy (phone push, easiest)

1. Install the ntfy app (Android, iOS) or open <https://ntfy.sh/app>.
2. Subscribe to a topic with a long random name, e.g. `amul-watch-k7p2x9q4`. Anyone who
   knows the name can read it, so do not use your name.
3. `type: ntfy`, `topic: amul-watch-k7p2x9q4`. Stock alerts arrive as high priority and
   tapping one opens the product page.

Self-hosting ntfy? Add `server: https://ntfy.example.com` and, if protected, `token:`.

### Email (Gmail or any SMTP)

Set the server once in `notifications.yaml`:

```yaml
email:
  host: smtp.gmail.com
  port: 587
  username: you@gmail.com
  password: ${SMTP_PASSWORD}
  from: you@gmail.com
```

For Gmail, turn on 2-step verification, create an app password at
<https://myaccount.google.com/apppasswords>, and put `SMTP_PASSWORD=that-password` in
`.env`. Then each email notifier only needs `to: [someone@example.com, ...]`.

### Telegram

1. In Telegram, message **@BotFather**, send `/newbot`, copy the token into `.env` as
   `TELEGRAM_BOT_TOKEN=...`.
2. The person who should receive alerts sends any message to your new bot.
3. Open `https://api.telegram.org/bot<token>/getUpdates`; their `chat.id` is in the reply.
   For a group, add the bot to the group and use the (negative) group id.

### Slack

Simplest: an **incoming webhook** (`type: slack_webhook`, `url: ${SLACK_WEBHOOK_URL}`)
posts to one channel. Create it under your Slack app's *Incoming Webhooks*.

To DM specific people, use a bot token (`type: slack`, `token: ${SLACK_BOT_TOKEN}`,
`channel: U...` for a person or `C...` for a channel). The app needs the `chat:write`
scope. A person's member id is in their Slack profile under *More > Copy member ID*.

### Discord

Server settings > Integrations > Webhooks > New webhook > copy URL.
`type: discord`, `url: ${DISCORD_WEBHOOK_URL}`.

### Webhook (Home Assistant, n8n, Zapier, your own API)

`type: webhook`, `url: https://...`, optional `headers: {Authorization: "Bearer ${TOKEN}"}`.
The body is the whole alert as JSON:

```json
{"kind": "stock", "title": "Amul: Rose Lassi in stock", "message": "...",
 "url": "https://shop.amul.com/en/product/...", "product": "Rose Lassi",
 "alias": "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
 "pincodes": ["560001"], "qty": 12, "price": 900.0}
```

### Command (anything else)

Runs a program with the alert in environment variables: `AMUL_KIND`, `AMUL_TITLE`,
`AMUL_MESSAGE`, `AMUL_URL`, `AMUL_PRODUCT`, `AMUL_PINCODES`, `AMUL_QTY`, and
`AMUL_ALERT_JSON`. A non-zero exit is recorded as a failure.

```yaml
say-it:
  type: command
  command: ["say", "Rose lassi is back"]      # macOS text to speech
```

### Desktop and browser

`type: desktop` shows a notification on the machine running amul-watch (`sticky: true`
keeps it on screen until dismissed). `type: browser` opens the product page. Both do
nothing useful inside Docker; use ntfy there.

## Add a pincode

**UI**: Addresses & Products > Add address. `Label` is private (only in the UI);
`Short label` is what appears in alert messages, so use a city name, not a person's name.

**YAML** (`config.yaml`):

```yaml
pincodes:
  - pincode: "560001"
    label: "Mum's place"
    short: Bangalore
    enabled: true
    products: [rose-lassi, buttermilk]   # leave out to watch every enabled product
```

## Add a product

1. Open the product on shop.amul.com. The URL ends in its **alias**:
   `https://shop.amul.com/en/product/amul-high-protein-rose-lassi-200-ml-or-pack-of-30`.
2. **UI**: Addresses & Products > Add product > paste the whole URL, a label and a short
   name (used in routes, e.g. `rose-lassi`).

**YAML**:

```yaml
watchlist:
  - alias: amul-kool-protein-milkshake-or-chocolate-180-ml-or-pack-of-8
    short: kool-choc
    label: "Kool Protein Chocolate"
    enabled: true
    prefer_pack_of_30: false   # true = judge stock by the pack-of-30 variant when there is one
```

Check it resolves: `amul-watch doctor` looks the first watched product up live.

## Add a watch

**UI**: Watches > Add watch > pick or type a pincode > tick one or more products > tick
notifiers > Save. One watch is created per product.

**YAML**: a watch is the pincode's `products` entry plus a route:

```yaml
# config.yaml
pincodes:
  - pincode: "560001"
    products: [rose-lassi]
# notifications.yaml
alerts:
  "560001:rose-lassi": [me-phone, mum-telegram]
```

Wildcards: `"*:rose-lassi"` covers every pincode without its own route. Anything without
any route goes to `default_alerts`.

## Test before a real restock

```bash
amul-watch simulate stock --pincode 560001 --product rose-lassi --dry-run
```

prints exactly who would be alerted. Drop `--dry-run` to send a TEST message through the
real notifiers. In the UI, each watch and each notifier has a **Test** button. Tests do
not touch stock state or the alert gate.

`amul-watch alerts` lists every live watch and its resolved notifiers.

## Pause and resume

`amul-watch pause -r "ordered enough"` / `amul-watch resume`, or the buttons in the
Controls tab. The process and the UI keep running; only polling stops.

## Run it in the background

```bash
amul-watch service install
```

| OS | Mechanism | Logs |
|---|---|---|
| macOS | LaunchAgent `io.github.amul-watch` | `data/service.err.log`, `data/amul-watch.log` |
| Linux | systemd user unit `amul-watch.service` | `journalctl --user -u amul-watch`, `data/amul-watch.log` |
| Windows | Scheduled task `amul-watch`, at logon | `data\amul-watch.log` |
| Docker | `restart: unless-stopped` | `docker compose logs -f` |

On Linux, run `loginctl enable-linger $USER` to keep it running while logged out.
On macOS, keep the install outside `~/Documents` and `~/Desktop`: launchd refuses to run
jobs from folders protected by privacy settings.

`amul-watch service status` / `amul-watch service uninstall`.

## Move to another machine

Copy the home directory (`amul-watch paths` shows it). That is the whole state. In Docker:

```bash
docker run --rm -v amul-stock-watch_amul-data:/data -v "$PWD":/out alpine tar czf /out/amul-data.tgz -C /data .
```

## Troubleshooting

Start with `amul-watch doctor`: it checks curl, config, every notifier and a live product
lookup, in that order.

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl: (60) SSL certificate problem` | A corporate TLS proxy | Point `CURL_CA_BUNDLE` at your company CA bundle (in Docker, mount it and set the env var; see `docker-compose.yml`) |
| `doctor` says product lookup empty | Wrong alias | Copy it again from the product URL |
| UI says "Not polling" | `serve` is not running, or only `ui` is | `amul-watch status`, then `amul-watch serve` or `service install` |
| A notifier shows "needs setup" | Required field missing, or `${VAR}` not in `.env` | Edit it, then press Test |
| Alerts never arrive for one watch | Route names a notifier that does not exist | `amul-watch alerts` shows the resolved list; `doctor` flags unknown names |
| "cannot reach Amul" system alert | Shop down, or blocked on this network | Wait; if it persists, `amul-watch session import` with a browser cookie |
