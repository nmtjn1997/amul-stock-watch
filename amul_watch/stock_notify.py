"""Turn a stock event into notifier calls."""

from __future__ import annotations

import logging
from typing import Any

from amul_watch.notification_routes import (
    qty_alert_names,
    resolve_channel_names,
    resolve_channel_names_for_pins,
    system_alert_names,
)
from amul_watch.notifiers import Alert, send
from amul_watch.pincodes import format_pin_scope

log = logging.getLogger(__name__)


def stock_route_names(cfg: dict[str, Any], alias: str, pincodes: list[str]) -> list[str]:
    if pincodes:
        return resolve_channel_names_for_pins(cfg, pincodes, alias)
    return resolve_channel_names(cfg, None, alias)


def notify_stock_full(cfg: dict[str, Any], alert: Alert, *, names: list[str] | None = None) -> dict[str, str]:
    """0 to in-stock: every notifier on the route for these pincodes."""
    route = names if names is not None else stock_route_names(cfg, alert.alias, alert.pincodes)
    return send(cfg, route, alert)


def notify_stock_qty(
    cfg: dict[str, Any],
    *,
    product_label: str,
    qty: int,
    prev_qty: int | None = None,
    pincode: str | None = None,
    national: bool = False,
) -> None:
    """While in stock: quantity changes go only to qty_update_alerts (usually a quiet channel)."""
    names = qty_alert_names(cfg)
    if not names:
        return
    try:
        min_delta = max(1, int(cfg.get("qty_update_min_delta", 2)))
    except (TypeError, ValueError):
        min_delta = 2
    if prev_qty is not None and abs(prev_qty - qty) < min_delta:
        return
    where = "all pincodes" if national else format_pin_scope(cfg, pincode)
    change = f"{prev_qty} -> {qty}" if prev_qty is not None and prev_qty != qty else str(qty)
    send(
        cfg,
        names,
        Alert(
            kind="qty",
            title=f"{product_label}: qty {change}",
            message=f"{product_label} @ {where}: qty {change}",
            product=product_label,
            pincodes=[pincode] if pincode else [],
            qty=qty,
        ),
    )


def notify_system(cfg: dict[str, Any], title: str, message: str) -> dict[str, str]:
    """Problems a human should know about (Amul unreachable, session cannot be started)."""
    return send(cfg, system_alert_names(cfg), Alert(kind="system", title=title, message=message))
