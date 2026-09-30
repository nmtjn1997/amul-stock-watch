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


def _product_qty_meta_key(alias: str) -> str:
    return f"product_qty:{alias}"


def _last_unified_qty(db: StockDB, alias: str) -> int | None:
    raw = db.get_meta(_product_qty_meta_key(alias))
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _set_unified_qty(db: StockDB, alias: str, qty: int) -> None:
    db.set_meta(_product_qty_meta_key(alias), str(qty))


def _clear_unified_qty(db: StockDB, alias: str) -> None:
    db.set_meta(_product_qty_meta_key(alias), "")


def process_product_alerts(
    cfg: dict[str, Any],
    db: StockDB,
    client: Any,
    results: list[PinPollResult],
) -> tuple[int, int]:
    """Apply deduped full alerts and qty updates for one product. Returns (alerts, qty_updates)."""
    if not results:
        return 0, 0

    alias = results[0].alias
    product_label = results[0].product_label
    transitions = [r for r in results if r.in_stock and not r.was_in_stock]
    in_stock = _in_stock_results(results)
    unified = pins_unified(results)
    alerts = 0
    qty_updates = 0

    if not in_stock:
        _clear_unified_qty(db, alias)
        cleared = db.clear_product_alert_keys(alias)
        if cleared:
            log.info(
                "all pins OUT for %s: cleared %s alert key(s) (ready for next 0→stock)",
                product_label,
                cleared,
            )
        return 0, 0

    if transitions:
        primary = transitions[0]
        pins = [r.pincode for r in transitions]
        if fire_transition_alert(
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
            alert_key=f"{alias}:batch:{','.join(sorted(pins))}",
            alert_pincodes=pins,
        ):
            alerts += 1
            for r in transitions:
                db.mark_alert(r.key)
        _set_unified_qty(db, alias, in_stock[0].qty)

    # Qty updates go to qty_update_alerts only; compare against last reported unified qty (not per-pin drift)
    if unified:
        qty = in_stock[0].qty
        prev_reported = _last_unified_qty(db, alias)
        if prev_reported is None:
            _set_unified_qty(db, alias, qty)
        elif qty != prev_reported:
            notify_stock_qty(
                cfg,
                product_label=product_label,
                qty=qty,
                prev_qty=prev_reported,
                national=True,
            )
            qty_updates += 1
            _set_unified_qty(db, alias, qty)
    else:
        for r in in_stock:
            if r.was_in_stock and r.qty != r.prev_qty:
                notify_stock_qty(
                    cfg,
                    product_label=product_label,
                    pincode=r.pincode,
                    qty=r.qty,
                    prev_qty=r.prev_qty,
                )
                qty_updates += 1

    return alerts, qty_updates
