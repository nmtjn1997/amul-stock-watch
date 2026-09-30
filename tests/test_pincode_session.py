"""The shop answers for the session's selected pincode, so a cached zone lookup must
still switch the session before that pincode's products are read."""

from __future__ import annotations

import time

from amul_watch.client import AmulClient
from amul_watch.pincodes import resolve_substore_cached


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def get_substore(self, pin):
        return self.rows.get(pin)

    def set_substore(self, *a):
        raise AssertionError("cache hit expected")


def test_cached_zone_still_switches_session(monkeypatch) -> None:
    client = AmulClient({})
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(client, "set_store_preference", lambda store, **k: calls.append(("store", store)))
    monkeypatch.setattr(client, "set_geolocation_preference", lambda pin, **k: calls.append(("pin", pin)))
    db = FakeDB({
        "110001": {"substore_id": "a", "substore_name": "delhi", "updated_at": time.time()},
        "560001": {"substore_id": "b", "substore_name": "karnataka", "updated_at": time.time()},
    })
    resolve_substore_cached(client, db, "110001")
    resolve_substore_cached(client, db, "110001")   # same pin twice: no extra calls
    resolve_substore_cached(client, db, "560001")
    assert calls == [("store", "delhi"), ("pin", "110001"), ("store", "karnataka"), ("pin", "560001")]


def test_new_session_forgets_selected_pincode(tmp_path, monkeypatch) -> None:
    client = AmulClient({}, cookie_jar=tmp_path / "jar.txt")
    client._active_pin = "110001"
    monkeypatch.setattr(client, "_curl_text", lambda *a, **k: (tmp_path / "jar.txt").write_text("x jsessionid y"))
    client.bootstrap_session()
    assert client._active_pin is None
