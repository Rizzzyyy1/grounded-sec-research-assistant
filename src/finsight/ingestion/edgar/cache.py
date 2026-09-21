"""On-disk HTTP response cache with validators.

Every EDGAR response is stored as ``<sha256(url)>.body`` plus a small ``.json`` sidecar holding
``ETag`` / ``Last-Modified`` and the fetch time. That gives us two things:

* **Freshness** - an entry younger than its TTL is served without touching the network, and an
  entry with ``ttl=None`` (filing documents, which are immutable once filed) is served forever.
* **Cheap revalidation** - a stale entry is re-checked with a conditional GET; a ``304`` costs
  one tiny request instead of re-downloading megabytes of XBRL JSON.

Writes are atomic (temp file + ``os.replace``) so an interrupted run never leaves a torn entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CacheEntry:
    url: str
    body: bytes
    etag: str | None
    last_modified: str | None
    fetched_at: float


class ResponseCache:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._root = root
        self._clock = clock

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode()).hexdigest()
        folder = self._root / key[:2]
        return folder / f"{key}.body", folder / f"{key}.json"

    def get(self, url: str) -> CacheEntry | None:
        body_path, meta_path = self._paths(url)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            body = body_path.read_bytes()
        except (FileNotFoundError, json.JSONDecodeError):
            return None  # missing or torn entry: treat as a miss
        return CacheEntry(
            url=url,
            body=body,
            etag=meta.get("etag"),
            last_modified=meta.get("last_modified"),
            fetched_at=float(meta["fetched_at"]),
        )

    def put(
        self, url: str, body: bytes, *, etag: str | None = None, last_modified: str | None = None
    ) -> None:
        body_path, meta_path = self._paths(url)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "url": url,
            "etag": etag,
            "last_modified": last_modified,
            "fetched_at": self._clock(),
        }
        self._atomic_write(body_path, body)
        self._atomic_write(meta_path, json.dumps(meta).encode())

    def touch(self, url: str) -> None:
        """Mark an entry as freshly validated (after a 304 Not Modified)."""
        entry = self.get(url)
        if entry is not None:
            self.put(url, entry.body, etag=entry.etag, last_modified=entry.last_modified)

    def is_fresh(self, entry: CacheEntry, ttl_s: float | None) -> bool:
        """``ttl_s=None`` means immutable: always fresh."""
        return True if ttl_s is None else (self._clock() - entry.fetched_at) < ttl_s

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
        tmp.write_bytes(data)
        tmp.replace(path)
