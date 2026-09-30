# Extending

## Develop

```bash
git clone https://github.com/nmtjn1997/amul-stock-watch.git && cd amul-stock-watch
```

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/pytest -q
```

Tests never touch your real config: `tests/conftest.py` points `AMUL_WATCH_HOME` at a
temporary directory before anything is imported. None of them call the real shop.

For a manual run against a scratch home:

```bash
AMUL_WATCH_HOME=/tmp/amul-dev .venv/bin/amul-watch init
```

```bash
AMUL_WATCH_HOME=/tmp/amul-dev .venv/bin/amul-watch serve
```

Only runtime dependencies are PyYAML and ruamel.yaml (for comment-preserving writes).
The web server is the standard library's `http.server`; the UI is one HTML file with no
build step.

## Add a notifier type

Everything lives in `amul_watch/notifiers.py`. Suppose you want Pushover.

1. Write the send function. It receives the notifier's settings (with `${VAR}` already
   expanded), the `Alert`, and the whole config. Raise on failure; do not catch.

   ```python
   def send_pushover(spec: dict[str, Any], alert: Alert, _cfg: dict[str, Any]) -> None:
       post_json("https://api.pushover.net/1/messages.json", {
           "token": _require(spec, "app_token"),
           "user": _require(spec, "user_key"),
           "title": alert.title,
           "message": alert.message,
           "url": alert.url,
           "priority": 1 if alert.kind == "stock" else 0,
       })
   ```

2. Register it and its required keys:

   ```python
   NOTIFIER_TYPES["pushover"] = (send_pushover, "Pushover push notification")
   REQUIRED_KEYS["pushover"] = ["app_token", "user_key"]
   ```

3. Give the UI a form: add an entry to `NOTIFIER_FORMS` in `amul_watch/web/index.html`.

   ```js
   pushover:[{key:"app_token",label:"App token",secret:"PUSHOVER_APP_TOKEN"},
             {key:"user_key",label:"User key"}],
   ```

4. Add a test in `tests/test_notifiers.py`, and a row in `docs/CONFIGURATION.md`.

`send()` already isolates failures (one broken notifier never blocks the others), records
per-notifier results in the history, and skips `enabled: false`. HTTP goes through
`http_util.post_json` / `post_text`, which use curl, so proxies and CA bundles behave the
same as for the shop.

The `command` type means most one-off integrations need no code at all.

## Alert kinds

| `Alert.kind` | Sent when | Routed by |
|---|---|---|
| `stock` | out of stock to in stock | `alerts` / `default_alerts` |
| `qty` | quantity changed while in stock | `qty_update_alerts` |
| `system` | session cannot be recovered, imported cookie expired | `system_alerts` |
| `test` | `simulate`, UI Test buttons | same as what is being tested |

A notifier can behave differently per kind (ntfy raises priority for `stock`; `browser`
only opens pages for `stock` and `test`).

## Add a CLI command

Commands are plain functions in `amul_watch/__main__.py` registered in `build_parser()`.
Import heavy modules inside the function, so `amul-watch --help` stays instant and
`--home` is applied before any path is computed.

## Add an API endpoint

Add a handler `api_<name>(body) -> dict` in `webui.py` and a line in `ROUTES`. GET
handlers get the query string as `body`, POST handlers the JSON body. Raise
`ValueError` for user errors; the server returns it as `{"error": ...}` with status 400.
Put config writes in `config_edit.py`, not in the handler, so the CLI and tests can reuse
them.

## Project layout

```
amul_watch/
  __main__.py          CLI
  config.py            paths, loading, init
  client.py            Amul HTTP (curl), session jar
  session_guard.py     session health and recovery
  pincodes.py          pincode list, place names, delivery zones
  watchlist_util.py    products per pincode
  poller.py            one poll cycle
  poll_spread.py       paced poll cycle
  poll_priority.py     per-product weights
  product_poll.py      transitions, dedup, qty updates
  stock.py             parse the product payload
  stock_alert.py       compose and send a stock alert
  stock_notify.py      events to notifier names
  notification_routes.py  route lookup, descriptions
  notifiers.py         notifier types
  http_util.py         curl POST helpers
  notifications.py     alert history (JSONL)
  daemon.py            poll loop, pause, heartbeat
  webui.py             HTTP server + JSON API
  web/index.html       the UI
  config_edit.py       UI write path
  service.py           launchd / systemd / Task Scheduler
  doctor.py            self check
  session_cli.py       session show/import/reset
  enquiries.py         Amul notify-me registration
  inventory.py         inventory table and CSV
  logs.py              log tailing for the UI
  examples/            files `init` copies
tests/
docs/
scripts/install.sh, scripts/install.ps1
Dockerfile, docker-compose.yml
```
