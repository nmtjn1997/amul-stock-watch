from __future__ import annotations

import json
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
    monkeypatch.delenv("AMUL_WATCH_UI_PASSWORD", raising=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _post(url: str, body: dict, headers: dict[str, str]) -> int:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


BODY = {"name": "x", "type": "command", "config": {"command": ["true"]}}


def test_cross_site_text_plain_post_is_refused(server) -> None:
    assert _post(server + "/api/notifier", BODY, {"Content-Type": "text/plain"}) == 403


def test_foreign_origin_is_refused(server) -> None:
    assert _post(server + "/api/notifier", BODY,
                 {"Content-Type": "application/json", "Origin": "https://evil.example"}) == 403


def test_rebound_host_is_refused(server) -> None:
    req = urllib.request.Request(server + "/api/state", headers={"Host": "evil.example"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 403


def test_same_origin_json_post_works(server) -> None:
    host = server.split("://")[1]
    assert _post(server + "/api/notifier", BODY,
                 {"Content-Type": "application/json", "Origin": f"http://{host}"}) == 200


def test_healthz_needs_nothing(server) -> None:
    with urllib.request.urlopen(server + "/healthz", timeout=5) as resp:
        assert resp.status == 200


def test_invalid_json_and_string_lists(server) -> None:
    host = server.split("://")[1]
    hdrs = {"Content-Type": "application/json", "Origin": f"http://{host}"}
    req = urllib.request.Request(server + "/api/watch", data=b"{not json", headers=hdrs, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert b"not valid JSON" in exc.value.read()
    assert _post(server + "/api/watch", {"pincode": "560001", "products": "rose-lassi", "recipients": "desktop"}, hdrs) == 200
    assert config.load_config()["alerts"]["560001:rose-lassi"] == ["desktop"]
