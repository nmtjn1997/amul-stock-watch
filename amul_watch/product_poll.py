from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from amul_watch.db import StockDB

from amul_watch.stock import ProductStock
from amul_watch.stock_alert import fire_transition_alert
from amul_watch.stock_notify import notify_stock_qty

log = logging.getLogger(__name__)


@dataclass
class PinPollResult:
    pincode: str
    pin_label: str
    alias: str
    product_label: str
    stock: ProductStock
    in_stock: bool
    qty: int
    variant_label: str
    was_in_stock: bool
    prev_qty: int
    key: str
    had_data: bool = True  # False when the API returned an empty body (session likely dead)


def _in_stock_results(results: list[PinPollResult]) -> list[PinPollResult]:
    return [r for r in results if r.in_stock]


def pins_unified(results: list[PinPollResult]) -> bool:
    """True when every in-stock pin reports the same qty (national pool)."""
    ins = _in_stock_results(results)
    if len(ins) < 2:
        return len(ins) == 1
    return len({r.qty for r in ins}) == 1


def _baseline_key(alias: str, pincode: str | None) -> str:
    return f"product_qty:{alias}" if pincode is None else f"qty:{pincode}:{alias}"


def _get_baseline(db: StockDB, key: str) -> int | None:
    raw = db.get_meta(key)
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def _qty_update(cfg: dict[str, Any], db: StockDB, *, label: str, alias: str, qty: int,
                pincode: str | None) -> bool:
    """Report a quantity change against the last *reported* quantity, so a slow drain
    (10, 9, 8, ...) is still reported once it adds up to qty_update_min_delta."""
    key = _baseline_key(alias, pincode)
    baseline = _get_baseline(db, key)
    if baseline is None:
        db.set_meta(key, str(qty))
        return False
    if qty == baseline:
        return False
    sent = notify_stock_qty(cfg, product_label=label, qty=qty, prev_qty=baseline,
                            pincode=pincode, national=pincode is None)
    if sent:
        db.set_meta(key, str(qty))
    return sent


def process_product_alerts(
    cfg: dict[str, Any],
    db: StockDB,
    client: Any,
    results: list[PinPollResult],
) -> tuple[int, int]:
    """Full alerts and qty updates for one product. Returns (alerts, qty_updates).

    The gate is the alert_sent row, not the previous poll: a pincode is alerted when it
    is in stock and has not been alerted since it was last out of stock. The row is only
    written once a notifier accepted the alert, so a failed send or a restart between
    the stock read and the send is retried on the next cycle.
    """
    if not results:
        return 0, 0

    alias = results[0].alias
    product_label = results[0].product_label
    in_stock = _in_stock_results(results)
    for r in results:
        if not r.in_stock:
            db.clear_alert(r.key)
            db.set_meta(_baseline_key(alias, r.pincode), "")
    if not in_stock:
        db.set_meta(_baseline_key(alias, None), "")
        return 0, 0

    transitions = [r for r in in_stock if not db.alert_sent(r.key)]
    alerts = 0
    qty_updates = 0

    if transitions:
        primary = transitions[0]
        pins = [r.pincode for r in transitions]
        delivered = fire_transition_alert(
            cfg,
            db,
            client,
            alias=alias,
            product_name=primary.stock.name or product_label,
            url=primary.stock.url,
            source="poll",
            pincode=primary.pincode,
            pin_label=primary.pin_label,
            stock=primary.stock,
            alert_pincodes=pins,
        )
        if delivered:
            alerts += 1
            for r in transitions:
                db.mark_alert(r.key)
                db.set_meta(_baseline_key(alias, r.pincode), str(r.qty))
            db.set_meta(_baseline_key(alias, None), str(in_stock[0].qty))
        else:
            log.warning("no notifier accepted the %s alert; it will be retried next cycle", product_label)

    fresh = {r.pincode for r in transitions}
    if pins_unified(results):
        if not fresh and _qty_update(cfg, db, label=product_label, alias=alias,
                                     qty=in_stock[0].qty, pincode=None):
            qty_updates += 1
    else:
        for r in in_stock:
            if r.pincode not in fresh and _qty_update(cfg, db, label=product_label, alias=alias,
                                                      qty=r.qty, pincode=r.pincode):
                qty_updates += 1
    return alerts, qty_updates
