"""Outbound HTTP for notifiers, through curl like the Amul client.

One transport for everything means one thing to get working behind a corporate proxy
or custom CA: whatever the local curl trusts, the whole program trusts.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any


def _curl(url: str, data: str, headers: dict[str, str], timeout: int) -> str:
    from amul_watch.client import find_curl

    cmd = [find_curl(), "-sS", "--fail-with-body", "-X", "POST", url, "--data-binary", "@-",
           "--max-time", str(timeout)]
    for key, value in headers.items():
        cmd += ["-H", f"{key}: {value}"]
    proc = subprocess.run(cmd, input=data, capture_output=True, text=True, timeout=timeout + 5)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"curl exit {proc.returncode}").strip()[:300])
    return proc.stdout


def post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None,
              timeout: int = 30) -> Any:
    hdrs = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}
    out = _curl(url, json.dumps(payload), hdrs, timeout)
    try:
        return json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        return {"raw": out[:500]}


def post_text(url: str, body: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> str:
    return _curl(url, body, dict(headers or {}), timeout)
