"""EdgarClient against a mocked SEC: headers, caching, revalidation, retries, errors."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from tenacity.wait import wait_none

from finsight.config.settings import SecSettings, Settings
from finsight.core.exceptions import ConfigError, IngestionError
from finsight.ingestion.edgar.client import EdgarClient
from finsight.ingestion.edgar.rate_limit import TokenBucket

pytestmark = pytest.mark.unit

UA = "Jane Doe jane@example.com"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm"


class CountingBucket(TokenBucket):
    """Counts acquisitions without ever sleeping."""

    def __init__(self) -> None:
        super().__init__(1_000, burst=1_000)
        self.calls = 0

    def acquire(self) -> float:
        self.calls += 1
        return 0.0


def make_client(
    tmp_path: Path,
    *,
    cache: bool = True,
    max_retries: int = 3,
    sleeps: list[float] | None = None,
    limiter: TokenBucket | None = None,
) -> EdgarClient:
    settings = Settings(
        base_dir=tmp_path,
        sec=SecSettings(user_agent=UA, cache_responses=cache, max_retries=max_retries),
    )
    recorded = sleeps if sleeps is not None else []
    return EdgarClient(
        settings,
        cache_dir=tmp_path / "cache",
        limiter=limiter or CountingBucket(),
        retry_wait=wait_none(),
        sleep=recorded.append,
    )


def age_cache(cache_dir: Path, seconds: float) -> None:
    """Pretend every cached entry was fetched ``seconds`` ago."""
    for sidecar in cache_dir.rglob("*.json"):
        meta = json.loads(sidecar.read_text())
        meta["fetched_at"] -= seconds
        sidecar.write_text(json.dumps(meta))


# ---------------------------------------------------------------------------- configuration
def test_refuses_to_start_without_a_user_agent(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="User-Agent"):
        EdgarClient(Settings(base_dir=tmp_path))


@respx.mock
def test_every_request_carries_the_user_agent(tmp_path: Path) -> None:
    route = respx.get(FACTS_URL).mock(return_value=httpx.Response(200, json={"facts": {}}))
    make_client(tmp_path).get_company_facts("320193")
    assert route.calls.last.request.headers["User-Agent"] == UA


# ---------------------------------------------------------------------------- resources
@respx.mock
def test_ticker_to_cik_pads_and_is_case_insensitive_and_fetched_once(tmp_path: Path) -> None:
    rows = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
    route = respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=rows))
    client = make_client(tmp_path)
    assert client.ticker_to_cik("aapl") == "0000320193"
    assert client.ticker_to_cik("AAPL") == "0000320193"
    assert route.call_count == 1


@respx.mock
def test_unknown_ticker_raises(tmp_path: Path) -> None:
    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(IngestionError, match="unknown ticker"):
        make_client(tmp_path).ticker_to_cik("ZZZZ")


@respx.mock
def test_cik_is_padded_in_urls(tmp_path: Path) -> None:
    route = respx.get(FACTS_URL).mock(return_value=httpx.Response(200, json={"ok": 1}))
    assert make_client(tmp_path).get_company_facts("320193") == {"ok": 1}
    assert route.called


@respx.mock
def test_document_url_uses_unpadded_cik_and_dashless_accession(tmp_path: Path) -> None:
    route = respx.get(DOC_URL).mock(return_value=httpx.Response(200, content=b"<html/>"))
    body = make_client(tmp_path).get_document(
        "0000320193", "0000320193-24-000123", "aapl-20240928.htm"
    )
    assert body == b"<html/>"
    assert route.called


# ---------------------------------------------------------------------------- caching
@respx.mock
def test_fresh_cache_entry_avoids_the_network(tmp_path: Path) -> None:
    route = respx.get(FACTS_URL).mock(return_value=httpx.Response(200, json={"v": 1}))
    client = make_client(tmp_path)
    client.get_company_facts("320193")
    client.get_company_facts("320193")
    assert route.call_count == 1


@respx.mock
def test_stale_entry_is_revalidated_with_etag_and_304_reuses_body(tmp_path: Path) -> None:
    route = respx.get(FACTS_URL).mock(
        side_effect=[
            httpx.Response(200, json={"v": 1}, headers={"ETag": '"abc"'}),
            httpx.Response(304),
        ]
    )
    client = make_client(tmp_path)
    assert client.get_company_facts("320193") == {"v": 1}
    age_cache(tmp_path / "cache", 25 * 3600)  # older than the 24 h TTL
    assert client.get_company_facts("320193") == {"v": 1}
    assert route.call_count == 2
    assert route.calls.last.request.headers["If-None-Match"] == '"abc"'


@respx.mock
def test_304_restarts_the_freshness_clock(tmp_path: Path) -> None:
    route = respx.get(FACTS_URL).mock(
        side_effect=[
            httpx.Response(200, json={"v": 1}, headers={"ETag": '"a"'}),
            httpx.Response(304),
        ]
    )
    client = make_client(tmp_path)
    client.get_company_facts("320193")
    age_cache(tmp_path / "cache", 25 * 3600)
    client.get_company_facts("320193")  # revalidated
    client.get_company_facts("320193")  # now fresh again: no third request
    assert route.call_count == 2


@respx.mock
def test_stale_entry_with_changed_content_is_replaced(tmp_path: Path) -> None:
    respx.get(FACTS_URL).mock(
        side_effect=[httpx.Response(200, json={"v": 1}), httpx.Response(200, json={"v": 2})]
    )
    client = make_client(tmp_path)
    client.get_company_facts("320193")
    age_cache(tmp_path / "cache", 25 * 3600)
    assert client.get_company_facts("320193") == {"v": 2}


@respx.mock
def test_filing_documents_are_immutable_and_never_refetched(tmp_path: Path) -> None:
    route = respx.get(DOC_URL).mock(return_value=httpx.Response(200, content=b"<html/>"))
    client = make_client(tmp_path)
    for _ in range(2):
        client.get_document("320193", "0000320193-24-000123", "aapl-20240928.htm")
        age_cache(tmp_path / "cache", 10**7)
    assert route.call_count == 1


@respx.mock
def test_cache_can_be_disabled(tmp_path: Path) -> None:
    route = respx.get(FACTS_URL).mock(return_value=httpx.Response(200, json={}))
    client = make_client(tmp_path, cache=False)
    client.get_company_facts("320193")
    client.get_company_facts("320193")
    assert route.call_count == 2


# ---------------------------------------------------------------------------- retries
@respx.mock
def test_retries_429_then_503_then_succeeds_honouring_retry_after(tmp_path: Path) -> None:
    route = respx.get(FACTS_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    sleeps: list[float] = []
    assert make_client(tmp_path, sleeps=sleeps).get_company_facts("320193") == {"ok": True}
    assert route.call_count == 3
    assert sleeps[0] == 2.0  # server-directed wait wins over our own backoff
    assert len(sleeps) == 2


@respx.mock
def test_transport_errors_are_retried(tmp_path: Path) -> None:
    route = respx.get(FACTS_URL).mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={"ok": 1})]
    )
    assert make_client(tmp_path).get_company_facts("320193") == {"ok": 1}
    assert route.call_count == 2


@respx.mock
def test_gives_up_after_max_retries_with_a_clear_error(tmp_path: Path) -> None:
    route = respx.get(FACTS_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(IngestionError, match="failed after retries"):
        make_client(tmp_path, max_retries=2).get_company_facts("320193")
    assert route.call_count == 3  # first attempt + 2 retries


@respx.mock
def test_every_attempt_including_retries_takes_a_rate_limit_token(tmp_path: Path) -> None:
    respx.get(FACTS_URL).mock(
        side_effect=[httpx.Response(500), httpx.Response(500), httpx.Response(200, json={})]
    )
    bucket = CountingBucket()
    make_client(tmp_path, limiter=bucket).get_company_facts("320193")
    assert bucket.calls == 3


@respx.mock
def test_client_errors_are_not_retried(tmp_path: Path) -> None:
    route = respx.get(FACTS_URL).mock(return_value=httpx.Response(404))
    with pytest.raises(IngestionError, match="HTTP 404"):
        make_client(tmp_path).get_company_facts("320193")
    assert route.call_count == 1


@respx.mock
def test_403_explains_the_likely_cause(tmp_path: Path) -> None:
    respx.get(FACTS_URL).mock(return_value=httpx.Response(403))
    with pytest.raises(IngestionError, match="USER_AGENT"):
        make_client(tmp_path).get_company_facts("320193")


@respx.mock
def test_non_json_body_raises_ingestion_error(tmp_path: Path) -> None:
    respx.get(FACTS_URL).mock(return_value=httpx.Response(200, content=b"<html>oops</html>"))
    with pytest.raises(IngestionError, match="non-JSON"):
        make_client(tmp_path).get_company_facts("320193")


@respx.mock
def test_json_array_body_is_rejected(tmp_path: Path) -> None:
    respx.get(FACTS_URL).mock(return_value=httpx.Response(200, json=[1, 2]))
    with pytest.raises(IngestionError, match="JSON object"):
        make_client(tmp_path).get_company_facts("320193")
