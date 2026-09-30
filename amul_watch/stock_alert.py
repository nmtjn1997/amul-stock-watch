from __future__ import annotations

import logging
from typing import Any

from amul_watch.client import AmulAPIError, AmulClient
from amul_watch.db import StockDB
from amul_watch.pincodes import collect_pincodes, format_pin_scope, resolve_substore_cached
from amul_watch.stock import ProductStock, parse_product_stock
from amul_watch.notifiers import Alert
from amul_watch.stock_notify import notify_stock_full, stock_route_names

log = logging.getLogger(__name__)


def _watch_item(cfg: dict[str, Any], alias: str) -> dict[str, Any] | None:
    for item in cfg.get("watchlist") or []:
        if item.get("alias") == alias:
            return item
    return None


def fetch_stock_at_pin(
    client: AmulClient,
    db: StockDB,
    cfg: dict[str, Any],
    alias: str,
    pin: str,
    pin_label: str,
    *,
    prefer_pack: bool = True,
) -> tuple[ProductStock, bool]:
    substore_id, _ = resolve_substore_cached(client, db, pin)
    raw = client.get_product(alias, substore_id)
    if not raw:
        stock = ProductStock(
            alias=alias,
            name=alias,
            url=f"https://shop.amul.com/en/product/{alias}",
            variants=[],
            best=None,
            pack_of_30=None,
            any_in_stock=False,
        )
    else:
        stock = parse_product_stock(raw, alias=alias, prefer_pack_of_30=prefer_pack)
    in_stock = bool(stock.best and stock.best.in_stock)
    return stock, in_stock


def verify_all_pincodes(
    client: AmulClient,
    db: StockDB,
    cfg: dict[str, Any],
    alias: str,
    *,
    prefer_pack: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for loc in collect_pincodes(client, cfg):
        pin = loc["pincode"]
        pin_label = loc["label"]
        try:
            stock, in_stock = fetch_stock_at_pin(
                client, db, cfg, alias, pin, pin_label, prefer_pack=prefer_pack
            )
            rows.append(
                {
                    "pincode": pin,
                    "label": pin_label,
                    "stock": stock,
                    "in_stock": in_stock,
                    "error": None,
                }
            )
        except AmulAPIError as exc:
            rows.append(
                {
                    "pincode": pin,
                    "label": pin_label,
                    "stock": None,
                    "in_stock": False,
                    "error": str(exc),
                }
            )
    return rows


def _build_multi_pin_message(cfg: dict[str, Any], product_name: str, url: str, rows: list[dict[str, Any]]) -> str:
    lines = [f"Amul IN STOCK: {product_name}", "", "Available at:"]
    for r in rows:
        if not r.get("in_stock"):
            continue
        stock: ProductStock | None = r.get("stock")
        best = stock.best if stock else None
        detail = ""
        if best:
            detail = f", qty {best.qty}"
            if best.price is not None:
                detail += f", Rs {best.price:.0f}"
        lines.append(f"  - {format_pin_scope(cfg, r['pincode'])}{detail}")
    lines += ["", url]
    return "\n".join(lines)


def _format_alert(cfg: dict[str, Any], product: ProductStock, pincode: str) -> str:
    best = product.best or product.pack_of_30 or (product.variants[0] if product.variants else None)
    place = format_pin_scope(cfg, pincode)
    if not best:
        return f"{product.name} in stock @ {place}"
    variant = best.label.split("|")[0].strip() if best.label else ""
    lines = [
        f"Amul IN STOCK: {product.name}",
        f"Where: {place}",
    ]
    if variant:
        lines.append(f"Pack: {variant}")
    if best.price is not None:
        lines.append(f"Price: Rs {best.price:.0f}")
    lines.append(f"Qty: {best.qty}")
    lines.append(product.url)
    return "\n".join(lines)


def _stock_summary_for_log(
    cfg: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    primary_pin: str | None = None,
) -> str:
    """Compact pin=qty[,₹price] list for audit logs."""
    parts: list[str] = []
    for r in rows:
        if not r.get("in_stock"):
            continue
        stock: ProductStock | None = r.get("stock")
        best = stock.best if stock else None
        pin = str(r["pincode"])
        if best:
            bit = f"{format_pin_scope(cfg, pin)} qty={best.qty}"
            if best.price is not None:
                bit += f" Rs {best.price:.0f}"
        else:
            bit = f"{format_pin_scope(cfg, pin)} in_stock"
        parts.append(bit)
    if not parts and primary_pin:
        return format_pin_scope(cfg, primary_pin)
    return "; ".join(parts) if parts else "unknown"


def fire_transition_alert(
    cfg: dict[str, Any],
    db: StockDB,
    client: AmulClient | None,
    *,
    alias: str,
    product_name: str,
    url: str,
    source: str,
    pincode: str | None = None,
    pin_label: str | None = None,
    stock: ProductStock | None = None,
    alert_pincodes: list[str] | None = None,
) -> bool:
    """Send the restock alert. True when at least one notifier accepted it (or there is
    nobody to tell), which is what lets the caller close the alert gate."""
    item = _watch_item(cfg, alias)
    label = (item or {}).get("label") or product_name
    prefer_pack = bool((item or {}).get("prefer_pack_of_30", True))

    rows: list[dict[str, Any]] = []
    if client is not None:
        try:
            rows = verify_all_pincodes(client, db, cfg, alias, prefer_pack=prefer_pack)
        except Exception as exc:
            log.warning("pincode verify failed: %s", exc)

    primary = None
    if pincode and stock:
        primary = {
            "pincode": pincode,
            "label": pin_label or pincode,
            "stock": stock,
            "in_stock": bool(stock.best and stock.best.in_stock),
        }
    if not primary:
        primary = next((r for r in rows if r.get("in_stock")), None)

    in_pins = list(alert_pincodes or [])
    if not in_pins:
        in_pins = [str(r["pincode"]) for r in rows if r.get("in_stock")]
        if pincode and pincode not in in_pins:
            in_pins.append(pincode)

    in_rows = [r for r in rows if r.get("in_stock")]
    if len(in_rows) > 1:
        message = _build_multi_pin_message(cfg, product_name, url, rows)
    elif primary and primary.get("stock") and primary.get("in_stock"):
        message = _format_alert(cfg, primary["stock"], primary["pincode"])
    else:
        message = f"Amul IN STOCK: {product_name}\n{url}"

    stock_summary = _stock_summary_for_log(cfg, rows, primary_pin=pincode)
    log.info(
        "STOCK DETECTED source=%s product=%s | %s | alert_pins=%s",
        source,
        label,
        stock_summary,
        ",".join(in_pins) if in_pins else (pincode or "-"),
    )

    best = None
    pstock = (primary or {}).get("stock")
    if pstock is not None:
        best = pstock.best
    alert = Alert(
        kind="stock",
        title=f"Amul: {label} in stock",
        message=message,
        url=url,
        product=label,
        alias=alias,
        pincodes=in_pins,
        qty=best.qty if best else None,
        price=best.price if best else None,
    )
    names = stock_route_names(cfg, alias, in_pins)
    results = notify_stock_full(cfg, alert, names=names)

    from amul_watch import notifications as nf

    nf.record(
        {
            **alert.as_dict(),
            "source": source,
            "notifiers": names,
            "results": results,
        }
    )
    return not results or any(r == "ok" for r in results.values())
