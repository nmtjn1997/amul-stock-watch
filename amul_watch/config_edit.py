"""Unified, watch-centric editor for the Amul stock watcher config.

The engine reads two files:
  * config.yaml        -> pincodes[] (address list, with per-pin products[]) and watchlist[]
  * notifications.yaml -> notifiers{} (recipients) and alerts{} (pin:product -> notifier names)

This module presents ONE mental model on top of those files:

    a WATCH = an address (pincode) + a product + the recipients notified when it hits stock

Adding/removing a watch keeps every underlying structure consistent:
  * the address is added/enabled in pincodes[]
  * the product alias is added to that pincode's per-pin `products` allowlist (so ONLY
    the products someone actually wants are polled for their pincode)
  * the product is kept globally enabled in watchlist[] (so it is polled at all)
  * the recipients are written to alerts["<pin>:<short>"]

Round-trip editing (ruamel.yaml when available) preserves comments/formatting.
Reads for resolution go through the same load_config() the engine uses.
"""

from __future__ import annotations

import functools
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from amul_watch.config import (
    DB_PATH,
    DEFAULT_CONFIG,
    LOG_PATH,
    NOTIFICATIONS_CONFIG,
    load_config,
)
from amul_watch.notification_routes import (
    describe_notifier,
    format_delivery_summary,
    product_short,
    resolve_channel_names,
    resolve_product_alias,
)
from amul_watch.notifiers import NOTIFIER_TYPES, REQUIRED_KEYS, validate

# --------------------------------------------------------------------------- #
# YAML round-trip (comment-preserving when ruamel is installed)
# --------------------------------------------------------------------------- #

try:  # pragma: no cover - depends on environment
    from ruamel.yaml import YAML

    _yaml = YAML()
    _yaml.preserve_quotes = True
    # Match the hand-written files' block style: "  - item" (dash indented 2 under
    # its key, content at 4) so unchanged lines stay byte-identical on round-trip.
    _yaml.indent(mapping=2, sequence=4, offset=2)
    _yaml.width = 4096
    _HAVE_RUAMEL = True
except Exception:  # pragma: no cover
    _HAVE_RUAMEL = False
    import yaml as _pyyaml


def _rt_load(path: Path) -> Any:
    if not path.is_file():
        return _new_map()
    text = path.read_text(encoding="utf-8")
    if _HAVE_RUAMEL:
        data = _yaml.load(text)
        return data if data is not None else _new_map()
    data = _pyyaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def _rt_save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    if _HAVE_RUAMEL:
        with tmp.open("w", encoding="utf-8") as fh:
            _yaml.dump(data, fh)
    else:
        with tmp.open("w", encoding="utf-8") as fh:
            _pyyaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, default_flow_style=False)
    for attempt in range(5):
        try:
            tmp.replace(path)
            return
        except PermissionError:  # Windows: the poller may have the file open for a moment
            if attempt == 4:
                raise
            time.sleep(0.1)


def _new_map() -> Any:
    if _HAVE_RUAMEL:
        from ruamel.yaml.comments import CommentedMap

        return CommentedMap()
    return {}


def _new_seq() -> Any:
    if _HAVE_RUAMEL:
        from ruamel.yaml.comments import CommentedSeq

        return CommentedSeq()
    return []


def _seq_remove(entry: Any, key: str, value: str) -> None:
    """Remove value from entry[key] in place, preserving other items' comments."""
    seq = entry.get(key)
    if not seq:
        return
    keep = _new_seq()
    for p in seq:
        if str(p) != value:
            keep.append(p)
    entry[key] = keep


# Every read-modify-write of the YAML files runs under this lock, so two UI requests
# saving at once cannot lose one of the edits.
_WRITE_LOCK = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _WRITE_LOCK:
            return fn(*args, **kwargs)
    return wrapper


