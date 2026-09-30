"""The UI's write path: every edit lands in the YAML the poller reads."""

from __future__ import annotations

import pytest

from amul_watch import config, config_edit as ce


@pytest.fixture(autouse=True)
def fresh_home():
    config.init_home(force=True)
    yield


def test_init_creates_files() -> None:
    assert config.DEFAULT_CONFIG.is_file()
    assert config.NOTIFICATIONS_CONFIG.is_file()
    assert config.SESSION_ENV.is_file()


def test_add_watch_writes_pincode_product_and_route() -> None:
    ce.upsert_notifier("phone", "ntfy", {"topic": "abc"})
    ce.upsert_watch("560001", "plain-lassi", ["phone"], address_label="Home", address_short="Bangalore")
    cfg = config.load_config()
    pin = next(p for p in cfg["pincodes"] if str(p["pincode"]) == "560001")
    assert "amul-high-protein-plain-lassi-200-ml-or-pack-of-30" in pin["products"]
    assert cfg["alerts"]["560001:plain-lassi"] == ["phone"]
    assert cfg["notifiers"]["phone"] == {"type": "ntfy", "topic": "abc"}


def test_disable_watch_parks_it() -> None:
    ce.upsert_watch("560001", "rose-lassi", ["desktop"])
    ce.set_watch_enabled("560001", "rose-lassi", False)
    pin = next(p for p in config.load_config()["pincodes"] if str(p["pincode"]) == "560001")
    assert pin["enabled"] is False
    assert "amul-high-protein-rose-lassi-200-ml-or-pack-of-30" in pin["disabled_products"]


def test_invalid_notifier_is_rejected() -> None:
    with pytest.raises(ValueError):
        ce.upsert_notifier("bad", "telegram", {"chat_id": "1"})
    with pytest.raises(ValueError):
        ce.upsert_notifier("bad", "carrier-pigeon", {})


def test_add_product_from_pasted_url() -> None:
    res = ce.upsert_product("https://shop.amul.com/en/product/amul-kool-koko-200-ml", label="Kool Koko", short="Koko")
    assert res["alias"] == "amul-kool-koko-200-ml"
    assert res["short"] == "koko"


def test_comments_survive_round_trip() -> None:
    ce.upsert_watch("560001", "rose-lassi", ["desktop"])
    assert "# amul-watch config" in config.DEFAULT_CONFIG.read_text()
