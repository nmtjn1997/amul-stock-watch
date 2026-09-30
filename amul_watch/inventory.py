from __future__ import annotations

import csv
import os
import sys
import time
from pathlib import Path
from typing import Any

from amul_watch.config import STOCK_CSV_PATH
from amul_watch.db import StockDB
from amul_watch.pincodes import pin_short_label
from amul_watch.pincodes import is_pincode_enabled
from amul_watch.watchlist_util import watchlist_by_alias


def _row_from_stock(key: str, row: dict[str, Any], cfg: dict[str, Any]) -> dict[str, str]:
    import json

    payload = {}
    try:
        payload = json.loads(row.get("payload") or "{}")
    except json.JSONDecodeError:
        pass
    pin = str(payload.get("pincode") or key.split(":", 1)[0])
    alias = str(payload.get("alias") or (key.split(":", 1)[1] if ":" in key else ""))
    item = watchlist_by_alias(cfg).get(alias) or {}
    loc = f"{pin_short_label(cfg, pin)} {pin}"
    return {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(row["updated_at"]))),
        "pincode": pin,
        "location": loc,
        "product": str(item.get("label") or alias),
        "alias": alias,
        "in_stock": "yes" if int(row["in_stock"]) else "no",
        "qty": str(int(row["qty"])),
        "variant": str(row.get("variant_label") or ""),
        "price": f"{float(row['price']):.0f}" if row.get("price") is not None else "",
    }


def sync_inventory_csv(db: StockDB, cfg: dict[str, Any], path: Path | None = None) -> Path:
    """Rewrite inventory CSV from current DB stock_state."""
    dest = path or STOCK_CSV_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = db.list_stock_state()
    fields = ["updated_at", "pincode", "location", "product", "alias", "in_stock", "qty", "variant", "price"]
    out_rows = [_row_from_stock(r["key"], r, cfg) for r in rows]
    out_rows.sort(key=lambda r: (r["pincode"], r["product"]))
    with dest.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)
    return dest


def load_inventory_rows(
    db: StockDB,
    cfg: dict[str, Any],
    *,
    in_stock_only: bool = False,
    pincode: str | None = None,
    enabled_only: bool = True,
) -> list[dict[str, str]]:
    rows = [_row_from_stock(r["key"], r, cfg) for r in db.list_stock_state()]
    if enabled_only:
        from amul_watch.watchlist_util import pin_product_allow

        enabled_aliases = set(watchlist_by_alias(cfg).keys())

        def _keep(r: dict[str, str]) -> bool:
            if not is_pincode_enabled(cfg, r["pincode"]):
                return False
            if not r["alias"]:
                return True
            if r["alias"] not in enabled_aliases:
                return False
            allow = pin_product_allow(cfg, r["pincode"])
            return allow is None or r["alias"] in allow

        rows = [r for r in rows if _keep(r)]
    if in_stock_only:
        rows = [r for r in rows if r["in_stock"] == "yes"]
    if pincode:
        rows = [r for r in rows if r["pincode"] == str(pincode)]
    rows.sort(key=lambda r: (r["pincode"], r["product"]))
    return rows