def _product_in(cfg: dict[str, Any], seq: Any, alias: str) -> bool:
    """Product lists may hold short names or full aliases; compare what they resolve to."""
    return any(resolve_product_alias(cfg, str(p)) == alias for p in (seq or []))


def _product_add(cfg: dict[str, Any], seq: Any, alias: str) -> None:
    if not _product_in(cfg, seq, alias):
        seq.append(product_short(cfg, alias))


def _product_remove(cfg: dict[str, Any], entry: Any, key: str, alias: str) -> None:
    seq = entry.get(key)
    if not seq:
        return
    keep = _new_seq()
    for p in seq:
        if resolve_product_alias(cfg, str(p)) != alias:
            keep.append(p)
    entry[key] = keep


# --------------------------------------------------------------------------- #
# Product shorthand helpers
# --------------------------------------------------------------------------- #

def _known_products(cfg: dict[str, Any]) -> list[dict[str, str]]:
    """All products (from watchlist[]) with a stable shorthand + alias + label."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in cfg.get("watchlist") or []:
        alias = str(item.get("alias") or "")
        if not alias or alias in seen:
            continue
        seen.add(alias)
        out.append(
            {
                "alias": alias,
                "short": product_short(cfg, alias),
                "label": str(item.get("label") or alias),
                "enabled": bool(item.get("enabled", True)),
                "enquiry_name": str(item.get("enquiry_name") or ""),
            }
        )
    return out


def _known_alias(cfg: dict[str, Any], product: str) -> str:
    """The full alias of a product in the watchlist, or a clear error."""
    alias = resolve_product_alias(cfg, product)
    if not alias or not any(str(i.get("alias") or "") == alias for i in cfg.get("watchlist") or []):
        known = ", ".join(product_short(cfg, str(i.get("alias"))) for i in cfg.get("watchlist") or []) or "none yet"
        raise ValueError(f"unknown product {product!r}: add it under Products first (known: {known})")
    return alias


def _check_notifier_names(cfg: dict[str, Any], names: list[str]) -> None:
    known = set((cfg.get("notifiers") or {}).keys())
    missing = [n for n in names if n not in known]
    if missing:
        raise ValueError(f"unknown notifier(s) {', '.join(missing)}: add them in the Notifiers tab first")


def _norm_pin(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 6 else ""


# --------------------------------------------------------------------------- #
# Read side: build the state the UI renders
# --------------------------------------------------------------------------- #

def get_state() -> dict[str, Any]:
    cfg = load_config()
    from amul_watch.pincodes import _all_pincode_entries, _entry_enabled, _normalize_pincode

    products = _known_products(cfg)
    alias_by_short = {p["short"]: p["alias"] for p in products}
    label_by_alias = {p["alias"]: p["label"] for p in products}

    # addresses
    addresses: list[dict[str, Any]] = []
    for loc in _all_pincode_entries(cfg):
        pin = _normalize_pincode(loc.get("pincode"))
        if not pin:
            continue
        prod_aliases: list[str] = []
        raw = loc.get("products")
        if isinstance(raw, list):
            prod_aliases = [resolve_product_alias(cfg, str(p)) for p in raw if str(p).strip()]
        disabled_aliases: list[str] = []
        raw_dis = loc.get("disabled_products")
        if isinstance(raw_dis, list):
            disabled_aliases = [resolve_product_alias(cfg, str(p)) for p in raw_dis if str(p).strip()]
        addresses.append(
            {
                "pincode": pin,
                "label": str(loc.get("label") or pin),
                "short": str(loc.get("short") or loc.get("short_label") or ""),
                "enabled": _entry_enabled(loc),
                "products": prod_aliases,
                "disabled_products": disabled_aliases,
            }
        )

    # notifiers (who can be alerted)
    recipients: list[dict[str, Any]] = []
    for name, spec in (cfg.get("notifiers") or {}).items():
        if not isinstance(spec, dict):
            continue
        recipients.append(
            {
                "name": str(name),
                "type": str(spec.get("type") or ""),
                "enabled": spec.get("enabled") is not False,
                "config": _masked(spec),
                "summary": describe_notifier(str(name), spec),
                "problems": validate(spec),
            }
        )

    # watches = pincode x product. Active ones (in products[]) are enabled; parked ones
    # (in disabled_products[]) show as disabled so they can be re-enabled without recreating.
    watches: list[dict[str, Any]] = []

    def _mk_watch(pin: str, addr: dict[str, Any], alias: str, enabled: bool) -> dict[str, Any]:
        short = product_short(cfg, alias)
        names = resolve_channel_names(cfg, pin, alias)
        return {
            "pincode": pin,
            "address_label": addr["label"],
            "alias": alias,
            "product_short": short,
            "product_label": label_by_alias.get(alias, alias),
            "recipients": names,
            "recipients_summary": format_delivery_summary(cfg, names),
            "route_key": f"{pin}:{short}",
            "enabled": enabled,
            "explicit": bool(addr["products"]),
            # True when this watch has its own route; False means it follows default_alerts.
            "has_route": any(
                k in (cfg.get("alerts") or {})
                for k in (f"{pin}:{short}", f"{pin}:{alias}", f"*:{short}", f"*:{alias}")
            ),
        }

    from amul_watch.watchlist_util import pin_product_allow

    enabled_products = {p["alias"] for p in products if p["enabled"]}
    for addr in addresses:
        pin = addr["pincode"]
        # Same rule the poller uses, so the UI never shows a watch that is not polled.
        allow = pin_product_allow(cfg, pin)
        if allow is None:
            active_aliases = [p["alias"] for p in products if p["enabled"]] if addr["enabled"] else []
        else:
            active_aliases = [a for a in addr["products"] if a in allow]
        seen_here: set[str] = set()
        for alias in active_aliases:
            seen_here.add(alias)
            watches.append(_mk_watch(pin, addr, alias, addr["enabled"] and alias in enabled_products))
        for alias in addr.get("disabled_products") or []:
            if alias in seen_here:
                continue  # active list wins if somehow in both
            seen_here.add(alias)
            watches.append(_mk_watch(pin, addr, alias, False))
    watches.sort(key=lambda w: (w["pincode"], not w["enabled"], w["product_label"]))

    default_alerts = [str(n) for n in (cfg.get("default_alerts") or [])]

    return {
        "addresses": sorted(addresses, key=lambda a: a["pincode"]),
        "products": products,
        "recipients": recipients,
        "watches": watches,
        "default_alerts": default_alerts,
        "product_shorthands": sorted(alias_by_short),
        "notifier_types": [
            {"type": t, "description": desc, "required": REQUIRED_KEYS.get(t, [])}
            for t, (_fn, desc) in NOTIFIER_TYPES.items()
        ],
        "system_alerts": [str(n) for n in (cfg.get("system_alerts") or [])],
        "qty_update_alerts": [str(n) for n in (cfg.get("qty_update_alerts") or [])],
        "daemon": daemon_state(),
        "config_paths": {
            "config": str(DEFAULT_CONFIG),
            "notifications": str(NOTIFICATIONS_CONFIG),
        },
    }


def live_stock() -> list[dict[str, str]]:
    from amul_watch.db import StockDB
    from amul_watch.inventory import load_inventory_rows

    cfg = load_config()
    db = StockDB(DB_PATH)
    try:
        return load_inventory_rows(db, cfg, enabled_only=True)
    except Exception:
        return []


def daemon_state() -> dict[str, Any]:
    from amul_watch.daemon import watcher_state

    state = watcher_state()
    state["last_log"] = recent_log(6)
    return state


def recent_log(n: int = 6) -> list[str]:
    if not LOG_PATH.is_file():
        return []
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-n:]


# --------------------------------------------------------------------------- #
# Write side: watch-centric mutations
# --------------------------------------------------------------------------- #

def _find_pincode_entry(cfg_doc: Any, pincode: str) -> Any | None:
    for loc in cfg_doc.get("pincodes") or []:
        if _norm_pin(loc.get("pincode")) == pincode:
            return loc
    return None


def _ensure_pincode_entry(cfg_doc: Any, pincode: str, *, label: str = "", short: str = "") -> Any:
    entry = _find_pincode_entry(cfg_doc, pincode)
    if entry is not None:
        if label and not entry.get("label"):
            entry["label"] = label
        if short and not entry.get("short"):
            entry["short"] = short
        return entry
    if "pincodes" not in cfg_doc or cfg_doc.get("pincodes") is None:
        cfg_doc["pincodes"] = _new_seq()
    entry = _new_map()
    entry["pincode"] = pincode
    entry["label"] = label or f"Pin {pincode}"
    if short:
        entry["short"] = short
    entry["enabled"] = True
    entry["products"] = _new_seq()
    cfg_doc["pincodes"].append(entry)
    return entry


def _find_watch_item(cfg_doc: Any, alias: str) -> Any | None:
    for item in cfg_doc.get("watchlist") or []:
        if str(item.get("alias") or "") == alias:
            return item
    return None


def _ensure_watchlist_item(
    cfg_doc: Any, alias: str, *, label: str = "", short: str = "", enquiry_name: str = ""
) -> Any:
    item = _find_watch_item(cfg_doc, alias)
    if item is not None:
        item["enabled"] = True
        return item
    if "watchlist" not in cfg_doc or cfg_doc.get("watchlist") is None:
        cfg_doc["watchlist"] = _new_seq()
    item = _new_map()
    item["alias"] = alias
    item["label"] = label or alias.replace("-", " ").title()
    if short:
        item["short"] = short
    if enquiry_name:
        item["enquiry_name"] = enquiry_name
    item["prefer_pack_of_30"] = True
    item["enabled"] = True
    cfg_doc["watchlist"].append(item)
    return item


def _recompute_global_product_flags(cfg_doc: Any) -> None:
    """Ensure any product used by a per-pin products[] list is globally enabled.

    ONLY enables, never disables. `ordered_watchlist` filters on the global
    `enabled` flag before applying the per-pin allowlist, so a product used by a
    pincode must be globally enabled or it would be dropped. Disabling is left to
    explicit user action; auto-disabling here would silently stop polling a product
    the moment another pin got an explicit list (incremental-edit hazard).
    """
    cfg = load_config()
    used: set[str] = set()
    for loc in cfg_doc.get("pincodes") or []:
        for p in loc.get("products") or []:
            used.add(resolve_product_alias(cfg, str(p)))
    for item in cfg_doc.get("watchlist") or []:
        alias = str(item.get("alias") or "")
        if alias in used and item.get("enabled") is False:
            item["enabled"] = True


def _set_pin_enabled_from_products(entry: Any) -> None:
    prods = entry.get("products") or []
    entry["enabled"] = bool(len(prods) > 0)


@_locked
def upsert_watch(
    pincode: str,
    product: str,
    recipients: list[str] | None,
    *,
    enabled: bool = True,
    address_label: str = "",
    address_short: str = "",
) -> dict[str, Any]:
    """Create or update one watch (pincode + product -> recipients).

    recipients=None leaves the route alone; [] removes it so the watch follows
    default_alerts; a list sets it.
    """
    pin = _norm_pin(pincode)
    if not pin:
        raise ValueError(f"invalid pincode: {pincode!r} (six digits)")
    cfg = load_config()
    alias = _known_alias(cfg, product)
    short = product_short(cfg, alias)
    if recipients:
        _check_notifier_names(cfg, [str(r).strip() for r in recipients if str(r).strip()])
    if recipients is not None:
        recipients = [str(r).strip() for r in recipients if str(r).strip()]

    # --- config.yaml: address + per-pin product allowlist + watchlist item ---
    cfg_doc = _rt_load(DEFAULT_CONFIG)
    entry = _ensure_pincode_entry(cfg_doc, pin, label=address_label, short=address_short)
    if "products" not in entry or entry.get("products") is None:
        entry["products"] = _new_seq()
    if enabled:
        # active watch: ensure in products, remove from disabled_products (parked list)
        _product_add(cfg, entry["products"], alias)
        _product_remove(cfg, entry, "disabled_products", alias)
    else:
        # disable ≠ delete: park the alias in disabled_products (kept in config, not polled)
        _product_remove(cfg, entry, "products", alias)
        if "disabled_products" not in entry or entry.get("disabled_products") is None:
            entry["disabled_products"] = _new_seq()
        _product_add(cfg, entry["disabled_products"], alias)
    _set_pin_enabled_from_products(entry)
    _ensure_watchlist_item(cfg_doc, alias)
    _recompute_global_product_flags(cfg_doc)
    _rt_save(DEFAULT_CONFIG, cfg_doc)

    # --- notifications.yaml: recipients route (skip if unchanged to keep comments) ---
    if recipients is not None:
        notes_doc = _rt_load(NOTIFICATIONS_CONFIG)
        if "alerts" not in notes_doc or notes_doc.get("alerts") is None:
            notes_doc["alerts"] = _new_map()
        alerts = notes_doc["alerts"]
        changed = False
        if recipients:
            changed = _set_route(alerts, f"{pin}:{short}", recipients)
            if f"{pin}:{alias}" in alerts and alias != short:
                del alerts[f"{pin}:{alias}"]
                changed = True
        else:
            for key in (f"{pin}:{short}", f"{pin}:{alias}"):
                if key in alerts:
                    del alerts[key]
                    changed = True
        if changed:
            _rt_save(NOTIFICATIONS_CONFIG, notes_doc)

    return {"pincode": pin, "alias": alias, "short": short, "enabled": enabled, "recipients": recipients}


def _set_route(alerts: Any, key: str, recipients: list[str]) -> bool:
    """Write alerts[key]=recipients only when changed. Mutates an existing sequence
    in place (preserves ruamel comments) instead of replacing the node. Returns
    True if a change was written."""
    existing = alerts.get(key)
    if existing is not None and [str(x) for x in existing] == list(recipients):
        return False
    if existing is not None and hasattr(existing, "clear") and hasattr(existing, "append"):
        existing.clear()
        for r in recipients:
            existing.append(r)
    else:
        seq = _new_seq()
        for r in recipients:
            seq.append(r)
        alerts[key] = seq
    return True


@_locked
def delete_watch(pincode: str, product: str) -> dict[str, Any]:
    pin = _norm_pin(pincode)
    if not pin:
        raise ValueError(f"invalid pincode: {pincode!r} (six digits)")
    cfg = load_config()
    alias = _known_alias(cfg, product)
    short = product_short(cfg, alias)

    cfg_doc = _rt_load(DEFAULT_CONFIG)
    entry = _find_pincode_entry(cfg_doc, pin)
    if entry is not None:
        # full removal: drop from both the active and the parked (disabled) lists
        _product_remove(cfg, entry, "products", alias)
        _product_remove(cfg, entry, "disabled_products", alias)
        _set_pin_enabled_from_products(entry)
    _recompute_global_product_flags(cfg_doc)
    _rt_save(DEFAULT_CONFIG, cfg_doc)

    notes_doc = _rt_load(NOTIFICATIONS_CONFIG)
    alerts = notes_doc.get("alerts") or {}
    key = f"{pin}:{short}"
    if key in alerts:
        del alerts[key]
        _rt_save(NOTIFICATIONS_CONFIG, notes_doc)
    return {"pincode": pin, "alias": alias, "deleted": True}


@_locked
def set_watch_enabled(pincode: str, product: str, enabled: bool) -> dict[str, Any]:
    return upsert_watch(pincode, product, None, enabled=enabled)


@_locked
def populate_disabled_watches() -> dict[str, Any]:
    """Ensure every address × known product exists as a watch.

    Any (pincode, product) pair that is neither active (in products[]) nor already
    parked (in disabled_products[]) is added to disabled_products[]. This gives every
    combination a ready-to-toggle disabled watch, so users can enable one later
    instead of creating it from scratch. Never touches active watches.
    """
    cfg = load_config()
    product_aliases = [p["alias"] for p in _known_products(cfg) if p.get("enabled", True)]
    cfg_doc = _rt_load(DEFAULT_CONFIG)
    added = 0
    pins_touched = 0
    for loc in cfg_doc.get("pincodes") or []:
        pin = _norm_pin(loc.get("pincode"))
        if not pin:
            continue
        active = {resolve_product_alias(cfg, str(p)) for p in (loc.get("products") or [])}
        parked = {resolve_product_alias(cfg, str(p)) for p in (loc.get("disabled_products") or [])}
        missing = [a for a in product_aliases if a not in active and a not in parked]
        if not missing:
            continue
        if "disabled_products" not in loc or loc.get("disabled_products") is None:
            loc["disabled_products"] = _new_seq()
        for alias in missing:
            loc["disabled_products"].append(product_short(cfg, alias))
            added += 1
        pins_touched += 1
    if added:
        _rt_save(DEFAULT_CONFIG, cfg_doc)
    return {"added": added, "pins": pins_touched}


# --------------------------------------------------------------------------- #
# Address / product / recipient CRUD
# --------------------------------------------------------------------------- #

@_locked
def upsert_address(pincode: str, label: str = "", short: str = "", enabled: bool | None = None) -> dict[str, Any]:
    pin = _norm_pin(pincode)
    if not pin:
        raise ValueError(f"invalid pincode: {pincode!r}")
    cfg_doc = _rt_load(DEFAULT_CONFIG)
    entry = _ensure_pincode_entry(cfg_doc, pin, label=label, short=short)
    if label:
        entry["label"] = label
    if short:
        entry["short"] = short
    if enabled is not None:
        entry["enabled"] = bool(enabled)
    _rt_save(DEFAULT_CONFIG, cfg_doc)
    return {"pincode": pin, "label": entry.get("label"), "short": entry.get("short")}


@_locked
def delete_address(pincode: str) -> dict[str, Any]:
    pin = _norm_pin(pincode)
    cfg_doc = _rt_load(DEFAULT_CONFIG)
    pincodes = cfg_doc.get("pincodes") or []
    keep = _new_seq()
    for loc in pincodes:
        if _norm_pin(loc.get("pincode")) != pin:
            keep.append(loc)
    cfg_doc["pincodes"] = keep
    _rt_save(DEFAULT_CONFIG, cfg_doc)
    # drop any routes for this pin
    notes_doc = _rt_load(NOTIFICATIONS_CONFIG)
    alerts = notes_doc.get("alerts") or {}
    for key in [k for k in list(alerts.keys()) if str(k).startswith(f"{pin}:")]:
        del alerts[key]
    _rt_save(NOTIFICATIONS_CONFIG, notes_doc)
    return {"pincode": pin, "deleted": True}


@_locked
def upsert_product(alias_or_short: str, label: str = "", short: str = "", enquiry_name: str = "") -> dict[str, Any]:
    """Add or edit a product. `alias` is the slug in the shop URL: shop.amul.com/en/product/<alias>."""
    cfg = load_config()
    raw = str(alias_or_short or "").strip()
    if not raw:
        raise ValueError("product URL or alias required")
    raw = raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")  # accept a pasted product URL
    alias = resolve_product_alias(cfg, raw).rsplit("/product/", 1)[-1].lower()
    if not re.fullmatch(r"[a-z0-9-]+", alias):
        raise ValueError(f"invalid product alias {alias!r}: use the slug from the product URL")
    short = re.sub(r"[^a-z0-9-]+", "-", short.strip().lower()).strip("-")
    for other in cfg.get("watchlist") or []:
        other_alias = str(other.get("alias") or "")
        if other_alias != alias and short and short in (str(other.get("short") or ""), other_alias):
            raise ValueError(f"short name {short!r} is already used by {other_alias}")
    cfg_doc = _rt_load(DEFAULT_CONFIG)
    item = _find_watch_item(cfg_doc, alias)
    if item is None:
        item = _ensure_watchlist_item(cfg_doc, alias, label=label, short=short, enquiry_name=enquiry_name)
    else:
        if label:
            item["label"] = label
        if short:
            item["short"] = short
        if enquiry_name:
            item["enquiry_name"] = enquiry_name
    _rt_save(DEFAULT_CONFIG, cfg_doc)
    return {"alias": alias, "short": item.get("short") or alias, "label": item.get("label")}


# Settings that are secrets. The UI gets them masked unless they are ${VAR} references,
# and saving the mask back keeps the stored value.
SECRET_KEYS = {"token", "bot_token", "password"}
SECRET_URL_TYPES = {"slack_webhook", "discord", "webhook"}
MASK = "********"


def _is_secret(ntype: str, key: str) -> bool:
    return key in SECRET_KEYS or (key == "url" and ntype in SECRET_URL_TYPES)


def _masked(spec: dict[str, Any]) -> dict[str, Any]:
    ntype = str(spec.get("type") or "")
    out: dict[str, Any] = {}
    for key, value in spec.items():
        if key in ("type", "enabled"):
            continue
        if _is_secret(ntype, key) and isinstance(value, str) and value and not value.startswith("${"):
            out[key] = MASK
        elif key == "headers" and isinstance(value, dict):
            out[key] = {k: (v if str(v).startswith("${") else MASK) for k, v in value.items()}
        else:
            out[key] = value
    return out


@_locked
def upsert_notifier(
    name: str, ntype: str, fields: dict[str, Any] | None = None, *, enabled: bool | None = None
) -> dict[str, Any]:
    """Create a notifier, or update one in place.

    Only the keys present in `fields` change (an empty value removes that key), so
    settings the UI form does not show, like webhook headers, survive an edit.
    enabled=None keeps the current state.
    """
    name = str(name).strip()
    ntype = str(ntype).strip().lower()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError(f"notifier name {name!r}: use letters, digits, - _ or . (routes refer to it)")
    if ntype not in NOTIFIER_TYPES:
        raise ValueError(f"unknown notifier type {ntype!r} (known: {', '.join(NOTIFIER_TYPES)})")
    notes_doc = _rt_load(NOTIFICATIONS_CONFIG)
    if notes_doc.get("notifiers") is None:
        notes_doc["notifiers"] = _new_map()
    existing = notes_doc["notifiers"].get(name)
    if existing is not None and str(existing.get("type") or "") == ntype:
        spec = existing
    else:
        spec = _new_map()
        spec["type"] = ntype
    for key, value in (fields or {}).items():
        if key in ("type", "name", "enabled"):
            continue
        if value == MASK:
            continue  # unchanged secret shown masked in the UI
        if value in (None, "", [], False):
            if key in spec:
                del spec[key]
        elif isinstance(value, list):
            seq = _new_seq()
            for v in value:
                if str(v).strip():
                    seq.append(str(v).strip())
            spec[key] = seq
        else:
            spec[key] = value
    if enabled is True and "enabled" in spec:
        del spec["enabled"]
    elif enabled is False:
        spec["enabled"] = False
    problems = validate(dict(spec), resolve=False)
    if problems:
        raise ValueError(f"notifier {name!r}: " + "; ".join(problems))
    notes_doc["notifiers"][name] = spec
    _rt_save(NOTIFICATIONS_CONFIG, notes_doc)
    return {"name": name, "type": ntype}


@_locked
def delete_notifier(name: str) -> dict[str, Any]:
    """Remove a notifier and every reference to it. A route left empty is removed, so
    those watches follow default_alerts."""
    notes_doc = _rt_load(NOTIFICATIONS_CONFIG)
    notifiers = notes_doc.get("notifiers") or {}
    if name in notifiers:
        del notifiers[name]
    alerts = notes_doc.get("alerts") or {}
    for key in list(alerts.keys()):
        seq = alerts[key]
        if isinstance(seq, list) and name in [str(x) for x in seq]:
            _seq_remove(alerts, key, name)
            if not alerts[key]:
                del alerts[key]
    for key in ("default_alerts", "system_alerts", "qty_update_alerts"):
        if isinstance(notes_doc.get(key), list):
            _seq_remove(notes_doc, key, name)
    _rt_save(NOTIFICATIONS_CONFIG, notes_doc)
    return {"name": name, "deleted": True}


@_locked
def set_name_list(key: str, names: list[str]) -> dict[str, Any]:
    """default_alerts, system_alerts or qty_update_alerts."""
    if key not in ("default_alerts", "system_alerts", "qty_update_alerts"):
        raise ValueError(f"not a notifier list: {key!r}")
    _check_notifier_names(load_config(), [str(n).strip() for n in names if str(n).strip()])
    notes_doc = _rt_load(NOTIFICATIONS_CONFIG)
    seq = _new_seq()
    for n in names:
        if str(n).strip():
            seq.append(str(n).strip())
    notes_doc[key] = seq
    _rt_save(NOTIFICATIONS_CONFIG, notes_doc)
    return {key: [str(n) for n in seq]}


@_locked
def normalize_watches() -> dict[str, Any]:
    """One-time: give every enabled pincode an explicit per-pin products[] list
    derived from its existing routes (falls back to all globally-enabled products).

    This converts the implicit pins x products cross-product into explicit per-pin
    sets, so each pincode only polls the products someone asked for.
    """
    cfg = load_config()
    from amul_watch.pincodes import _all_pincode_entries, _entry_enabled, _normalize_pincode

    alerts = cfg.get("alerts") or {}
    global_enabled = [
        str(i.get("alias"))
        for i in (cfg.get("watchlist") or [])
        if i.get("alias") and i.get("enabled", True) is not False
    ]

    routes_by_pin: dict[str, list[str]] = {}
    for key in alerts:
        pin, _, short = str(key).partition(":")
        if pin == "*" or not _norm_pin(pin):
            continue
        alias = resolve_product_alias(cfg, short)
        routes_by_pin.setdefault(pin, [])
        if alias not in routes_by_pin[pin]:
            routes_by_pin[pin].append(alias)

    cfg_doc = _rt_load(DEFAULT_CONFIG)
    changed: list[str] = []
    for loc in cfg_doc.get("pincodes") or []:
        pin = _norm_pin(loc.get("pincode"))
        if not pin:
            continue
        # find the merged-cfg entry to know enabled state
        src = next((e for e in _all_pincode_entries(cfg) if _normalize_pincode(e.get("pincode")) == pin), {})
        if not _entry_enabled(src):
            continue
        if isinstance(loc.get("products"), list) and loc.get("products"):
            continue  # already explicit
        aliases = routes_by_pin.get(pin) or list(global_enabled)
        seq = _new_seq()
        for a in aliases:
            seq.append(a)
        loc["products"] = seq
        changed.append(pin)
    _recompute_global_product_flags(cfg_doc)
    _rt_save(DEFAULT_CONFIG, cfg_doc)
    return {"normalized_pincodes": changed}


# --------------------------------------------------------------------------- #
# Watcher control. Config needs no "apply": the poll loop re-reads it every cycle.
# --------------------------------------------------------------------------- #

def pause_daemon(reason: str = "") -> dict[str, Any]:
    from amul_watch.daemon import pause

    return pause(reason)


def resume_daemon() -> dict[str, Any]:
    from amul_watch.daemon import resume

    return resume()
