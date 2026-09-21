"""Claude wrapper: request shape, cost, stop reasons, error mapping (SDK stubbed, no network)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from finsight.config.settings import LLMSettings
from finsight.core.exceptions import GenerationError
from finsight.core.schemas import Usage
from finsight.generation.llm import PRICES, AnthropicLLM, build_request, compute_cost

pytestmark = pytest.mark.unit

MSGS = [{"role": "user", "content": "hi"}]


# ------------------------------------------------------------------ request shape
def test_request_uses_adaptive_thinking_effort_and_a_cacheable_system_prompt() -> None:
    req = build_request(LLMSettings(refusal_fallback_model=None), system="SYS", messages=MSGS,
                        tools=None, model=None, max_tokens=None)  # fmt: skip
    assert req["model"] == "claude-opus-5"
    assert req["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in str(req)  # removed on current models: sending it is a 400
    assert req["output_config"] == {"effort": "high"}
    assert req["system"] == [
        {"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}
    ]
    assert req["max_tokens"] == 16000
    assert "betas" not in req and "fallbacks" not in req and "tools" not in req
    assert "temperature" not in req  # sampling params are rejected on these models


def test_overrides_tools_and_fallbacks_are_included_when_requested() -> None:
    tools = [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
    req = build_request(LLMSettings(), system="S", messages=MSGS, tools=tools,
                        model="claude-sonnet-5", max_tokens=123)  # fmt: skip
    assert (req["model"], req["max_tokens"], req["tools"]) == ("claude-sonnet-5", 123, tools)
    assert req["betas"] == ["server-side-fallback-2026-06-01"]
    assert req["fallbacks"] == [{"model": "claude-opus-4-8"}]


def test_model_roles_come_from_settings_not_call_sites() -> None:
    settings = LLMSettings(model="claude-sonnet-5", refusal_fallback_model=None)
    req = build_request(
        settings, system="S", messages=MSGS, tools=None, model=None, max_tokens=None
    )
    assert req["model"] == "claude-sonnet-5"


# ------------------------------------------------------------------ cost
def test_cost_is_token_weighted_and_cache_aware() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000,
                  cache_read_tokens=2_000_000, cache_write_tokens=1_000_000)  # fmt: skip
    # opus-5: $5 in / $25 out. 1M in + 0.1M out + 2M*0.1 cached + 1M*1.25 written
    expected = 5.0 + 2.5 + 2 * 5.0 * 0.10 + 5.0 * 1.25
    assert compute_cost("claude-opus-5", usage) == pytest.approx(expected)


def test_unknown_model_costs_zero_instead_of_crashing() -> None:
    assert compute_cost("some-future-model", Usage(input_tokens=10, output_tokens=10)) == 0.0


def test_price_table_uses_current_model_ids_without_date_suffixes() -> None:
    assert {"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"} <= set(PRICES)
    assert all(not m[-8:].isdigit() for m in PRICES)


# ------------------------------------------------------------------ AnthropicLLM with a stub SDK
def response(stop: str = "end_turn", blocks: list[Any] | None = None, **usage: int) -> Any:
    blocks = blocks if blocks is not None else [SimpleNamespace(type="text", text="Hello [S1].")]
    return SimpleNamespace(
        stop_reason=stop, model="claude-opus-5", content=blocks,
        usage=SimpleNamespace(input_tokens=usage.get("i", 100), output_tokens=usage.get("o", 50),
                              cache_read_input_tokens=usage.get("cr", 0),
                              cache_creation_input_tokens=usage.get("cw", 0)),
        stop_details=SimpleNamespace(category="cyber"),
    )  # fmt: skip


class StubAPI:
    def __init__(self, outcome: Any) -> None:
        self.outcome, self.calls = outcome, []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def llm_with(
    monkeypatch: pytest.MonkeyPatch, outcome: Any, **settings: Any
) -> tuple[AnthropicLLM, StubAPI]:
    api = StubAPI(outcome)
    stub_client = SimpleNamespace(messages=api, beta=SimpleNamespace(messages=api))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **_kw: stub_client)
    return AnthropicLLM(LLMSettings(**settings)), api


def call(llm: AnthropicLLM, **kw: Any) -> Any:
    return llm.complete(system="S", messages=MSGS, **kw)


def test_success_parses_text_usage_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, api = llm_with(monkeypatch, response(i=1_000_000, o=100_000), refusal_fallback_model=None)
    result = call(llm)
    assert result.text == "Hello [S1]."
    assert (result.stop_reason, result.model) == ("end_turn", "claude-opus-5")
    assert result.usage.input_tokens == 1_000_000
    assert result.usage.cost_usd == pytest.approx(5.0 + 2.5)
    assert "betas" not in api.calls[0]


def test_fallback_setting_routes_through_the_beta_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, api = llm_with(monkeypatch, response())  # default: fallback on
    call(llm)
    assert api.calls[0]["fallbacks"] == [{"model": "claude-opus-4-8"}]


def test_tool_use_blocks_are_extracted_and_raw_content_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocks = [SimpleNamespace(type="text", text="Let me look."),
              SimpleNamespace(type="tool_use", id="tu_1", name="get_metric", input={"ticker": "AAPL"})]  # fmt: skip
    llm, _ = llm_with(monkeypatch, response("tool_use", blocks), refusal_fallback_model=None)
    result = call(llm)
    assert result.stop_reason == "tool_use"
    assert [(t.id, t.name, t.input) for t in result.tool_uses] == [
        ("tu_1", "get_metric", {"ticker": "AAPL"})
    ]
    assert result.raw_content is blocks  # echoed back unchanged in the tool loop


def test_cache_tokens_are_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, _ = llm_with(monkeypatch, response(cr=900, cw=50), refusal_fallback_model=None)
    usage = call(llm).usage
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (900, 50)


def test_truncated_answers_are_an_error_not_a_silent_short_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm, _ = llm_with(monkeypatch, response("max_tokens"), refusal_fallback_model=None)
    with pytest.raises(GenerationError, match="truncated"):
        call(llm)


def test_refusals_surface_their_category(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, _ = llm_with(monkeypatch, response("refusal"), refusal_fallback_model=None)
    with pytest.raises(GenerationError, match="cyber"):
        call(llm)


def api_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=req), body=None)


@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (api_error(anthropic.AuthenticationError, 401), "ANTHROPIC_API_KEY"),
        (api_error(anthropic.RateLimitError, 429), "rate limit"),
        (api_error(anthropic.BadRequestError, 400), "rejected the request"),
        (api_error(anthropic.InternalServerError, 500), "API error 500"),
    ],
)
def test_sdk_errors_are_mapped_to_actionable_generation_errors(
    monkeypatch: pytest.MonkeyPatch, exc: Exception, message: str
) -> None:
    llm, _ = llm_with(monkeypatch, exc, refusal_fallback_model=None)
    with pytest.raises(GenerationError, match=message):
        call(llm)
