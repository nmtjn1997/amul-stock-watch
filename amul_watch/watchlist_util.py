"""Which products are polled at which pincode."""

from __future__ import annotations

from typing import Any


def _item_enabled(item: dict[str, Any]) -> bool:
    """True unless a watchlist entry sets enabled: false (entry kept, polling off)."""
    return bool(item.get("enabled", True))


def pin_product_allow(cfg: dict[str, Any], pincode: str | None) -> set[str] | None:
    """Per-pincode product allowlist from pincodes[].products, or None for "all products".

    This is what lets one person's pincode watch a different product set than another.
    Only a missing `products` key means "all"; an empty list means "none".
    """
    if not pincode:
        return None
    from amul_watch.notification_routes import resolve_product_alias
    from amul_watch.pincodes import _all_pincode_entries, _normalize_pincode

    pin = _normalize_pincode(pincode)
    if not pin:
        return None
    for loc in _all_pincode_entries(cfg):
        if _normalize_pincode(loc.get("pincode")) != pin:
            continue
        raw = loc.get("products")
        if isinstance(raw, list):
            return {resolve_product_alias(cfg, str(p)) for p in raw if str(p).strip()}
        if loc.get("disabled_products"):
            return set()
        return None
    return None


def ordered_watchlist(cfg: dict[str, Any], pincode: str | None = None) -> list[dict[str, Any]]:
    """Enabled products for this pincode, in config order."""
    items = [item for item in (cfg.get("watchlist") or []) if item.get("alias") and _item_enabled(item)]
    allow = pin_product_allow(cfg, pincode)
    if allow is not None:
        items = [item for item in items if str(item.get("alias") or "") in allow]
    return items


def watchlist_by_alias(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["alias"]): item
        for item in (cfg.get("watchlist") or [])
        if item.get("alias") and _item_enabled(item)
    }
