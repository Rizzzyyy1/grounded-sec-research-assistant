"""ResponseCache: round-trips, freshness, revalidation bookkeeping, crash safety."""

from __future__ import annotations

from pathlib import Path

import pytest

from finsight.ingestion.edgar.cache import ResponseCache

pytestmark = pytest.mark.unit

URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"


class Clock:
    def __init__(self, t: float = 1_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_miss_returns_none(tmp_path: Path) -> None:
    assert ResponseCache(tmp_path).get(URL) is None


def test_roundtrip_preserves_body_and_validators(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path, clock=Clock(42.0))
    cache.put(URL, b'{"a": 1}', etag='"abc"', last_modified="Mon, 01 Jan 2024 00:00:00 GMT")
    entry = cache.get(URL)
    assert entry is not None
    assert entry.body == b'{"a": 1}'
    assert entry.etag == '"abc"'
    assert entry.last_modified == "Mon, 01 Jan 2024 00:00:00 GMT"
    assert entry.fetched_at == 42.0


def test_distinct_urls_do_not_collide(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    cache.put(URL, b"one")
    cache.put(URL + "?x=1", b"two")
    assert cache.get(URL).body == b"one"  # type: ignore[union-attr]
    assert cache.get(URL + "?x=1").body == b"two"  # type: ignore[union-attr]


def test_freshness_respects_ttl(tmp_path: Path) -> None:
    clock = Clock(1_000.0)
    cache = ResponseCache(tmp_path, clock=clock)
    cache.put(URL, b"x")
    entry = cache.get(URL)
    assert entry is not None
    assert cache.is_fresh(entry, ttl_s=60)
    clock.t += 61
    assert not cache.is_fresh(entry, ttl_s=60)


def test_ttl_none_means_immutable(tmp_path: Path) -> None:
    clock = Clock()
    cache = ResponseCache(tmp_path, clock=clock)
    cache.put(URL, b"x")
    entry = cache.get(URL)
    assert entry is not None
    clock.t += 10**9
    assert cache.is_fresh(entry, ttl_s=None)


def test_touch_refreshes_timestamp_without_changing_content(tmp_path: Path) -> None:
    clock = Clock(1_000.0)
    cache = ResponseCache(tmp_path, clock=clock)
    cache.put(URL, b"body", etag="e1")
    clock.t += 500
    cache.touch(URL)
    entry = cache.get(URL)
    assert entry is not None
    assert (entry.body, entry.etag, entry.fetched_at) == (b"body", "e1", 1_500.0)


def test_touch_on_missing_entry_is_a_noop(tmp_path: Path) -> None:
    ResponseCache(tmp_path).touch(URL)  # must not raise


def test_torn_entry_is_treated_as_a_miss(tmp_path: Path) -> None:
    """A crash between the two writes must never surface a half-written entry."""
    cache = ResponseCache(tmp_path)
    cache.put(URL, b"body")
    next(tmp_path.rglob("*.body")).unlink()
    assert cache.get(URL) is None


def test_corrupt_sidecar_is_treated_as_a_miss(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    cache.put(URL, b"body")
    next(tmp_path.rglob("*.json")).write_text("{not json")
    assert cache.get(URL) is None


def test_no_temp_files_left_behind(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    for i in range(5):
        cache.put(URL, f"v{i}".encode())
    assert not list(tmp_path.rglob("*.tmp*"))
