from __future__ import annotations

from typing import Any


def poll_priority_weights(cfg: dict[str, Any]) -> dict[str, int]:
    """Per-alias poll weight for spread scheduling (1 = normal, 2 = ~2× per pin per cycle)."""
    raw = cfg.get("poll_priority")
    if not raw:
        return {}

    weights: dict[str, int] = {}
    if not isinstance(raw, dict):
        return weights

    default = max(1, int(raw.get("weight") or raw.get("default_weight") or 2))
    aliases = raw.get("aliases")
    if aliases:
        for alias in aliases:
            weights[str(alias)] = default

    for key, value in raw.items():
        if key in ("weight", "default_weight", "aliases"):
            continue
        if isinstance(value, (int, float)):
            weights[str(key)] = max(1, int(value))

    return weights


def interleave_weighted_watchlist(
    items: list[dict[str, Any]],
    weights: dict[str, int],
) -> list[dict[str, Any]]:
    """Round-robin duplicate slots so priority products are spread, not batched at end."""
    if not weights or not items:
        return items

    remaining: list[tuple[dict[str, Any], int]] = [
        (item, max(1, weights.get(str(item.get("alias")), 1))) for item in items
    ]
    if all(count == 1 for _, count in remaining):
        return items

    out: list[dict[str, Any]] = []
    while any(count > 0 for _, count in remaining):
        for index, (item, count) in enumerate(remaining):
            if count > 0:
                out.append(item)
                remaining[index] = (item, count - 1)
    return out


def poll_plan_summary(cfg: dict[str, Any], pincodes: list[dict[str, str]], tasks: list[Any]) -> str:
    """Human-readable counts for logs."""
    from amul_watch.poller import PollTask
    from amul_watch.watchlist_util import watchlist_by_alias

    labels = watchlist_by_alias(cfg)
    per_alias: dict[str, int] = {}
    for task in tasks:
        if not isinstance(task, PollTask) or task.kind != "product" or not task.item:
            continue
        alias = str(task.item.get("alias"))
        per_alias[alias] = per_alias.get(alias, 0) + 1

    if not per_alias:
        return f"{len(pincodes)} pins"

    bits = []
    for alias, count in sorted(per_alias.items(), key=lambda kv: -kv[1]):
        item = labels.get(alias) or {}
        label = str(item.get("label") or alias.replace("-", " ")[:16])
        bits.append(f"{label}×{count}")
    return f"{len(pincodes)} pins · " + ", ".join(bits)
