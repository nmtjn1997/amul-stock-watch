from __future__ import annotations

from amul_watch.notification_routes import (
    alert_route_key,
    enquiry_emails,
    resolve_channel_names,
    resolve_channel_names_for_pins,
    resolve_product_alias,
)

ROSE = "amul-high-protein-rose-lassi-200-ml-or-pack-of-30"
PLAIN = "amul-high-protein-plain-lassi-200-ml-or-pack-of-30"

CFG = {
    "watchlist": [
        {"alias": ROSE, "short": "rose-lassi", "label": "Rose"},
        {"alias": PLAIN, "short": "plain-lassi", "label": "Plain"},
    ],
    "notifiers": {
        "a": {"type": "desktop"},
        "b": {"type": "email", "to": ["b@example.com"]},
        "c": {"type": "ntfy", "topic": "t"},
    },
    "alerts": {
        "560001:rose-lassi": ["a", "b"],
        "560002:rose-lassi": ["b", "c"],
        "*:plain-lassi": ["c"],
    },
    "default_alerts": ["a"],
}


def test_exact_route_wins() -> None:
    assert resolve_channel_names(CFG, "560001", ROSE) == ["a", "b"]


def test_wildcard_route() -> None:
    assert resolve_channel_names(CFG, "999999", PLAIN) == ["c"]


def test_default_when_no_route() -> None:
    assert resolve_channel_names(CFG, "999999", ROSE) == ["a"]


def test_union_across_pins_is_deduped() -> None:
    assert resolve_channel_names_for_pins(CFG, ["560001", "560002"], ROSE) == ["a", "b", "c"]


def test_short_names_resolve_both_ways() -> None:
    assert resolve_product_alias(CFG, "rose-lassi") == ROSE
    assert resolve_product_alias(CFG, ROSE) == ROSE
    assert alert_route_key(CFG, "560001", ROSE) == "560001:rose-lassi"


def test_route_by_full_alias_still_matches() -> None:
    cfg = {**CFG, "alerts": {f"560001:{ROSE}": ["c"]}}
    assert resolve_channel_names(cfg, "560001", ROSE) == ["c"]


def test_enquiry_emails_fall_back_to_email_notifiers() -> None:
    assert enquiry_emails(CFG) == ["b@example.com"]
