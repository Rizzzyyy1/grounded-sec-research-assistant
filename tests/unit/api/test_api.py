"""HTTP API: endpoints, error mapping, middleware, OpenAPI - via TestClient on the synthetic world."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from finsight import __version__
from finsight.agent.tools import AgentContext
from finsight.api.deps import Services
from finsight.api.main import create_app
from finsight.api.schemas import DISCLAIMER
from finsight.config.settings import Settings
from finsight.core.exceptions import GenerationError
from finsight.core.schemas import Usage
from finsight.generation.llm import LLMResult

pytestmark = pytest.mark.unit


def services(ctx: AgentContext, llm_factory: Any = None) -> Services:
    return Services(
        settings=Settings(),
        ctx=ctx,
        index_chunks=5,
        embedding_model="hashing-512",
        llm_factory=llm_factory,
    )


@pytest.fixture
def client(ctx: AgentContext) -> TestClient:
    return TestClient(create_app(services(ctx)), raise_server_exceptions=False)


# ------------------------------------------------------------------ health
def test_liveness_and_readiness(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok", "version": __version__}
    ready = client.get("/readyz").json()
    assert ready["status"] == "ready" and ready["index_chunks"] == 5
    assert ready["companies_with_facts"] == 3 and ready["default_mode"] == "router"
    assert ready["llm_credentials"] is False


def test_not_ready_when_indexes_are_missing_but_liveness_still_works(ctx: AgentContext) -> None:
    app = create_app(services(ctx))
    del app.state.services
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/healthz").status_code == 200
    r = c.get("/readyz")
    assert r.status_code == 503 and r.json()["status"] == "degraded"
    q = c.post("/v1/query", json={"question": "What was Apple's revenue in fiscal 2024?"})
    assert q.status_code == 503 and q.json()["error"] == "not_configured"


# ------------------------------------------------------------------ query
def test_query_router_answers_a_numeric_question_from_xbrl(client: TestClient) -> None:
    r = client.post("/v1/query", json={"question": "What was Apple's revenue in fiscal 2024?"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "router" and body["disclaimer"] == DISCLAIMER
    assert "$391,035 million" in body["answer"]["text"]
    assert body["answer"]["tool_calls"][0]["name"] == "get_financial_metric"
    assert body["answer"]["warnings"] == []


def test_query_advice_is_declined_with_the_disclaimer(client: TestClient) -> None:
    body = client.post("/v1/query", json={"question": "Should I buy Apple stock?"}).json()
    assert body["answer"]["abstained"] and body["answer"]["abstain_reason"] == "out_of_scope"


def test_query_rag_mode_runs_with_the_extractive_backend(client: TestClient) -> None:
    r = client.post(
        "/v1/query",
        json={"question": "What manufacturing risks does Apple describe?", "mode": "rag"},
    )
    assert r.status_code == 200 and r.json()["answer"]["citations"]


def test_agent_mode_without_credentials_is_a_clear_503(client: TestClient) -> None:
    r = client.post("/v1/query", json={"question": "What was Apple's revenue?", "mode": "agent"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_upstream_llm_failure_maps_to_502(ctx: AgentContext) -> None:
    class Boom:
        def complete(self, **_: Any) -> LLMResult:
            raise GenerationError("Anthropic rate limit reached")

    app = create_app(services(ctx, llm_factory=Boom))
    r = TestClient(app, raise_server_exceptions=False).post(
        "/v1/query", json={"question": "What was Apple's revenue in fiscal 2024?", "mode": "agent"}
    )
    assert r.status_code == 502 and r.json()["error"] == "generation_failed"
    assert r.json()["request_id"]


def test_auto_mode_prefers_the_agent_when_a_model_is_available(ctx: AgentContext) -> None:
    class Direct:
        def complete(self, **_: Any) -> LLMResult:
            return LLMResult(text="Apple's revenue was $391,035 million in fiscal 2024.", stop_reason="end_turn",
                             usage=Usage(cost_usd=0.01), model="claude-opus-5")  # fmt: skip

    c = TestClient(create_app(services(ctx, llm_factory=Direct)))
    body = c.post("/v1/query", json={"question": "What was Apple's revenue in fiscal 2024?"}).json()
    assert body["mode"] == "agent" and body["answer"]["model"] == "claude-opus-5"
    assert c.get("/readyz").json()["default_mode"] == "agent"


@pytest.mark.parametrize(
    "payload",
    [{"question": "hi"}, {"question": "x" * 2001}, {"question": "valid question here", "mode": "magic"},
     {"question": "valid question here", "extra": 1}, {}],
)  # fmt: skip
def test_request_validation(client: TestClient, payload: dict[str, Any]) -> None:
    assert client.post("/v1/query", json=payload).status_code == 422


def test_sse_stream_emits_ordered_events(client: TestClient) -> None:
    with client.stream(
        "POST", "/v1/query/stream", json={"question": "What was Apple's revenue in fiscal 2024?"}
    ) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        text = "".join(r.iter_text())
    events = [
        line.removeprefix("event: ").strip()
        for line in text.splitlines()
        if line.startswith("event:")
    ]
    assert events == ["start", "tool", "answer", "done"]
    assert "$391,035 million" in text


# ------------------------------------------------------------------ companies
def test_companies_list_and_known_gaps(client: TestClient) -> None:
    rows = client.get("/v1/companies").json()
    assert len(rows) == 12 and {"ticker", "name", "sector", "fiscal_year_end", "known_gaps"} <= set(
        rows[0]
    )
    assert any(r["ticker"] == "XOM" and "operating_income" in r["known_gaps"] for r in rows)


def test_financials_endpoint_returns_typed_points(client: TestClient) -> None:
    body = client.get(
        "/v1/companies/aapl/financials",
        params={"metrics": "revenue,net_income", "years": "2023-2024"},
    ).json()
    assert body["ticker"] == "AAPL" and set(body["metrics"]) == {"revenue", "net_income"}
    pts = body["metrics"]["revenue"]
    assert [p["fiscal_year"] for p in pts] == [2023, 2024] and pts[1]["value"] == 391_035e6
    assert pts[1]["xbrl_tag"] == "tag:revenue" and pts[1]["derived"] is False


def test_ratios_endpoint_computes_and_explains_gaps(client: TestClient) -> None:
    body = client.get(
        "/v1/companies/JPM/ratios", params={"names": "net_margin,gross_margin", "years": "2024"}
    ).json()
    nm = body["ratios"]["net_margin"]
    assert nm["formula"] == "net_income / revenue" and nm["points"][0]["formatted"] == "32.9%"
    gm = body["ratios"]["gross_margin"]
    assert (
        gm["points"] == [] and "gross_profit" in gm["skipped"]["2024"]
    )  # bank: not applicable, explained


@pytest.mark.parametrize(
    ("url", "status"),
    [
        ("/v1/companies/ZZZ/financials", 404),
        ("/v1/companies/AAPL/financials?metrics=vibes", 422),
        ("/v1/companies/AAPL/financials?years=abc", 422),
        ("/v1/companies/AAPL/ratios?names=magic", 422),
        ("/v1/passages/does-not-exist", 404),
    ],
)
def test_bad_requests_get_precise_status_codes(client: TestClient, url: str, status: int) -> None:
    assert client.get(url).status_code == status


def test_passage_lookup_returns_the_cited_text(client: TestClient, ctx: AgentContext) -> None:
    chunk = next(iter(ctx.retriever.catalogue.values()))
    body = client.get(f"/v1/passages/{chunk.id}").json()
    assert (
        body["text"] == chunk.text
        and body["ticker"] == chunk.metadata.ticker
        and body["source_url"]
    )


# ------------------------------------------------------------------ middleware / docs
def test_request_id_is_generated_or_honoured_and_timing_is_reported(client: TestClient) -> None:
    r = client.get("/healthz")
    assert len(r.headers["x-request-id"]) == 16 and float(r.headers["x-process-time-ms"]) >= 0
    assert (
        client.get("/healthz", headers={"X-Request-ID": "abc123"}).headers["x-request-id"]
        == "abc123"
    )


def test_rate_limit_applies_to_queries_only(ctx: AgentContext) -> None:
    c = TestClient(create_app(services(ctx), rate_limit=3))
    q = {"question": "Should I buy Apple stock?"}
    assert [c.post("/v1/query", json=q).status_code for _ in range(3)] == [200, 200, 200]
    limited = c.post("/v1/query", json=q)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1
    assert limited.json()["error"] == "rate_limited"
    assert c.get("/healthz").status_code == 200 and c.get("/v1/companies").status_code == 200


def test_openapi_schema_is_published_with_typed_responses(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "FinSight API" and spec["info"]["version"] == __version__
    assert {
        "/v1/query",
        "/v1/query/stream",
        "/v1/companies",
        "/v1/companies/{ticker}/financials",
        "/v1/companies/{ticker}/ratios",
        "/v1/passages/{chunk_id}",
        "/healthz",
        "/readyz",
    } <= set(spec["paths"])
    assert (
        "QueryResponse" in spec["components"]["schemas"]
        and "Not investment advice" in spec["info"]["description"]
    )


def test_rate_limit_is_configurable_from_settings(
    ctx: AgentContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINSIGHT_API_RATE_LIMIT", "2")
    from finsight.config.settings import get_settings  # noqa: PLC0415

    get_settings.cache_clear()
    c = TestClient(create_app(services(ctx)))
    q = {"question": "Should I buy Apple stock?"}
    assert [c.post("/v1/query", json=q).status_code for _ in range(3)] == [200, 200, 429]


# ------------------------------------------------------------------ provider selection (ADR-0011)
def test_readyz_reports_the_active_llm_provider(client: TestClient) -> None:
    assert client.get("/readyz").json()["llm_provider"].startswith("none:")


def test_explicit_agent_request_names_both_free_and_paid_fixes(client: TestClient) -> None:
    r = client.post("/v1/query", json={"question": "What was Apple's revenue?", "mode": "agent"})
    assert r.status_code == 503
    assert "ANTHROPIC_API_KEY" in r.json()["detail"] and "--llm ollama" in r.json()["detail"]


def test_resolve_llm_auto_uses_claude_only_when_credentials_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from finsight.api.main import _resolve_llm  # noqa: PLC0415

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    factory, provider = _resolve_llm(Settings(llm_provider="auto"))
    assert factory is None and provider.startswith("none:") and "Claude" in provider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    factory, provider = _resolve_llm(Settings(llm_provider="auto"))
    assert factory is not None and provider == "claude"


def test_resolve_llm_explicit_claude_without_credentials_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from finsight.api.main import _resolve_llm  # noqa: PLC0415

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    factory, provider = _resolve_llm(Settings(llm_provider="claude"))
    assert factory is None and provider == "none: llm_provider=claude but no Claude credentials " \
        "(ANTHROPIC_API_KEY / ant auth login)"  # fmt: skip


def test_resolve_llm_ollama_never_falls_back_to_claude_even_if_credentials_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explicit choice is the only one that runs - not a preference, a guarantee."""
    from finsight.api import main as api_main  # noqa: PLC0415

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")  # would satisfy "auto"/"claude" too
    monkeypatch.setattr(
        "finsight.generation.ollama.describe_status", lambda _s: (True, "reachable, pulled")
    )
    factory, provider = api_main._resolve_llm(Settings(llm_provider="ollama"))
    assert provider == "ollama (llama3.2:3b)"
    from finsight.generation.ollama import OllamaLLM  # noqa: PLC0415

    assert factory is not None
    llm = factory()
    try:
        assert isinstance(llm, OllamaLLM)
    finally:
        llm.close()


def test_resolve_llm_ollama_unreachable_is_none_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from finsight.api.main import _resolve_llm  # noqa: PLC0415

    monkeypatch.setattr(
        "finsight.generation.ollama.describe_status",
        lambda _s: (False, "not reachable at http://localhost:11434"),
    )
    factory, provider = _resolve_llm(Settings(llm_provider="ollama"))
    assert factory is None
    assert provider == "none: llm_provider=ollama but not reachable at http://localhost:11434"