def inventory_table_lines(
    rows: list[dict[str, str]],
    *,
    csv_path: Path | None = None,
) -> list[str]:
    """Render inventory table as lines (for live in-place redraw)."""
    if not rows:
        lines = ["No inventory rows yet: run `amul-watch poll` or start `amul-watch serve`"]
        if csv_path and csv_path.is_file():
            lines.append(f"CSV: {csv_path}")
        return lines

    # Colour the STOCK column (green IN / red OUT). Pad first, then wrap the escape
    # so alignment (and the live redraw's line lengths) are unaffected.
    use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def paint(code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if use_color else text

    headers = ("PRODUCT", "PINCODE", "LOCATION", "STOCK", "QTY", "PRICE", "UPDATED")
    widths = (14, 8, 16, 6, 5, 7, 19)
    lines = [
        f"{headers[0]:<{widths[0]}} {headers[1]:<{widths[1]}} {headers[2]:<{widths[2]}} "
        f"{headers[3]:<{widths[3]}} {headers[4]:>{widths[4]}} {headers[5]:>{widths[5]}} "
        f"{headers[6]:<{widths[6]}}",
        "-" * 88,
    ]
    for row in rows:
        price = f"₹{row['price']}" if row.get("price") else "-"
        stock = "IN" if row["in_stock"] == "yes" else "OUT"
        stock_cell = paint("32" if stock == "IN" else "31", f"{stock:<{widths[3]}}")
        loc = row["location"]
        if loc.endswith(f" {row['pincode']}"):
            loc = loc[: -len(row["pincode"]) - 1]
        lines.append(
            f"{row['product'][:widths[0]]:<{widths[0]}} "
            f"{row['pincode']:<{widths[1]}} "
            f"{loc[:widths[2]]:<{widths[2]}} "
            f"{stock_cell} "
            f"{row['qty']:>{widths[4]}} "
            f"{price:>{widths[5]}} "
            f"{row['updated_at']:<{widths[6]}}"
        )
    in_n = sum(1 for r in rows if r["in_stock"] == "yes")
    lines.append("")
    lines.append(paint("32", f"{in_n}/{len(rows)} in stock") if in_n else f"{in_n}/{len(rows)} in stock")
    if csv_path:
        lines.append(f"CSV: {csv_path}")
    return lines


def print_inventory_table(
    rows: list[dict[str, str]],
    *,
    csv_path: Path | None = None,
) -> None:
    for line in inventory_table_lines(rows, csv_path=csv_path):
        print(line)


class _LiveScreen:
    """In-place redraw via cursor-up (works in Cursor/iTerm; alt-screen often breaks after resize)."""

    def __init__(self) -> None:
        import os
        import sys

        self._stdout = sys.stdout
        term = os.environ.get("TERM", "")
        self._active = bool(self._stdout.isatty()) and term not in ("", "dumb")
        self._line_count = 0

    def enter(self) -> None:
        if not self._active:
            return
        self._stdout.write("\033[?25l")
        self._stdout.flush()

    def redraw(self, lines: list[str]) -> None:
        if not self._active:
            self._stdout.write("\n" + "=" * 80 + "\n")
            for line in lines:
                self._stdout.write(line + "\n")
            self._stdout.flush()
            return

        if self._line_count > 0:
            self._stdout.write(f"\033[{self._line_count}A")
        for line in lines:
            self._stdout.write("\033[2K\r")
            self._stdout.write(line + "\n")
        shrink = self._line_count - len(lines)
        if shrink > 0:
            for _ in range(shrink):
                self._stdout.write("\033[2K\n")
            self._stdout.write(f"\033[{shrink}A")
        self._stdout.flush()
        self._line_count = len(lines)

    def leave(self) -> None:
        if not self._active:
            return
        self._stdout.write("\033[?25h")
        self._stdout.flush()
        self._line_count = 0

    @property
    def is_tty(self) -> bool:
        return self._active


def run_inventory_live(
    cfg: dict[str, Any],
    db: StockDB,
    *,
    client: Any | None = None,
    guard: Any | None = None,
    refresh_interval: float = 1.0,
    poll_interval: float | None = None,
    poll_api: bool = False,
    in_stock_only: bool = False,
    pincode: str | None = None,
    enabled_only: bool = True,
) -> None:
    """Redraw inventory from DB every refresh_interval.

    Default (--live): reads SQLite only: no Amul API, no alerts. Daemon/poll updates DB.
    With poll_api=True (--poll): also hits Amul API on poll_interval (alerts still off).
    """
    import time

    csv_path = STOCK_CSV_PATH
    poll_every = float(poll_interval if poll_interval is not None else cfg.get("poll_interval_seconds", 45))
    screen = _LiveScreen()
    last_poll_summary: dict[str, Any] = {}
    last_poll_at = 0.0
    last_db_mtime = 0.0

    def _db_mtime() -> float:
        try:
            return db.path.stat().st_mtime
        except OSError:
            return 0.0

    def _render(*, status: str) -> None:
        rows = load_inventory_rows(
            db,
            cfg,
            in_stock_only=in_stock_only,
            pincode=pincode,
            enabled_only=enabled_only,
        )
        scope = "enabled pins/products" if enabled_only else "all pins/products"
        if poll_api:
            mode = f"DB + API poll {poll_every:g}s (no alerts)"
        else:
            mode = "DB only, no API, no alerts"
        lines = [
            "amul-watch inventory --live",
            f"refresh {refresh_interval:g}s · {mode} · {scope} · {status} · Ctrl+C to stop",
            "",
            *inventory_table_lines(rows, csv_path=csv_path if not screen.is_tty else None),
        ]
        if last_poll_summary.get("errors"):
            lines.append(f"poll errors: {last_poll_summary['errors'][:3]}")
        screen.redraw(lines)

    screen.enter()
    try:
        sync_inventory_csv(db, cfg)
        last_db_mtime = _db_mtime()
        if poll_api:
            from amul_watch.poller import run_poll

            if client is None or guard is None:
                raise RuntimeError("poll_api requires client and guard")
            _render(status="polling Amul API… (~30s)")
            last_poll_summary = run_poll(client, db, cfg, guard=guard, alerts_enabled=False)
            last_poll_at = time.time()
            sync_inventory_csv(db, cfg)
            last_db_mtime = _db_mtime()
        _render(status="watching DB" if last_db_mtime else "no data yet: start amul-watch serve or run poll")
        while True:
            time.sleep(refresh_interval)
            now = time.time()
            mtime = _db_mtime()
            if poll_api and now - last_poll_at >= poll_every:
                from amul_watch.poller import run_poll

                _render(status="polling Amul API… (~30s)")
                last_poll_summary = run_poll(client, db, cfg, guard=guard, alerts_enabled=False)
                last_poll_at = time.time()
                sync_inventory_csv(db, cfg)
                mtime = _db_mtime()
            elif mtime != last_db_mtime:
                sync_inventory_csv(db, cfg)
            last_db_mtime = mtime
            if poll_api and last_poll_at:
                status = time.strftime("last API %H:%M:%S", time.localtime(last_poll_at))
            elif mtime:
                status = time.strftime("DB updated %H:%M:%S", time.localtime(mtime))
            else:
                status = "waiting for daemon/poll"
            _render(status=status)
    except KeyboardInterrupt:
        screen.leave()
        print("\n(live inventory stopped)", flush=True)

