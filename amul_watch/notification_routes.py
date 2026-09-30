"""Alert routing: which notifiers hear about which pincode + product.

notifications.yaml:

    alerts:
      "560001:rose-lassi": [me-email, phone]   # one pincode, one product
      "*:rose-lassi":      [phone]             # any pincode, this product
    default_alerts: [desktop]                  # anything without a route
    system_alerts:  [desktop]                  # session / Amul API trouble
    qty_update_alerts: []                      # quantity changes while in stock

Lookup order for one pin + product: exact `pin:short`, then `pin:alias`, then
`*:short`, then `*:alias`, then default_alerts. The first hit wins; routes do not merge.
"""

from __future__ import annotations

from typing import Any

from amul_watch.notifiers import notifier_map


# --------------------------------------------------------------------------- #
# Product names
# --------------------------------------------------------------------------- #

def product_short(cfg: dict[str, Any], alias: str) -> str:
    """The short name used in route keys: watchlist[].short, else the full alias."""
    for item in cfg.get("watchlist") or []:
        if str(item.get("alias") or "") == alias and item.get("short"):
            return str(item["short"])
    return alias


def resolve_product_alias(cfg: dict[str, Any], product: str) -> str:
    """Accept a short name or a full alias; return the full alias."""
    key = str(product).strip().lower()
    for item in cfg.get("watchlist") or []:
        alias = str(item.get("alias") or "")
        if key in (alias.lower(), str(item.get("short") or "").lower()):
            return alias
    return str(product).strip()


def alert_route_key(cfg: dict[str, Any], pincode: str | None, alias: str) -> str:
    return f"{pincode or '*'}:{product_short(cfg, alias)}"


# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #

def _alerts_map(cfg: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, names in (cfg.get("alerts") or {}).items():
        if isinstance(names, list):
            out[str(key)] = [str(n) for n in names]
    return out


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def resolve_channel_names(cfg: dict[str, Any], pincode: str | None, alias: str) -> list[str]:
    """Notifier names for one pin + product."""
    alerts = _alerts_map(cfg)
    short = product_short(cfg, alias)
    candidates = []
    if pincode:
        candidates += [f"{pincode}:{short}", f"{pincode}:{alias}"]
    candidates += [f"*:{short}", f"*:{alias}"]
    for key in candidates:
        if key in alerts:
            return _dedupe(alerts[key])
    return _dedupe([str(n) for n in (cfg.get("default_alerts") or [])])


def resolve_channel_names_for_pins(cfg: dict[str, Any], pincodes: list[str], alias: str) -> list[str]:
    """Union across pins, so one restock seen at three pincodes is one alert per notifier."""
    merged: list[str] = []
    for pin in pincodes:
        merged += resolve_channel_names(cfg, pin, alias)
    return _dedupe(merged) or resolve_channel_names(cfg, None, alias)


def system_alert_names(cfg: dict[str, Any]) -> list[str]:
    return [str(n) for n in (cfg.get("system_alerts") or [])]


def qty_alert_names(cfg: dict[str, Any]) -> list[str]:
    return [str(n) for n in (cfg.get("qty_update_alerts") or [])]


def enquiry_emails(cfg: dict[str, Any]) -> list[str]:
    """Addresses Amul's own notify-me form is registered with (register-enquiries)."""
    raw = cfg.get("enquiry_emails") or []
    if raw:
        return _dedupe([str(e).strip() for e in raw if str(e).strip()])
    emails: list[str] = []
    for spec in notifier_map(cfg).values():
        if spec.get("type") == "email":
            emails += [str(a).strip() for a in (spec.get("to") or []) if str(a).strip()]
    return _dedupe(emails)


# --------------------------------------------------------------------------- #
# Human-readable descriptions (CLI `alerts`, UI chips)
# --------------------------------------------------------------------------- #

def describe_notifier(name: str, spec: dict[str, Any] | None) -> str:
    if spec is None:
        return f"{name}: MISSING (referenced by a route but not defined)"
    kind = str(spec.get("type") or "?")
    detail = ""
    if kind == "email":
        detail = ", ".join(str(a) for a in spec.get("to") or [])
    elif kind in ("slack", "telegram"):
        detail = str(spec.get("channel") or spec.get("chat_id") or "")
    elif kind == "ntfy":
        detail = f"topic {spec.get('topic', '?')}"
    elif kind == "command":
        detail = str(spec.get("command") or "")
    state = " (disabled)" if spec.get("enabled") is False else ""
    return f"{name}: {kind}{' ' + detail if detail else ''}{state}"


def format_delivery_summary(cfg: dict[str, Any], names: list[str]) -> list[str]:
    notifiers = notifier_map(cfg)
    return [describe_notifier(n, notifiers.get(n)) for n in names] or ["(nobody: no route and no default_alerts)"]


def describe_routes(cfg: dict[str, Any], *, pincode: str | None = None, alias: str | None = None) -> list[str]:
    notifiers = notifier_map(cfg)
    lines = ["notifiers:"]
    if not notifiers:
        lines.append("  (none defined: add some under `notifiers:` in notifications.yaml)")
    for name, spec in sorted(notifiers.items()):
        lines.append(f"  {describe_notifier(name, spec)}")
    lines += ["", "alerts (pincode:product -> notifiers):"]
    alerts = _alerts_map(cfg)
    if not alerts:
        lines.append("  (none: everything goes to default_alerts)")
    for key, names in sorted(alerts.items()):
        lines.append(f"  {key}: {', '.join(names)}")
    lines += [
        "",
        f"default_alerts:    {', '.join(str(n) for n in cfg.get('default_alerts') or []) or '-'}",
        f"system_alerts:     {', '.join(system_alert_names(cfg)) or '-'}",
        f"qty_update_alerts: {', '.join(qty_alert_names(cfg)) or '-'}",
    ]
    if pincode and alias:
        names = resolve_channel_names(cfg, pincode, alias)
        lines += ["", f"route {alert_route_key(cfg, pincode, alias)}:"]
        lines += [f"  {line}" for line in format_delivery_summary(cfg, names)]
    return lines


def describe_live_alerts(cfg: dict[str, Any]) -> list[str]:
    """Every pincode x product actually polled right now, and who it alerts."""
    from amul_watch.pincodes import _all_pincode_entries, _entry_enabled, _normalize_pincode
    from amul_watch.watchlist_util import ordered_watchlist

    lines = [f"{'PIN':<8} {'PRODUCT':<22} NOTIFIERS", "-" * 72]
    live = 0
    for loc in _all_pincode_entries(cfg):
        pin = _normalize_pincode(loc.get("pincode"))
        if not pin or not _entry_enabled(loc):
            continue
        for item in ordered_watchlist(cfg, pin):
            alias = str(item.get("alias"))
            label = str(item.get("label") or alias)
            names = resolve_channel_names(cfg, pin, alias)
            lines.append(f"{pin:<8} {label[:22]:<22} {', '.join(names) or '(nobody)'}")
            live += 1
    if not live:
        lines.append("nothing is being watched: enable a pincode and a product (config.yaml or the web UI)")
    return lines
