"""Token-bucket rate limiter.

SEC's fair-access policy caps clients at 10 requests/second. One limiter is shared by every
EDGAR call (threads and asyncio tasks) so the cap cannot be exceeded by accident.

Implementation note: callers *reserve* a slot under a lock and then sleep outside it. The token
count is allowed to go negative, so N simultaneous callers are queued at exactly ``1/rate``
spacing instead of all waking together (the classic thundering-herd bug of "sleep then retry").
Clock and sleep functions are injectable, which makes the behaviour testable in virtual time.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable


class TokenBucket:
    def __init__(
        self,
        rate: float,
        burst: int = 1,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        asleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        if burst < 1:
            raise ValueError("burst must be >= 1")
        self._rate = rate
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._sleep = sleep
        self._asleep = asleep
        self._last = clock()
        self._lock = threading.Lock()

    @property
    def rate(self) -> float:
        return self._rate

    def _reserve(self) -> float:
        """Take one token; return how long the caller must wait before proceeding."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now
            self._tokens -= 1.0
            return 0.0 if self._tokens >= 0 else -self._tokens / self._rate

    def acquire(self) -> float:
        """Block until a request may be made. Returns seconds waited."""
        wait = self._reserve()
        if wait > 0:
            self._sleep(wait)
        return wait

    async def aacquire(self) -> float:
        """Async variant of :meth:`acquire`."""
        wait = self._reserve()
        if wait > 0:
            await self._asleep(wait)
        return wait
