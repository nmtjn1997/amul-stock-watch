from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from amul_watch.client import AmulAPIError, AmulClient
from amul_watch.session_guard import SessionGuard
from amul_watch.db import StockDB
from amul_watch.inventory import sync_inventory_csv
from amul_watch.pincodes import collect_pincodes, resolve_substore_cached
from amul_watch.poll_priority import interleave_weighted_watchlist, poll_priority_weights
from amul_watch.product_poll import PinPollResult, process_product_alerts
from amul_watch.stock import ProductStock, parse_product_stock
from amul_watch.watchlist_util import ordered_watchlist

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PollTask:
    kind: Literal["substore", "product"]
    pincode: str
    pin_label: str
    item: dict[str, Any] | None = None


def build_poll_tasks(cfg: dict[str, Any], pincodes: list[dict[str, str]]) -> list[PollTask]:
    """Flatten pin × product work for spread scheduling (rebuilt each cycle)."""
    weights = poll_priority_weights(cfg)
    tasks: list[PollTask] = []
    for loc in pincodes:
        pin = loc["pincode"]
        pin_label = loc["label"]
        tasks.append(PollTask(kind="substore", pincode=pin, pin_label=pin_label))
        items = interleave_weighted_watchlist(ordered_watchlist(cfg, pin), weights)
        for item in items:
            tasks.append(PollTask(kind="product", pincode=pin, pin_label=pin_label, item=item))
    return tasks


def resolve_substore_task(
    client: AmulClient,
    db: StockDB,
    task: PollTask,
    substores: dict[str, str],
) -> str | None:
    try:
        substore_id, _ = resolve_substore_cached(client, db, task.pincode)
        substores[task.pincode] = substore_id
        return None
    except AmulAPIError as exc:
        return f"{task.pincode}: substore: {exc}"


def poll_product_task(
    client: AmulClient,
    db: StockDB,
    task: PollTask,
    substore_id: str,
    *,
    guard: SessionGuard | None = None,
) -> tuple[PinPollResult | None, str | None]:
    if not task.item:
        return None, None
    item = task.item
    alias = str(item["alias"])
    pin = task.pincode
    prefer_pack = bool(item.get("prefer_pack_of_30", True))
    key_base = f"{pin}:{alias}"
    try:
        raw = client.get_product(alias, substore_id)
    except AmulAPIError as exc:
        if exc.status in (401, 403) and guard:
            guard.handle_dead_session()
        prev = db.get_stock(key_base)
        if prev and prev.get("in_stock"):
            log.warning(
                "STOCK STATE STALE %s @ %s: poll failed, DB still in_stock=1 (qty %s): %s",
                alias,
                pin,
                prev.get("qty"),
                exc,
            )
        return None, f"{pin}/{alias}: {exc}"

    if not raw:
        # Empty body: a genuinely OOS product still returns a body (available=0 with the
        # real name). An empty body means the session is almost certainly dead (the pincode
        # cookie probe can still pass). Do NOT overwrite DB state to OOS or reset alert gates;
        # flag had_data=False so run_poll can fire a cookie-refresh alert instead of trusting it.
        prev = db.get_stock(key_base)
        return (
            PinPollResult(
                pincode=pin,
                pin_label=task.pin_label,
                alias=alias,
                product_label=item.get("label") or alias,
                stock=ProductStock(
                    alias=alias,
                    name=alias,
                    url=f"https://shop.amul.com/en/product/{alias}",
                    variants=[],
                    best=None,
                    pack_of_30=None,
                    any_in_stock=False,
                ),
                in_stock=False,
                qty=0,
                variant_label="",
                was_in_stock=bool(prev and prev["in_stock"]),
                prev_qty=int(prev["qty"]) if prev else 0,
                key=key_base,
                had_data=False,
            ),
            None,
        )

    stock = parse_product_stock(raw, alias=alias, prefer_pack_of_30=prefer_pack)

    prev = db.get_stock(key_base)
    in_stock = bool(stock.best and stock.best.in_stock)
    qty = stock.best.qty if stock.best else 0
    variant_label = stock.best.label if stock.best else ""
    price = stock.best.price if stock.best else None
    db.set_stock(key_base, in_stock, qty, variant_label, price, {"pincode": pin, "alias": alias})

    was_in_stock = bool(prev and prev["in_stock"])
    prev_qty = int(prev["qty"]) if prev else 0
    product_label = item.get("label") or stock.name or alias

    if was_in_stock and not in_stock:
        log.info(
            "STOCK OUT %s @ %s (qty was %s), reset alert gate for next restock",
            product_label,
            pin,
            prev_qty,
        )
        db.clear_alert(key_base)
    elif in_stock and not was_in_stock:
        log.info("STOCK IN %s @ %s qty=%s", product_label, pin, qty)

    return (
        PinPollResult(
            pincode=pin,
            pin_label=task.pin_label,
            alias=alias,
            product_label=product_label,
            stock=stock,
            in_stock=in_stock,
            qty=qty,
            variant_label=variant_label,
            was_in_stock=was_in_stock,
            prev_qty=prev_qty,
            key=key_base,
        ),
        None,
    )


