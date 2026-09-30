from __future__ import annotations

import base64
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from amul_watch import config
from amul_watch.webui import Handler


@pytest.fixture()
def server(monkeypatch):
    config.init_home(force=True)
    monkeypatch.setenv("AMUL_WATCH_UI_PASSWORD", "pw")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _get(url: str, password: str | None = None) -> int:
    req = urllib.request.Request(url)
    if password is not None:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"u:{password}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_password_required(server) -> None:
    assert _get(server + "/api/state") == 401
    assert _get(server + "/api/state", "wrong") == 401
    assert _get(server + "/api/state", "pw") == 200
