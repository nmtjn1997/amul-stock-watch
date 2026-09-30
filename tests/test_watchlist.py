from __future__ import annotations

from amul_watch.pincodes import collect_pincodes, format_pin_scope, is_pincode_enabled
from amul_watch.watchlist_util import ordered_watchlist

CFG = {
    "priority_pincode": "560002",
    "pincodes": [
        {"pincode": "560001", "label": "Home", "short": "Bangalore", "enabled": True, "products": ["rose"]},
        {"pincode": "560002", "label": "Office", "enabled": True},
        {"pincode": "560003", "label": "Off", "enabled": False},
        {"pincode": "560004", "label": "Parked", "enabled": True, "products": [], "disabled_products": ["rose"]},
    ],
    "watchlist": [
        {"alias": "rose-alias", "short": "rose", "enabled": True},
        {"alias": "plain-alias", "short": "plain", "enabled": True},
        {"alias": "off-alias", "short": "off", "enabled": False},
    ],
}


def test_disabled_pincodes_are_not_polled() -> None:
    pins = [p["pincode"] for p in collect_pincodes(None, CFG)]
    assert pins == ["560002", "560001", "560004"]  # priority first, disabled dropped
    assert not is_pincode_enabled(CFG, "560003")
    assert not is_pincode_enabled(CFG, "111111")


def test_nothing_configured_polls_nothing() -> None:
    assert collect_pincodes(None, {"pincodes": []}) == []


def test_per_pincode_allowlist() -> None:
    assert [i["alias"] for i in ordered_watchlist(CFG, "560001")] == ["rose-alias"]


def test_no_allowlist_means_all_enabled_products() -> None:
    assert [i["alias"] for i in ordered_watchlist(CFG, "560002")] == ["rose-alias", "plain-alias"]


def test_all_products_parked_polls_nothing() -> None:
    assert ordered_watchlist(CFG, "560004") == []


def test_messages_use_short_label_not_private_label() -> None:
    assert format_pin_scope(CFG, "560001") == "Bangalore 560001"
    assert format_pin_scope(CFG, "560002") == "560002"