def finalize_poll_alerts(
    cfg: dict[str, Any],
    db: StockDB,
    client: AmulClient,
    by_alias: dict[str, list[PinPollResult]],
    *,
    alerts_enabled: bool,
) -> dict[str, int]:
    summary = {"alerts": 0, "qty_updates": 0}
    for alias, results in by_alias.items():
        if alerts_enabled:
            alerts, qty_updates = process_product_alerts(cfg, db, client, results)
            summary["alerts"] += alerts
            summary["qty_updates"] += qty_updates
            if alerts:
                log.info("FULL ALERT product=%s count=%s", alias, alerts)
            if qty_updates:
                log.info("qty updates product=%s count=%s", alias, qty_updates)
    return summary


def session_looks_dead(
    by_alias: dict[str, list[PinPollResult]],
    summary: dict[str, Any],
    guard: SessionGuard | None,
) -> bool:
    """A real out-of-stock product still returns its record. If EVERY product came back
    empty the session is dead, and trusting it would read as "all out of stock", reset
    every alert gate and cause false restock alerts on the next good cycle."""
    all_results = [r for rs in by_alias.values() for r in rs]
    if not all_results or any(r.had_data for r in all_results):
        return False
    summary["session_expired"] = True
    summary["errors"].append(f"session likely expired: all {len(all_results)} products returned empty")
    log.warning("SESSION DEAD? all %d product checks returned empty bodies; restarting the session", len(all_results))
    if guard:
        guard.handle_dead_session()
    return True


def run_poll(
    client: AmulClient,
    db: StockDB,
    cfg: dict[str, Any],
    *,
    guard: SessionGuard | None = None,
    alerts_enabled: bool = True,
    check_session: bool = True,
) -> dict[str, Any]:
    """Run full poll immediately (burst). Used by CLI `poll` / `check`."""
    summary: dict[str, Any] = {"checks": 0, "alerts": 0, "qty_updates": 0, "errors": []}
    if guard and check_session and not guard.ensure_valid():
        summary["errors"].append("Amul session unavailable")
        log.warning("poll skipped: Amul session unavailable")
        return summary

    pincodes = collect_pincodes(client, cfg)
    tasks = build_poll_tasks(cfg, pincodes)
    by_alias: dict[str, list[PinPollResult]] = defaultdict(list)
    substores: dict[str, str] = {}

    for task in tasks:
        if task.kind == "substore":
            err = resolve_substore_task(client, db, task, substores)
            if err:
                summary["errors"].append(err)
            continue
        if task.pincode not in substores:
            continue
        summary["checks"] += 1
        result, err = poll_product_task(client, db, task, substores[task.pincode], guard=guard)
        if err:
            summary["errors"].append(err)
            continue
        if result:
            by_alias[result.alias].append(result)

    if session_looks_dead(by_alias, summary, guard):
        return summary

    alert_summary = finalize_poll_alerts(cfg, db, client, by_alias, alerts_enabled=alerts_enabled)
    summary["alerts"] = alert_summary["alerts"]
    summary["qty_updates"] = alert_summary["qty_updates"]

    try:
        sync_inventory_csv(db, cfg)
    except Exception as exc:
        log.warning("inventory csv sync failed: %s", exc)

    return summary


