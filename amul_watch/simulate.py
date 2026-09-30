"""Send a TEST alert through the real routing and notifiers, without touching stock state."""

from __future__ import annotations

from typing import Any

from amul_watch.config import load_config
from amul_watch.notification_routes import (
    alert_route_key,
    describe_routes,
    format_delivery_summary,
    resolve_product_alias,
    system_alert_names,
)
from amul_watch.notifiers import Alert, send
from amul_watch.pincodes import format_pin_scope
from amul_watch.stock_notify import stock_route_names
from amul_watch.watchlist_util import watchlist_by_alias


def _pick_product(cfg: dict[str, Any], product: str) -> tuple[str, str]:
    items = watchlist_by_alias(cfg)
    alias = resolve_product_alias(cfg, product) if product else next(iter(items), "")
    if not alias:
        raise ValueError("no products in the watchlist: add one first")
    if alias not in items:
        raise ValueError(f"unknown or disabled product {product!r} (known: {', '.join(items) or 'none'})")
    label = str((items.get(alias) or {}).get("label") or alias)
    return alias, label


def sim_stock(cfg: dict[str, Any], *, pincode: str, product: str = "", dry_run: bool = False) -> dict[str, str]:
    pin = str(pincode).strip()
    if not (pin.isdigit() and len(pin) == 6):
        raise ValueError(f"pincode must be six digits, got {pincode!r}")
    alias, label = _pick_product(cfg, product)
    url = f"https://shop.amul.com/en/product/{alias}"
    names = stock_route_names(cfg, alias, [pin])
    alert = Alert(
        kind="test",
        title=f"TEST Amul: {label} in stock",
        message="\n".join(
            [
                f"TEST alert (not a real restock): {label}",
                f"Where: {format_pin_scope(cfg, pin)}",
                "Qty: 12",
                url,
            ]
        ),
        url=url,
        product=label,
        alias=alias,
        pincodes=[pin],
        qty=12,
    )
    print(f"route {alert_route_key(cfg, pin, alias)}:")
    for line in format_delivery_summary(cfg, names):
        print(f"  {line}")
    print()
    if dry_run:
        print("DRY RUN: nothing sent\n")
        print(alert.message)
        return {}
    results = send(cfg, names, alert)
    from amul_watch import notifications as nf

    nf.record({**alert.as_dict(), "source": "test", "notifiers": names, "results": results})
    for name, result in results.items():
        print(f"  {name}: {result}")
    return results


def sim_system(cfg: dict[str, Any], *, dry_run: bool = False) -> dict[str, str]:
    names = system_alert_names(cfg)
    print("system_alerts:", ", ".join(names) or "(none configured)")
    if dry_run:
        return {}
    alert = Alert(kind="test", title="TEST Amul Stock Watch: system alert",
                  message="TEST: this is what a session or connectivity problem alert looks like.")
    results = send(cfg, names, alert)
    from amul_watch import notifications as nf

    nf.record({**alert.as_dict(), "source": "test", "notifiers": names, "results": results})
    for name, result in results.items():
        print(f"  {name}: {result}")
    return results


def run_simulate(kind: str, *, pincode: str = "", product: str = "", dry_run: bool = False) -> int:
    cfg = load_config()
    kind = kind.lower().strip()
    if kind == "system":
        results = sim_system(cfg, dry_run=dry_run)
        return 1 if any(r.startswith("error") for r in results.values()) else 0
    if kind == "stock":
        if not pincode:
            print("simulate stock needs --pincode")
            return 1
        try:
            results = sim_stock(cfg, pincode=pincode, product=product, dry_run=dry_run)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        return 1 if any(r.startswith("error") for r in results.values()) else 0
    if kind == "routes":
        print("\n".join(describe_routes(cfg)))
        return 0
    print(f"unknown simulate type {kind!r}: use stock | system | routes")
    return 1
