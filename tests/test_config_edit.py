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
    assert "plain-lassi" in pin["products"]
    assert cfg["alerts"]["560001:plain-lassi"] == ["phone"]
    assert cfg["notifiers"]["phone"] == {"type": "ntfy", "topic": "abc"}


def test_disable_watch_parks_it() -> None:
    ce.upsert_watch("560001", "rose-lassi", ["desktop"])
    ce.set_watch_enabled("560001", "rose-lassi", False)
    pin = next(p for p in config.load_config()["pincodes"] if str(p["pincode"]) == "560001")
    assert pin["enabled"] is False
    assert "rose-lassi" in pin["disabled_products"]


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


def test_address_without_products_watches_nothing() -> None:
    from amul_watch.watchlist_util import ordered_watchlist

    ce.upsert_address("400001", label="New", enabled=True)
    assert ordered_watchlist(config.load_config(), "400001") == []
    assert not [w for w in ce.get_state()["watches"] if w["pincode"] == "400001" and w["enabled"]]


def test_empty_recipients_removes_route() -> None:
    ce.upsert_watch("560001", "rose-lassi", ["desktop"])
    ce.upsert_watch("560001", "rose-lassi", [])
    assert "560001:rose-lassi" not in (config.load_config().get("alerts") or {})


def test_toggle_does_not_freeze_defaults_into_a_route() -> None:
    ce.upsert_watch("560001", "rose-lassi", None)
    ce.set_watch_enabled("560001", "rose-lassi", False)
    ce.set_watch_enabled("560001", "rose-lassi", True)
    assert "560001:rose-lassi" not in (config.load_config().get("alerts") or {})


def test_delete_notifier_cleans_every_reference() -> None:
    ce.upsert_notifier("phone", "ntfy", {"topic": "t"})
    ce.upsert_watch("560001", "rose-lassi", ["phone"])
    ce.set_name_list("system_alerts", ["phone", "desktop"])
    ce.delete_notifier("phone")
    cfg = config.load_config()
    assert "560001:rose-lassi" not in cfg["alerts"]
    assert cfg["system_alerts"] == ["desktop"]


def test_edit_keeps_hidden_settings_and_masked_secrets() -> None:
    ce.upsert_notifier("hook", "webhook", {"url": "https://x", "headers": {"A": "b"}})
    ce.upsert_notifier("hook", "webhook", {"url": "https://y"})
    assert config.load_config()["notifiers"]["hook"]["headers"] == {"A": "b"}
    ce.upsert_notifier("tg", "telegram", {"bot_token": "real-secret", "chat_id": "1"})
    shown = next(r for r in ce.get_state()["recipients"] if r["name"] == "tg")["config"]
    assert shown["bot_token"] == ce.MASK
    ce.upsert_notifier("tg", "telegram", {"bot_token": ce.MASK, "chat_id": "2"})
    assert config.load_config()["notifiers"]["tg"]["bot_token"] == "real-secret"


def test_delete_and_toggle_work_on_short_names_from_the_example() -> None:
    # the shipped example lists `rose-lassi` by short name under 110001
    ce.set_watch_enabled("110001", "rose-lassi", False)
    pin = next(p for p in config.load_config()["pincodes"] if str(p["pincode"]) == "110001")
    assert pin["products"] == [] and pin["disabled_products"] == ["rose-lassi"]
    ce.set_watch_enabled("110001", "amul-high-protein-rose-lassi-200-ml-or-pack-of-30", True)
    ce.delete_watch("110001", "rose-lassi")
    pin = next(p for p in config.load_config()["pincodes"] if str(p["pincode"]) == "110001")
    assert pin["products"] == [] and not pin.get("disabled_products")
