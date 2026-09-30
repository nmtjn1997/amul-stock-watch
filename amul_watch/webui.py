"""Web UI for the Amul stock watcher.

Stdlib-only HTTP server (no framework). Serves a single-page app plus a small JSON API
backed by amul_watch.config_edit. Binds 127.0.0.1 by default. When exposed on a network
(Docker, a home server), set AMUL_WATCH_UI_PASSWORD to require HTTP basic auth.

Start with:  amul-watch serve   (UI + poller)   or   amul-watch ui   (UI only)
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from amul_watch import config_edit as ce

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "index.html"


def _read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _parse_query(path: str) -> dict[str, Any]:
    from urllib.parse import parse_qs, urlparse

    q = parse_qs(urlparse(path).query)
    return {k: v[0] for k, v in q.items() if v}


# --------------------------------------------------------------------------- #
# API handlers: each returns a JSON-serialisable dict. Writes take effect on the
# poller's next cycle because it re-reads config every cycle.
# --------------------------------------------------------------------------- #

def api_state(_body: dict[str, Any]) -> dict[str, Any]:
    return ce.get_state()


def api_stock(_body: dict[str, Any]) -> dict[str, Any]:
    return {"rows": ce.live_stock(), "daemon": ce.daemon_state()}


def api_watch(body: dict[str, Any]) -> dict[str, Any]:
    """Create/update one watch, or several at once for the same pincode + recipients."""
    products = [str(p) for p in (body.get("products") or []) if str(p).strip()]
    if not products:
        products = [str(body.get("product") or "")]
    recipients = list(body.get("recipients") or [])
    enabled = bool(body.get("enabled", True))
    results = [
        ce.upsert_watch(
            str(body.get("pincode") or ""),
            product,
            recipients,
            enabled=enabled,
            address_label=str(body.get("address_label") or ""),
            address_short=str(body.get("address_short") or ""),
        )
        for product in products
    ]
    return {"ok": True, "watch": results[0], "watches": results}


def api_watch_delete(body: dict[str, Any]) -> dict[str, Any]:
    res = ce.delete_watch(str(body.get("pincode") or ""), str(body.get("product") or ""))
    return {"ok": True, "watch": res}


def api_watch_toggle(body: dict[str, Any]) -> dict[str, Any]:
    res = ce.set_watch_enabled(
        str(body.get("pincode") or ""),
        str(body.get("product") or ""),
        bool(body.get("enabled", True)),
    )
    return {"ok": True, "watch": res}


def api_address(body: dict[str, Any]) -> dict[str, Any]:
    res = ce.upsert_address(
        str(body.get("pincode") or ""),
        label=str(body.get("label") or ""),
        short=str(body.get("short") or ""),
        enabled=body.get("enabled"),
    )
    return {"ok": True, "address": res}


def api_address_delete(body: dict[str, Any]) -> dict[str, Any]:
    res = ce.delete_address(str(body.get("pincode") or ""))
    return {"ok": True, "address": res}


def api_product(body: dict[str, Any]) -> dict[str, Any]:
    res = ce.upsert_product(
        str(body.get("alias") or body.get("short") or ""),
        label=str(body.get("label") or ""),
        short=str(body.get("short") or ""),
        enquiry_name=str(body.get("enquiry_name") or ""),
    )
    return {"ok": True, "product": res}


def api_notifier(body: dict[str, Any]) -> dict[str, Any]:
    res = ce.upsert_notifier(
        str(body.get("name") or ""),
        str(body.get("type") or ""),
        dict(body.get("config") or {}),
        enabled=bool(body.get("enabled", True)),
    )
    return {"ok": True, "notifier": res}


def api_notifier_test(body: dict[str, Any]) -> dict[str, Any]:
    """Send a TEST message to one notifier, to check its settings."""
    from amul_watch.config import load_config, load_session_env
    from amul_watch.notifiers import Alert, send

    load_session_env()
    name = str(body.get("name") or "")
    results = send(
        load_config(),
        [name],
        Alert(kind="test", title="TEST from Amul Stock Watch",
              message=f"TEST: notifier {name!r} works. Real alerts will look like the stock alerts.",
              url="https://shop.amul.com/en/"),
    )
    return {"ok": results.get(name) == "ok", "results": results}


def api_notifier_delete(body: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "notifier": ce.delete_notifier(str(body.get("name") or ""))}


def api_watch_fill_disabled(_body: dict[str, Any]) -> dict[str, Any]:
    res = ce.populate_disabled_watches()
    return {"ok": True, **res}


def api_name_list(body: dict[str, Any]) -> dict[str, Any]:
    res = ce.set_name_list(str(body.get("key") or "default_alerts"), list(body.get("names") or []))
    return {"ok": True, **res}


def api_normalize(_body: dict[str, Any]) -> dict[str, Any]:
    res = ce.normalize_watches()
    return {"ok": True, **res}


def api_poll(_body: dict[str, Any]) -> dict[str, Any]:
    """Force a live poll now: fetch fresh stock from Amul (not just re-read the DB).

    Runs one full poll cycle (same code the daemon runs), so it also triggers real
    alerts on a 0→in-stock flip (the DB alert gate dedups against the daemon) and the
    dead-session guard. Blocks for the length of one cycle (up to a minute).
    """
    from amul_watch.client import AmulClient
    from amul_watch.config import DB_PATH, load_config, load_session_env
    from amul_watch.db import StockDB
    from amul_watch.poller import run_poll
    from amul_watch.session_guard import SessionGuard

    load_session_env()
    cfg = load_config()
    client = AmulClient(cfg)
    db = StockDB(DB_PATH)
    guard = SessionGuard(client, cfg)
    summary = run_poll(client, db, cfg, guard=guard, alerts_enabled=True)
    return {
        "ok": True,
        "summary": {
            k: summary.get(k)
            for k in ("checks", "alerts", "qty_updates", "session_expired")
        },
        "errors": (summary.get("errors") or [])[:5],
        "rows": ce.live_stock(),
        "daemon": ce.daemon_state(),
    }


def api_simulate(body: dict[str, Any]) -> dict[str, Any]:
    """Fire a test 0→in-stock alert through the real notify pipeline.

    dry_run=True previews the routes (who would be notified) without sending.
    Otherwise a TEST alert goes to the real notifiers. Stock state and cooldowns are
    not touched.
    """
    import contextlib
    import io

    from amul_watch import simulate as sim
    from amul_watch.config import load_config

    pincode = str(body.get("pincode") or "").strip()
    product = str(body.get("product") or "").strip()
    dry_run = bool(body.get("dry_run", False))
    if not pincode:
        raise ValueError("pincode required")

    cfg = load_config()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sim.sim_stock(cfg, pincode=pincode, product=product, dry_run=dry_run)
    return {
        "ok": True,
        "dry_run": dry_run,
        "pincode": pincode,
        "product": product,
        "output": buf.getvalue().strip(),
    }


def api_notifications(body: dict[str, Any]) -> dict[str, Any]:
    """Paginated timeline of notifications actually sent (newest first)."""
    from amul_watch import notifications as nf

    try:
        offset = int(body.get("offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = min(100, max(1, int(body.get("limit") or 25)))
    except (TypeError, ValueError):
        limit = 25
    items, total = nf.read(offset, limit)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def api_logs(body: dict[str, Any]) -> dict[str, Any]:
    """A window of a log file. ?after= follows, ?before= pages backwards."""
    from amul_watch.logs import log_files, read_log

    def _int(key: str) -> int | None:
        raw = body.get(key)
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    limit = _int("limit") or 200
    res = read_log(
        str(body.get("file") or "daemon"),
        limit=max(1, min(1000, limit)),
        before=_int("before"),
        after=_int("after"),
    )
    res["files"] = log_files()
    return res


def api_control(body: dict[str, Any]) -> dict[str, Any]:
    action = str(body.get("action") or "").lower()
    if action == "pause":
        res = ce.pause_daemon(str(body.get("reason") or ""))
    elif action == "resume":
        res = ce.resume_daemon()
    else:
        return {"ok": False, "error": f"unknown action {action!r}"}
    return {"ok": True, "action": action, "result": res, "daemon": ce.daemon_state()}


ROUTES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "GET /api/state": api_state,
    "GET /api/stock": api_stock,
    "GET /api/notifications": api_notifications,
    "GET /api/logs": api_logs,
    "POST /api/watch": api_watch,
    "POST /api/watch/delete": api_watch_delete,
    "POST /api/watch/toggle": api_watch_toggle,
    "POST /api/watch/fill-disabled": api_watch_fill_disabled,
    "POST /api/address": api_address,
    "POST /api/address/delete": api_address_delete,
    "POST /api/product": api_product,
    "POST /api/notifier": api_notifier,
    "POST /api/notifier/delete": api_notifier_delete,
    "POST /api/notifier/test": api_notifier_test,
    "POST /api/name-list": api_name_list,
    "POST /api/normalize": api_normalize,
    "POST /api/poll": api_poll,
    "POST /api/simulate": api_simulate,
    "POST /api/control": api_control,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "amul-watch-ui"

    def log_message(self, *_args: Any) -> None:  # quiet
        pass

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        try:
            body = INDEX_HTML.read_bytes()
        except OSError:
            self._send_json({"error": "UI page missing"}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        password = os.environ.get("AMUL_WATCH_UI_PASSWORD", "")
        if not password:
            return True
        header = self.headers.get("Authorization") or ""
        if header.startswith("Basic "):
            try:
                _user, _, given = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            except (ValueError, UnicodeDecodeError):
                given = ""
            if hmac.compare_digest(given, password):
                return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="amul-watch"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _dispatch(self, method: str) -> None:
        if not self._authorized():
            return
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        # SPA: any non-API GET (/, /live-stock, /notifications, deep links) serves the
        # app shell; the client router activates the right tab from the path.
        if method == "GET" and not path.startswith("/api"):
            self._send_html()
            return
        route = f"{method} {path}"
        fn = ROUTES.get(route)
        if fn is None:
            self._send_json({"error": "not found", "route": route}, 404)
            return
        body = _read_body(self) if method == "POST" else _parse_query(self.path)
        try:
            self._send_json(fn(body))
        except Exception as exc:  # surface config errors to the UI
            log.exception("api error on %s", route)
            self._send_json({"error": str(exc), "type": type(exc).__name__}, 400)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")


def serve(host: str = "127.0.0.1", port: int = 8847, *, open_browser: bool = True,
          with_poller: bool = False) -> int:
    """Serve the UI. with_poller=True also runs the stock poll loop in this process."""
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        import errno

        if exc.errno in (errno.EADDRINUSE, errno.EADDRNOTAVAIL):
            print(f"port {port} is busy: amul-watch may already be running at {url}")
            return 1
        raise
    stop = threading.Event()
    if with_poller:
        from amul_watch.daemon import start_background

        start_background(stop)
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("AMUL_WATCH_UI_PASSWORD"):
        log.warning("UI is reachable from the network with no password: set AMUL_WATCH_UI_PASSWORD")
    print(f"amul-watch UI: {url}" + ("  (poller running)" if with_poller else ""))
    print("  Ctrl+C to stop")
    if open_browser:
        def _open() -> None:
            import webbrowser

            webbrowser.open(url)

        threading.Timer(0.6, _open).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\namul-watch stopped.")
    finally:
        stop.set()
        httpd.server_close()
    return 0
