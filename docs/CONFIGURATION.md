# Configuration reference

Two YAML files and one secrets file, all in the home directory (`amul-watch paths`).
Both YAML files are re-read at the start of every poll cycle.

## config.yaml

| Key | Default | Meaning |
|---|---|---|
| `pincodes[]` | `[]` | Delivery addresses to check. Nothing enabled means nothing is polled |
| `pincodes[].pincode` | | Six digits, quoted |
| `pincodes[].label` | `Pin <pincode>` | Private name, shown only in the UI |
| `pincodes[].short` | | Public place name used in alert text. Falls back to the pincode |
| `pincodes[].enabled` | `true` | `false` keeps the entry but stops polling it |
| `pincodes[].products` | all enabled | Product short names or aliases watched at this pincode. A missing key means every enabled product; an empty list means none |
| `pincodes[].disabled_products` | | Parked watches: kept for re-enabling, not polled |
| `watchlist[]` | `[]` | Products |
| `watchlist[].alias` | | Slug from `https://shop.amul.com/en/product/<alias>` |
| `watchlist[].short` | the alias | Name used in routes (`pin:short`) and the CLI |
| `watchlist[].label` | | Display name |
| `watchlist[].enabled` | `true` | `false` stops polling it everywhere |
| `watchlist[].prefer_pack_of_30` | `true` | Judge stock by the pack-of-30 variant when one exists |
| `watchlist[].enquiry_name` | the label | Exact product name, for `register-enquiries` |
| `poll_interval_seconds` | `60` | Length of one cycle |
| `poll_jitter_seconds` | `10` | Random extra wait between cycles |
| `poll_spread` | `true` | Spread requests evenly across the cycle instead of a burst |
| `poll_spread_min_gap_seconds` | `1.0` | Minimum gap between requests when spreading |
| `poll_priority` | `{}` | `alias: weight`; weight 2 checks that product twice per cycle |
| `priority_pincode` | | Polled first each cycle |
| `request_delay_min` / `_max` | `1.0` / `2.0` | Per-request delay when `poll_spread` is off |
| `api_max_retries` | `3` | Retries for 5xx, 406 and network errors, with exponential backoff |
| `session_check_every_polls` | `5` | How often the session is probed proactively |
| `ui_port` | `8847` | Web UI port |
| `curl_path` | from PATH | Explicit curl binary, if not on PATH (or set `AMUL_CURL`) |
| `phone` | | Only for `register-enquiries` |

## notifications.yaml

| Key | Meaning |
|---|---|
| `notifiers.<name>` | A destination. `type` plus that type's settings (below). `enabled: false` mutes it |
| `alerts."<pin>:<product>"` | List of notifier names. `<pin>` may be `*`. `<product>` is the short name or alias |
| `default_alerts` | Notifiers for any watch without a matching route |
| `system_alerts` | Notifiers for "cannot reach Amul" and "imported cookie expired" |
| `qty_update_alerts` | Notifiers for quantity changes while in stock. Empty means none |
| `qty_update_min_delta` | Smallest quantity change worth reporting (default `2`) |
| `email` | SMTP server for `email` notifiers: `host`, `port`, `username`, `password`, `from`, `starttls` (default true), `ssl` |
| `enquiry_emails` | Addresses for `register-enquiries`. Defaults to every email notifier's `to` |

### Notifier types

| `type` | Required | Optional |
|---|---|---|
| `email` | `to` (list) | |
| `ntfy` | `topic` | `server`, `token` (for protected topics) |
| `telegram` | `bot_token`, `chat_id` | |
| `discord` | `url` | |
| `slack_webhook` | `url` | |
| `slack` | `token`, `channel` (C... or U...) | |
| `webhook` | `url` | `headers` |
| `command` | `command` (list or string) | |
| `desktop` | | `sticky` |
| `browser` | | |

Any string value may contain `${NAME}`, replaced from the environment (and `.env`) at
send time. A missing variable becomes an empty string, which `doctor` and the UI flag.

## .env

`KEY=value` lines, loaded into the environment at startup, before every poll cycle and on
every UI page load, so edits apply without a restart. A variable set in the real
environment (shell, Docker, systemd) always wins over `.env`. Created with owner-only
permissions.

| Variable | Used for |
|---|---|
| any `${NAME}` you reference | notifier secrets |
| `AMUL_WATCH_UI_PASSWORD` | require HTTP basic auth on the web UI (any username) |
| `AMUL_COOKIE`, `AMUL_MS_GA` | an imported browser session; set with `amul-watch session import` |

## Environment only

| Variable | Meaning |
|---|---|
| `AMUL_WATCH_HOME` | Home directory (same as `--home`) |
| `AMUL_WATCH_HOST` | Default bind address for `serve`/`ui` (Docker sets `0.0.0.0`) |
| `AMUL_WATCH_ALLOWED_HOSTS` | Extra host names the UI answers to without a password (comma separated), e.g. `amul.home` behind a local reverse proxy |
| `AMUL_CURL` | curl binary |
| `CURL_CA_BUNDLE` | CA bundle for curl, for corporate TLS proxies |
