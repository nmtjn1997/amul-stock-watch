"""History of alerts actually sent, for the web UI's Notifications tab.

Append-only JSONL at data/notifications.jsonl, one record per alert, including the
per-notifier result ("ok" or the error). Recording never raises into the alert path.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from amul_watch.config import DATA_DIR

NOTIF_PATH = DATA_DIR / "notifications.jsonl"
_LOCK = threading.Lock()


def record(entry: dict[str, Any]) -> None:
    try:
        e = dict(entry)
        e.setdefault("ts", time.time())
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(e, default=str, ensure_ascii=False) + "\n"
        with _LOCK, NOTIF_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def _read_all() -> list[dict[str, Any]]:
    if not NOTIF_PATH.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        with NOTIF_PATH.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def read(offset: int = 0, limit: int = 25) -> tuple[list[dict[str, Any]], int]:
    """Newest-first slice and total count."""
    items = _read_all()
    items.sort(key=lambda e: float(e.get("ts") or 0), reverse=True)
    offset = max(0, offset)
    return items[offset : offset + max(1, limit)], len(items)
