"""Token bucket behaviour, verified in virtual time so tests are instant and exact."""

from __future__ import annotations

import threading
import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finsight.ingestion.edgar.rate_limit import TokenBucket

pytestmark = pytest.mark.unit


class VirtualClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds

    async def asleep(self, seconds: float) -> None:
        self.t += seconds


def _bucket(rate: float, burst: int, clock: VirtualClock) -> TokenBucket:
    return TokenBucket(rate, burst, clock=clock.now, sleep=clock.sleep, asleep=clock.asleep)


def test_first_request_is_immediate() -> None:
    clock = VirtualClock()
    assert _bucket(8, 1, clock).acquire() == 0.0
    assert clock.t == 0.0


def test_back_to_back_requests_are_spaced_by_one_over_rate() -> None:
    clock = VirtualClock()
    bucket = _bucket(8, 1, clock)
    for _ in range(10):
        bucket.acquire()
    assert clock.t == pytest.approx(9 / 8)  # nine waits of 125 ms


def test_burst_allows_that_many_immediate_requests() -> None:
    clock = VirtualClock()
    bucket = _bucket(10, 3, clock)
    assert [bucket.acquire() for _ in range(3)] == [0.0, 0.0, 0.0]
    assert bucket.acquire() == pytest.approx(0.1)


def test_idle_time_refills_but_never_beyond_burst() -> None:
    clock = VirtualClock()
    bucket = _bucket(10, 2, clock)
    bucket.acquire()
    bucket.acquire()
    clock.t += 1_000  # a long idle period
    assert bucket.acquire() == 0.0
    assert bucket.acquire() == 0.0
    assert bucket.acquire() > 0.0  # capped at burst=2, not 10_000 tokens


@pytest.mark.parametrize(("rate", "burst"), [(0, 1), (-1, 1), (5, 0)])
def test_invalid_parameters_rejected(rate: float, burst: int) -> None:
    with pytest.raises(ValueError, match="must be"):
        TokenBucket(rate, burst)


@given(
    rate=st.floats(1, 50),
    burst=st.integers(1, 5),
    gaps=st.lists(st.floats(0, 0.5), min_size=2, max_size=40),
)
def test_never_exceeds_rate_over_any_window(rate: float, burst: int, gaps: list[float]) -> None:
    """Any n grants must span at least (n - burst) / rate seconds - the SEC cap invariant."""
    clock = VirtualClock()
    bucket = _bucket(rate, burst, clock)
    grants: list[float] = []
    for gap in gaps:
        clock.t += gap
        bucket.acquire()
        grants.append(clock.t)
    for i in range(len(grants)):
        for j in range(i + burst, len(grants)):
            n = j - i + 1
            assert grants[j] - grants[i] >= (n - burst) / rate - 1e-9


async def test_async_acquire_spaces_requests() -> None:
    clock = VirtualClock()
    bucket = _bucket(4, 1, clock)
    for _ in range(5):
        await bucket.aacquire()
    assert clock.t == pytest.approx(4 / 4)


def test_threads_share_one_budget() -> None:
    """Real clock: 5 threads x 10 calls at 500/s cannot finish faster than 49/500 s."""
    bucket = TokenBucket(500, burst=1)
    start = time.monotonic()

    def work() -> None:
        for _ in range(10):
            bucket.acquire()

    threads = [threading.Thread(target=work) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.monotonic() - start >= 49 / 500 - 0.005
