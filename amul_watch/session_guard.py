"""Keeps the Amul session usable, and says so when it cannot.

Default mode is an anonymous session in a cookie jar, which the client restarts on its
own when it expires. A human is only alerted (system_alerts) when a fresh session
cannot be started, or when a manually imported cookie has expired.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from amul_watch.client import PINCODE_API, AmulAPIError, AmulClient

log = logging.getLogger(__name__)

ALERT_COOLDOWN_SECONDS = 1800

MANUAL_COOKIE_HELP = (
    "The imported Amul cookie (AMUL_COOKIE in .env) stopped working.\n\n"
    "Either delete AMUL_COOKIE and let amul-watch manage its own session, or refresh it:\n"
    "1. Open https://shop.amul.com in a browser\n"
    "2. DevTools > Network > any /api/ request > Copy as cURL\n"
    "3. Run: amul-watch session import   (paste, then an empty line)"
)


class SessionGuard:
    def __init__(self, client: AmulClient, cfg: dict[str, Any]) -> None:
        self.client = client
        self.cfg = cfg
        self._last_alert = 0.0

    def _probe(self) -> None:
        params = {
            "limit": "1",
            "filters[0][field]": "pincode",
            "filters[0][value]": "110001",
            "filters[0][operator]": "regex",
            "cf_cache": "1h",
        }
        self.client._request("GET", PINCODE_API, referer="https://shop.amul.com/", params=params)

    def check(self, *, strict: bool = False) -> bool:
        """True when Amul answers. In the poll loop (not strict) a network blip counts as OK
        so it does not page anyone; `doctor` uses strict to surface it."""
        try:
            self._probe()
            return True
        except AmulAPIError as exc:
            if exc.status in (401, 403) or "Unauthorized" in str(exc) or "Cloudflare" in str(exc):
                log.warning("Amul session check failed (HTTP %s): %s", exc.status, exc)
                return False
            if strict:
                log.warning("Amul unreachable: %s", exc)
                return False
            log.warning("Amul session check non-auth error (HTTP %s), treating as OK: %s", exc.status, exc)
            return True

    def ensure_valid(self) -> bool:
        if self.check():
            return True
        return self.handle_dead_session()

    def handle_dead_session(self) -> bool:
        """Try to recover; alert a human only if that fails. Returns True if recovered."""
        if not self.client.cookie:
            try:
                self.client.bootstrap_session()
                if self.check():
                    return True
            except AmulAPIError as exc:
                log.warning("could not start a new Amul session: %s", exc)
            self._alert(
                "Amul Stock Watch: cannot reach Amul",
                "amul-watch could not start a session with shop.amul.com. It keeps retrying "
                "every poll. If this persists, the site may be down or blocking this network.",
            )
            return False
        self._alert("Amul Stock Watch: cookie expired", MANUAL_COOKIE_HELP)
        return False

    def _alert(self, title: str, message: str) -> None:
        now = time.time()
        if now - self._last_alert < ALERT_COOLDOWN_SECONDS:
            return
        self._last_alert = now
        from amul_watch.stock_notify import notify_system

        notify_system(self.cfg, title, message)
