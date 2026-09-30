"""`amul-watch session show | import | reset`.

Normally there is nothing to do here: the client keeps an anonymous session in
data/cookies.txt and restarts it when it expires. `import` exists for the rare network
where Cloudflare insists on a real browser: paste a "Copy as cURL" and the cookie from
it is used instead.
"""

from __future__ import annotations

import os
import re
import sys

from amul_watch.config import COOKIE_JAR, SESSION_ENV, load_session_env
from amul_watch.cookies import extract_cookie, extract_ms_ga


def _set_env_var(key: str, value: str | None) -> None:
    lines = SESSION_ENV.read_text(encoding="utf-8").splitlines() if SESSION_ENV.is_file() else []
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    lines = [ln for ln in lines if not pattern.match(ln)]
    if value is not None:
        lines.append(f'{key}="{value}"')
    SESSION_ENV.parent.mkdir(parents=True, exist_ok=True)
    SESSION_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(SESSION_ENV, 0o600)
    except OSError:
        pass


def _read_paste() -> str:
    if sys.stdin.isatty():
        print("Paste the Copy-as-cURL text, then an empty line (or Ctrl+D):")
    lines: list[str] = []
    for line in sys.stdin:
        if not line.strip() and lines:
            break
        lines.append(line.rstrip("\r\n"))
    return "\n".join(lines).strip()


def run(action: str) -> int:
    load_session_env()
    if action == "show":
        if os.environ.get("AMUL_COOKIE"):
            print(f"mode: imported browser cookie (AMUL_COOKIE in {SESSION_ENV}, "
                  f"{len(os.environ['AMUL_COOKIE'])} chars)")
        else:
            state = "present" if COOKIE_JAR.is_file() else "not created yet (made on first poll)"
            print(f"mode: automatic anonymous session, cookie jar {COOKIE_JAR} {state}")
        return 0
    if action == "import":
        raw = _read_paste()
        if not raw:
            print("nothing pasted")
            return 1
        cookie = extract_cookie(raw)
        if len(cookie) < 10 or "=" not in cookie:
            print(f"that does not look like a cookie ({len(cookie)} chars)")
            return 1
        _set_env_var("AMUL_COOKIE", cookie)
        ms_ga = extract_ms_ga(raw)
        if ms_ga:
            _set_env_var("AMUL_MS_GA", ms_ga)
        print(f"saved to {SESSION_ENV}. Check it with: amul-watch stock")
        return 0
    if action == "reset":
        _set_env_var("AMUL_COOKIE", None)
        _set_env_var("AMUL_MS_GA", None)
        COOKIE_JAR.unlink(missing_ok=True)
        print("back to the automatic anonymous session")
        return 0
    return 1