def run_stock_report(client: AmulClient, db: StockDB, cfg: dict[str, Any], *, guard: SessionGuard | None = None) -> list[dict[str, Any]]:
    """Fetch current availability for all watchlist × pincodes (no alerts)."""
    rows: list[dict[str, Any]] = []
    if guard and not guard.ensure_valid():
        return rows

    for loc in collect_pincodes(client, cfg):
        pin = loc["pincode"]
        pin_label = loc["label"]
        try:
            substore_id, _ = resolve_substore_cached(client, db, pin)
        except AmulAPIError as exc:
            for item in ordered_watchlist(cfg, pin):
                rows.append({
                    "product": item.get("label") or item["alias"],
                    "pincode": pin,
                    "location": pin_label,
                    "status": "ERROR",
                    "detail": str(exc),
                    "qty": 0,
                    "price": None,
                })
            continue

        for item in ordered_watchlist(cfg, pin):
            alias = item["alias"]
            prefer_pack = bool(item.get("prefer_pack_of_30", True))
            name = item.get("label") or alias.replace("-", " ").title()
            try:
                raw = client.get_product(alias, substore_id)
            except AmulAPIError as exc:
                rows.append({
                    "product": name,
                    "pincode": pin,
                    "location": pin_label,
                    "status": "ERROR",
                    "detail": str(exc),
                    "qty": 0,
                    "price": None,
                })
                if exc.status in (401, 403) and guard:
                    guard.handle_dead_session()
                continue

            if not raw:
                stock = ProductStock(
                    alias=alias,
                    name=name,
                    url=f"https://shop.amul.com/en/product/{alias}",
                    variants=[],
                    best=None,
                    pack_of_30=None,
                    any_in_stock=False,
                )
            else:
                stock = parse_product_stock(raw, alias=alias, prefer_pack_of_30=prefer_pack)

            best = stock.best
            in_stock = bool(best and best.in_stock)
            rows.append({
                "product": name,
                "pincode": pin,
                "location": pin_label,
                "status": "IN STOCK" if in_stock else "OUT OF STOCK",
                "variant": best.label if best else "",
                "qty": best.qty if best else 0,
                "price": best.price if best else None,
                "url": stock.url,
            })
    return rows


def print_stock_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No results: enable a pincode and a product first (amul-watch ui)")
        return

    # Colour is decided from the real stdout, so a pipe/redirect stays clean.
    # NO_COLOR disables it. Pad the status first, then wrap the colour, so the
    # escape bytes never throw off column alignment.
    use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def paint(code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if use_color else text

    headers = ("PRODUCT", "PINCODE", "LOCATION", "STATUS", "QTY", "PRICE")
    widths = (18, 8, 22, 14, 5, 8)
    print(f"{headers[0]:<{widths[0]}} {headers[1]:<{widths[1]}} {headers[2]:<{widths[2]}} {headers[3]:<{widths[3]}} {headers[4]:>{widths[4]}} {headers[5]:>{widths[5]}}")
    print("-" * 80)
    for row in rows:
        price = f"₹{row['price']:.0f}" if row.get("price") else "-"
        status_padded = f"{row['status']:<{widths[3]}}"
        if row["status"] == "IN STOCK":
            status_cell = paint("32", status_padded)       # green
        elif row["status"] == "ERROR":
            status_cell = paint("1;31", status_padded)     # bold red
        else:
            status_cell = paint("31", status_padded)       # red (out of stock)
        print(
            f"{row['product'][:widths[0]]:<{widths[0]}} "
            f"{row['pincode']:<{widths[1]}} "
            f"{row['location'][:widths[2]]:<{widths[2]}} "
            f"{status_cell} "
            f"{row.get('qty', 0):>{widths[4]}} "
            f"{price:>{widths[5]}}"
        )
        if row.get("variant") and row["status"] == "IN STOCK":
            print(f"{'':<{widths[0]}} {'':<{widths[1]}} {row['variant'][:widths[2]]}")
    in_stock = sum(1 for r in rows if r["status"] == "IN STOCK")
    print()
    summary = f"{in_stock}/{len(rows)} in stock"
    print(paint("32", summary) if in_stock else paint("2", summary))
