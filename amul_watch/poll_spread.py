from __future__ import annotations

import logging
import time
from typing import Any

from amul_watch.client import AmulClient
from amul_watch.session_guard import SessionGuard
from amul_watch.db import StockDB
from amul_watch.inventory import sync_inventory_csv
from amul_watch.pincodes import collect_pincodes
from amul_watch.poll_priority import poll_plan_summary
from amul_watch.poller import (
    build_poll_tasks,
    finalize_poll_alerts,
    poll_product_task,
    resolve_substore_task,
    session_looks_dead,
)
from amul_watch.product_poll import PinPollResult

log = logging.getLogger(__name__)


def spread_gap_seconds(cfg: dict[str, Any], task_count: int) -> float:
    """Seconds between API call start times across one poll cycle."""
    interval = float(cfg.get("poll_interval_seconds", 60))
    floor = float(cfg.get("poll_spread_min_gap_seconds", 1.0))
    if task_count <= 0:
        return interval
    return max(floor, interval / task_count)


def spread_client_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """Tighter per-request delay when spread scheduling owns the pacing."""
    return {
        **cfg,
        "request_delay_min": float(cfg.get("poll_spread_request_delay_min", 0.02)),
        "request_delay_max": float(cfg.get("poll_spread_request_delay_max", 0.08)),
    }


def run_spread_poll(
    client: AmulClient,
    db: StockDB,
    cfg: dict[str, Any],
    *,
    guard: SessionGuard | None = None,
    alerts_enabled: bool = True,
    check_session: bool = True,
) -> dict[str, Any]:
    """Run one full watchlist cycle with API calls spread evenly across poll_interval_seconds."""
    summary: dict[str, Any] = {"checks": 0, "alerts": 0, "qty_updates": 0, "errors": [], "spread": True}
    if guard and check_session and not guard.ensure_valid():
        summary["errors"].append("Amul session unavailable")
        log.warning("spread poll skipped: Amul session unavailable")
        return summary

    pincodes = collect_pincodes(client, cfg)
    tasks = build_poll_tasks(cfg, pincodes)
    if not tasks:
        return summary

    gap = spread_gap_seconds(cfg, len(tasks))
    interval = float(cfg.get("poll_interval_seconds", 60))
    log.info(
        "spread poll: %s tasks, %.1fs gap, ~%.0fs cycle (%s)",
        len(tasks),
        gap,
        interval,
        poll_plan_summary(cfg, pincodes, tasks),
    )

    by_alias: dict[str, list[PinPollResult]] = {}
    substores: dict[str, str] = {}
    cycle_start = time.monotonic()

    for index, task in enumerate(tasks):
        target = cycle_start + index * gap
        wait_for = target - time.monotonic()
        if wait_for > 0:
            time.sleep(wait_for)

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
            by_alias.setdefault(result.alias, []).append(result)
            try:
                sync_inventory_csv(db, cfg)
            except Exception as exc:
                log.warning("inventory csv sync failed: %s", exc)

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
