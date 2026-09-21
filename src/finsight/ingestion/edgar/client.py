"""Polite SEC EDGAR HTTP client.

The only place in the codebase that talks to sec.gov. It enforces SEC's fair-access policy:

* a descriptive ``User-Agent`` with a contact email (refuses to start without one),
* a shared token-bucket limiter, acquired before *every* attempt - retries included,
* retries with exponential backoff on 429 / 5xx / transport errors, honouring ``Retry-After``,
* an on-disk cache with ETag revalidation, so re-runs are free and reproducible.

Cache policy per resource:

===================================  =====================================================
Ticker map, submissions, facts       revalidated after a TTL (they change as companies file)
Filing documents (by accession)      immutable once filed: cached forever, never revalidated
===================================  =====================================================
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

from finsight.config.settings import Settings, get_settings
from finsight.core.exceptions import IngestionError
from finsight.core.logging import get_logger
from finsight.ingestion.edgar.cache import ResponseCache
from finsight.ingestion.edgar.rate_limit import TokenBucket

log = get_logger(__name__)

WWW = "https://www.sec.gov"
DATA = "https://data.sec.gov"

_HOUR = 3600.0
_MAX_RETRY_AFTER_S = 60.0


class _Retryable(Exception):  # internal control-flow signal, never escapes the client
    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.retry_after = retry_after


def _parse_retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None  # HTTP-date form: fall back to exponential backoff


def _wait_honouring_retry_after(base: wait_base) -> Callable[[RetryCallState], float]:
    def wait(state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, _Retryable) and exc.retry_after is not None:
            return min(exc.retry_after, _MAX_RETRY_AFTER_S)
        return float(base(state))

    return wait


def pad_cik(cik: str | int) -> str:
    return f"{int(cik):010d}"


def document_url(cik: str | int, accession: str, primary_doc: str) -> str:
    """Canonical URL of a filing's primary document."""
    return f"{WWW}/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_doc}"


class EdgarClient:
    """Synchronous EDGAR client. Use as a context manager, or call :meth:`close`."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http: httpx.Client | None = None,
        cache_dir: Path | None = None,
        limiter: TokenBucket | None = None,
        retry_wait: wait_base | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        settings = settings or get_settings()
        self._ua = settings.require_sec_user_agent()  # raises ConfigError with a fix-it message
        self._limiter = limiter or TokenBucket(settings.sec.requests_per_second, burst=1)
        self._owns_http = http is None
        self._http = http or httpx.Client(timeout=settings.sec.timeout_s, follow_redirects=True)
        self._cache: ResponseCache | None = None
        if settings.sec.cache_responses:
            self._cache = ResponseCache(cache_dir or settings.raw_dir / "http_cache")
        self._retryer = Retrying(
            stop=stop_after_attempt(settings.sec.max_retries + 1),
            wait=_wait_honouring_retry_after(
                retry_wait or wait_exponential(multiplier=1, min=1, max=30)
            ),
            retry=retry_if_exception_type(_Retryable),
            sleep=sleep,
            reraise=True,
        )
        self._ticker_map: dict[str, str] | None = None

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ transport
    def _attempt(self, url: str, headers: dict[str, str]) -> httpx.Response:
        self._limiter.acquire()
        try:
            response = self._http.get(url, headers=headers)
        except httpx.TransportError as exc:
            raise _Retryable(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise _Retryable(f"HTTP {response.status_code}", _parse_retry_after(response))
        return response

    def _get(self, url: str, *, ttl_s: float | None) -> bytes:
        cache = self._cache
        entry = cache.get(url) if cache else None
        if cache and entry and cache.is_fresh(entry, ttl_s):
            log.debug("edgar.cache_hit", url=url)
            return entry.body

        headers = {"User-Agent": self._ua, "Accept-Encoding": "gzip, deflate"}
        if entry:
            if entry.etag:
                headers["If-None-Match"] = entry.etag
            if entry.last_modified:
                headers["If-Modified-Since"] = entry.last_modified

        try:
            response = self._retryer(self._attempt, url, headers)
        except _Retryable as exc:
            raise IngestionError(f"GET {url} failed after retries: {exc}") from exc

        if response.status_code == 304 and cache and entry:
            cache.touch(url)
            return entry.body
        if response.status_code == 200:
            if cache:
                cache.put(
                    url,
                    response.content,
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                )
            return response.content

        hint = (
            " (SEC rejected the request; check FINSIGHT_SEC__USER_AGENT and request rate)"
            if response.status_code == 403
            else ""
        )
        raise IngestionError(f"GET {url} -> HTTP {response.status_code}{hint}")

    def _get_json(self, url: str, *, ttl_s: float | None) -> dict[str, Any]:
        try:
            data = json.loads(self._get(url, ttl_s=ttl_s))
        except json.JSONDecodeError as exc:
            raise IngestionError(f"non-JSON response from {url}") from exc
        if not isinstance(data, dict):
            raise IngestionError(f"expected a JSON object from {url}")
        return data

    # ------------------------------------------------------------------ resources
    def ticker_to_cik(self, ticker: str) -> str:
        """Zero-padded 10-digit CIK for a ticker (from SEC's company_tickers.json)."""
        if self._ticker_map is None:
            raw = self._get_json(f"{WWW}/files/company_tickers.json", ttl_s=24 * _HOUR)
            self._ticker_map = {
                str(row["ticker"]).upper(): pad_cik(row["cik_str"]) for row in raw.values()
            }
        try:
            return self._ticker_map[ticker.upper()]
        except KeyError:
            raise IngestionError(f"unknown ticker: {ticker!r}") from None

    def get_submissions(self, cik: str) -> dict[str, Any]:
        """Company profile plus the *recent* filings table (may not reach back far enough)."""
        return self._get_json(f"{DATA}/submissions/CIK{pad_cik(cik)}.json", ttl_s=6 * _HOUR)

    def get_submissions_page(self, name: str) -> dict[str, Any]:
        """One older-filings page, named in ``filings.files`` of the submissions document."""
        return self._get_json(f"{DATA}/submissions/{name}", ttl_s=24 * _HOUR)

    def get_company_facts(self, cik: str) -> dict[str, Any]:
        """Every XBRL fact the company has ever reported."""
        return self._get_json(
            f"{DATA}/api/xbrl/companyfacts/CIK{pad_cik(cik)}.json", ttl_s=24 * _HOUR
        )

    def get_document(self, cik: str, accession: str, primary_doc: str) -> bytes:
        """A filing's primary document. Immutable once filed, so cached forever."""
        return self._get(document_url(cik, accession, primary_doc), ttl_s=None)
