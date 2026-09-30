from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class VariantStock:
    label: str
    sku: str | None
    in_stock: bool
    qty: int
    price: float | None
    is_pack_of_30: bool


@dataclass
class ProductStock:
    alias: str
    name: str
    url: str
    variants: list[VariantStock]
    best: VariantStock | None
    pack_of_30: VariantStock | None
    any_in_stock: bool


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "in_stock", "instock"}
    return False


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_pack_of_30(text: str) -> bool:
    t = text.lower()
    return bool(re.search(r"pack[\s-]*of[\s-]*30|_30\b|\b30\b.*pack|\b30\b.*ml.*pack", t))


def _variant_from_dict(item: dict[str, Any], fallback_name: str) -> VariantStock:
    name = str(item.get("name") or item.get("title") or fallback_name)
    sku = item.get("sku")
    qty = _as_int(item.get("inventory_quantity", item.get("quantity", 0)))
    available = _as_bool(item.get("available", item.get("in_stock", qty > 0)))
    if qty <= 0 and available:
        qty = 1
    in_stock = available and qty > 0
    price = item.get("price")
    try:
        price_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        price_f = None
    label_bits = [name]
    if sku:
        label_bits.append(str(sku))
    label = " | ".join(label_bits)
    return VariantStock(
        label=label,
        sku=str(sku) if sku else None,
        in_stock=in_stock,
        qty=qty,
        price=price_f,
        is_pack_of_30=_is_pack_of_30(f"{name} {sku or ''}"),
    )


def parse_product_stock(product: dict[str, Any], *, alias: str, prefer_pack_of_30: bool = True) -> ProductStock:
    name = str(product.get("name") or alias.replace("-", " ").title())
    url = f"https://shop.amul.com/en/product/{alias}"
    variants: list[VariantStock] = []

    for var in product.get("variants") or []:
        if isinstance(var, dict):
            variants.append(_variant_from_dict(var, name))

    if not variants:
        variants.append(_variant_from_dict(product, name))

    pack = next((v for v in variants if v.is_pack_of_30), None)
    in_stock_variants = [v for v in variants if v.in_stock]

    best: VariantStock | None = None
    if prefer_pack_of_30 and pack and pack.in_stock:
        best = pack
    elif in_stock_variants:
        best = max(in_stock_variants, key=lambda v: v.qty)

    return ProductStock(
        alias=alias,
        name=name,
        url=url,
        variants=variants,
        best=best,
        pack_of_30=pack,
        any_in_stock=bool(in_stock_variants),
    )
