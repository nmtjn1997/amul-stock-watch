from __future__ import annotations

import random
import time


class RateLimiter:
    """Global min-gap between Amul API calls (inspired by API_Amul-Protein-Notifier)."""

    def __init__(self, *, min_interval: float = 0.2, delay_range: tuple[float, float] = (1.0, 2.0)) -> None:
        self.min_interval = min_interval
        self.delay_range = delay_range
        self._last = 0.0

    def wait(self) -> None:
        now = time.time()
        gap = random.uniform(*self.delay_range)
        wait_for = max(self.min_interval, gap) - (now - self._last)
        if wait_for > 0:
            time.sleep(wait_for)
        self._last = time.time()


def retry_delay(attempt: int, *, base: float = 2.0) -> float:
    return base**attempt + random.uniform(0, 0.5)
