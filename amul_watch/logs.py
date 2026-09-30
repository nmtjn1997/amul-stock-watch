"""Byte-offset log reader for the web UI, so the page can tail forward and page backward.

Offsets rather than line numbers: the daemon appends constantly, so any line-number
cursor would drift between requests. `end` is always a line boundary, which is what
makes a follow poll (`after=end`) return each line exactly once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from amul_watch.config import DATA_DIR, LOG_PATH

# One read is capped so a multi-MB log cannot blow up a single response.
MAX_READ = 512 * 1024


def log_files() -> dict[str, dict[str, Any]]:
    """Readable logs, keyed by the id the API takes as ?file=."""
    candidates = {
        "daemon": (LOG_PATH, "poller and alerts"),
        "service-err": (DATA_DIR / "service.err.log", "background service stderr / tracebacks"),
    }
    out: dict[str, dict[str, Any]] = {}
    for key, (path, label) in candidates.items():
        out[key] = {
            "id": key,
            "path": str(path),
            "label": label,
            "exists": path.is_file(),
            "size": path.stat().st_size if path.is_file() else 0,
            "tail_cmd": f"tail -100f {path}",
        }
    return out


def _resolve(name: str) -> Path:
    meta = log_files().get(str(name or "daemon"))
    if not meta:
        raise ValueError(f"unknown log {name!r}")
    return Path(meta["path"])


def read_log(
    name: str = "daemon",
    *,
    limit: int = 200,
    before: int | None = None,
    after: int | None = None,
) -> dict[str, Any]:
    """Lines plus the byte window they came from.

    after=N   → only what was appended past N (follow)
    before=N  → the `limit` lines immediately before N (page backwards)
    neither   → the last `limit` lines
    """
    path = _resolve(name)
    if not path.is_file():
        return {"file": name, "path": str(path), "lines": [], "start": 0, "end": 0,
                "size": 0, "has_older": False, "rotated": False}

    size = path.stat().st_size
    rotated = False
    # A backwards window lands on an arbitrary byte, so its first line is usually a
    # fragment. A follow cursor is a previous `end`, which is always a line boundary.
    partial_head = True
    with path.open("rb") as fh:
        if after is not None:
            cursor = int(after)
            if cursor > size:
                # File shrank: rotated or truncated. Restart from the tail.
                rotated = True
                after = None
            else:
                fh.seek(cursor)
                raw = fh.read(MAX_READ)
                start, end = cursor, cursor + len(raw)
                partial_head = False
        if after is None:
            stop = size if before is None else max(0, min(int(before), size))
            want = min(MAX_READ, stop)
            start = stop - want
            fh.seek(start)
            raw = fh.read(want)
            end = stop

    # Never hand back a half-written trailing line: leave it for the next poll.
    if raw and not raw.endswith(b"\n"):
        cut = raw.rfind(b"\n")
        if cut == -1:
            raw, end = b"", start
        else:
            end = start + cut + 1
            raw = raw[: cut + 1]

    lines = raw.decode("utf-8", errors="replace").splitlines()

    if partial_head and start > 0 and lines:
        start += len(lines.pop(0).encode("utf-8", errors="replace")) + 1

    if after is None and len(lines) > limit:
        for dropped in lines[:-limit]:
            start += len(dropped.encode("utf-8", errors="replace")) + 1
        lines = lines[-limit:]

    return {
        "file": name,
        "path": str(path),
        "lines": lines,
        "start": start,
        "end": end,
        "size": size,
        "has_older": start > 0,
        "rotated": rotated,
        "tail_cmd": f"tail -100f {path}",
    }
