from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class StockDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stock_state (
                key TEXT PRIMARY KEY,
                in_stock INTEGER NOT NULL,
                qty INTEGER NOT NULL,
                variant_label TEXT,
                price REAL,
                payload TEXT,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_sent (
                key TEXT PRIMARY KEY,
                sent_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS substore_cache (
                pincode TEXT PRIMARY KEY,
                substore_id TEXT NOT NULL,
                substore_name TEXT,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def get_stock(self, key: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM stock_state WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def list_stock_state(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM stock_state ORDER BY key").fetchall()
        return [dict(r) for r in rows]

    def set_stock(self, key: str, in_stock: bool, qty: int, variant_label: str, price: float | None, payload: dict) -> None:
        self._conn.execute(
            """
            INSERT INTO stock_state(key, in_stock, qty, variant_label, price, payload, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              in_stock=excluded.in_stock,
              qty=excluded.qty,
              variant_label=excluded.variant_label,
              price=excluded.price,
              payload=excluded.payload,
              updated_at=excluded.updated_at
            """,
            (key, int(in_stock), qty, variant_label, price, json.dumps(payload), time.time()),
        )
        self._conn.commit()

    def alert_sent(self, key: str) -> bool:
        return self._conn.execute("SELECT 1 FROM alert_sent WHERE key=?", (key,)).fetchone() is not None

    def mark_alert(self, key: str) -> None:
        self._conn.execute(
            "INSERT INTO alert_sent(key, sent_at) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET sent_at=excluded.sent_at",
            (key, time.time()),
        )
        self._conn.commit()

    def prune_alerts(self, keep: set[str]) -> int:
        """Forget alert rows for pincode:product pairs that are no longer polled, so a
        watch turned back on alerts on its next restock."""
        rows = [r["key"] for r in self._conn.execute("SELECT key FROM alert_sent").fetchall()]
        stale = [k for k in rows if k not in keep]
        for key in stale:
            self._conn.execute("DELETE FROM alert_sent WHERE key=?", (key,))
        if stale:
            self._conn.commit()
        return len(stale)

    def clear_alert(self, key: str) -> None:
        """Forget a prior full alert so the next 0→in-stock can notify again."""
        self._conn.execute("DELETE FROM alert_sent WHERE key=?", (key,))
        self._conn.commit()

    def get_substore(self, pincode: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM substore_cache WHERE pincode=?", (pincode,)).fetchone()
        return dict(row) if row else None

    def set_substore(self, pincode: str, substore_id: str, substore_name: str | None) -> None:
        self._conn.execute(
            """
            INSERT INTO substore_cache(pincode, substore_id, substore_name, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(pincode) DO UPDATE SET
              substore_id=excluded.substore_id,
              substore_name=excluded.substore_name,
              updated_at=excluded.updated_at
            """,
            (pincode, substore_id, substore_name, time.time()),
        )
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
