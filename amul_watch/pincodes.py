"""Pincodes (delivery addresses) from config.yaml, and their Amul delivery zones."""

from __future__ import annotations

import re
import time
from typing import Any

from amul_watch.client import AmulClient
from amul_watch.db import StockDB


def _normalize_pincode(value: Any) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits if len(digits) == 6 else None


def _entry_enabled(entry: Any, *, default: bool = True) -> bool:
    """True unless an entry explicitly sets enabled: false."""
    if isinstance(entry, dict) and "enabled" in entry:
        return bool(entry.get("enabled"))
    return default


def _all_pincode_entries(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (cfg.get("pincodes") or []) if isinstance(item, dict)]


def pincode_enabled_map(cfg: dict[str, Any]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for loc in _all_pincode_entries(cfg):
        pin = _normalize_pincode(loc.get("pincode"))
        if pin:
            out[pin] = _entry_enabled(loc)
    return out


def is_pincode_enabled(cfg: dict[str, Any], pincode: str) -> bool:
    pin = _normalize_pincode(pincode)
    return bool(pin) and pincode_enabled_map(cfg).get(pin, False)


def pin_short_label(cfg: dict[str, Any], pincode: str | None) -> str:
    """Public-safe place name for messages (`short`), never the private `label`."""
    pin = _normalize_pincode(pincode)
    for loc in _all_pincode_entries(cfg):
        if _normalize_pincode(loc.get("pincode")) == pin and loc.get("short"):
            return str(loc["short"])
    return str(pincode or "")


def format_pin_scope(cfg: dict[str, Any], pincode: str | None = None) -> str:
    if not pincode:
        return "unknown pincode"
    short = pin_short_label(cfg, pincode)
    return f"{short} {pincode}" if short != str(pincode) else str(pincode)


def sort_pincodes(pincodes: list[dict[str, str]], priority_pincode: str) -> list[dict[str, str]]:
    first = [p for p in pincodes if p["pincode"] == priority_pincode]
    return first + [p for p in pincodes if p["pincode"] != priority_pincode]


def collect_pincodes(_client: AmulClient | None, cfg: dict[str, Any]) -> list[dict[str, str]]:
    """Enabled pincodes, priority pin first. Nothing enabled means nothing is polled."""
    found: dict[str, dict[str, str]] = {}
    for loc in _all_pincode_entries(cfg):
        pin = _normalize_pincode(loc.get("pincode"))
        if pin and _entry_enabled(loc) and pin not in found:
            found[pin] = {"pincode": pin, "label": str(loc.get("label") or f"Pin {pin}")}
    return sort_pincodes(list(found.values()), str(cfg.get("priority_pincode") or ""))


def resolve_substore_cached(client: AmulClient, db: StockDB, pincode: str) -> tuple[str, str | None]:
    """Amul's delivery-zone id for a pincode (cached for a day), with the session
    switched to that pincode so the next product reads are for it."""
    cached = db.get_substore(pincode)
    if cached and cached.get("substore_name") and (time.time() - cached["updated_at"]) < 86400:
        client.use_pincode(pincode, str(cached["substore_name"]))
        return cached["substore_id"], cached.get("substore_name")
    substore_id, name = client.resolve_substore(pincode)
    db.set_substore(pincode, substore_id, name)
    return substore_id, name
