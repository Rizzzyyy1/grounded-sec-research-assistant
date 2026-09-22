"""OllamaLLM: message translation, request shape, response parsing, error mapping (mocked HTTP)."""

from __future__ import annotations

import inspect
from typing import Any

import httpx
import pytest
import respx
from tenacity.wait import wait_none

from finsight.config.settings import LLMSettings, OllamaSettings
from finsight.core.exceptions import GenerationError
from finsight.generation import ollama as ollama_module
from finsight.generation.ollama import (
    OllamaLLM,
    _to_ollama_messages,
    _to_ollama_tools,
    build_request,
    describe_status,
)

pytestmark = pytest.mark.unit

BASE = "http://localhost:11434"
CHAT_URL = f"{BASE}/api/chat"
TOOLS = [{"name": "get_financial_metric", "description": "d", "input_schema": {"type": "object"}}]


def llm(**kw: Any) -> OllamaLLM:
    return OllamaLLM(OllamaSettings(base_url=BASE, **kw), retry_wait=wait_none())


def ok(**fields: object) -> httpx.Response:
    body = {
        "model": "llama3.2:3b",
        "message": {"role": "assistant", "content": "Hello [S1]."},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 100,
        "eval_count": 20,
    }
    body.update(fields)
    return httpx.Response(200, json=body)


# ------------------------------------------------------------------ message translation
def test_plain_string_turns_pass_through_with_a_system_message_prepended() -> None:
    out = _to_ollama_messages("SYS", [{"role": "user", "content": "hi"}])
    assert out == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]


def test_assistant_tool_use_blocks_become_tool_calls() -> None:
    blocks = [
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "call_1", "name": "get_financial_metric", "input": {"ticker": "AAPL"}},
    ]  # fmt: skip
    out = _to_ollama_messages("SYS", [{"role": "assistant", "content": blocks}])
    assert out[1] == {
        "role": "assistant",
        "content": "Let me check.",
        "tool_calls": [{"function": {"name": "get_financial_metric", "arguments": {"ticker": "AAPL"}}}],
    }  # fmt: skip


def test_tool_result_blocks_become_tool_role_messages_with_the_name_resolved() -> None:
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_1", "name": "get_financial_metric", "input": {}}
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": '{"revenue": 1}', "is_error": False}
        ]},
    ]  # fmt: skip
    out = _to_ollama_messages("SYS", messages)
    assert out[-1] == {
        "role": "tool",
        "tool_name": "get_financial_metric",
        "content": '{"revenue": 1}',
    }


def test_tool_result_with_unknown_id_falls_back_to_a_placeholder_name() -> None:
    messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_missing", "content": "x", "is_error": True}
        ]}
    ]  # fmt: skip
    out = _to_ollama_messages("SYS", messages)
    assert out[-1]["tool_name"] == "unknown_tool"


# ------------------------------------------------------------------ request shape
def test_build_request_uses_configured_model_context_and_max_tokens() -> None:
    req = build_request(
        OllamaSettings(model="llama3.2:3b", context_tokens=4096), LLMSettings(max_tokens=999),
        system="S", messages=[{"role": "user", "content": "hi"}], tools=None, model=None, max_tokens=None,
    )  # fmt: skip
    assert req["model"] == "llama3.2:3b"
    assert req["stream"] is False
    assert req["options"]["num_ctx"] == 4096
    assert req["options"]["num_predict"] == 999
    assert req["options"]["temperature"] == 0  # evaluation must be reproducible
    assert "tools" not in req


def test_build_request_call_site_overrides_win() -> None:
    req = build_request(
        OllamaSettings(model="llama3.2:3b"), LLMSettings(max_tokens=999),
        system="S", messages=[], tools=None, model="qwen2.5:7b", max_tokens=50,
    )  # fmt: skip
    assert req["model"] == "qwen2.5:7b"
    assert req["options"]["num_predict"] == 50


def test_tools_translate_to_openai_style_function_definitions() -> None:
    out = _to_ollama_tools(TOOLS)
    assert out == [
        {
            "type": "function",
            "function": {"name": "get_financial_metric", "description": "d", "parameters": {"type": "object"}},
        }
    ]  # fmt: skip
    assert _to_ollama_tools(None) is None
    assert _to_ollama_tools([]) is None


# ------------------------------------------------------------------ successful responses
@respx.mock
def test_text_only_response_has_zero_cost_and_no_tool_uses() -> None:
    respx.post(CHAT_URL).mock(return_value=ok())
    result = llm().complete(system="S", messages=[{"role": "user", "content": "hi"}])
    assert result.text == "Hello [S1]."
    assert result.tool_uses == ()
    assert result.usage.cost_usd == 0.0
    assert (result.usage.input_tokens, result.usage.output_tokens) == (100, 20)
    assert result.model == "llama3.2:3b"
    assert result.raw_content == [{"type": "text", "text": "Hello [S1]."}]


@respx.mock
def test_tool_call_response_is_extracted_with_object_arguments() -> None:
    respx.post(CHAT_URL).mock(return_value=ok(message={
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "call_9", "function": {"name": "get_financial_metric", "arguments": {"ticker": "AAPL"}}}],
    }))  # fmt: skip
    result = llm().complete(system="S", messages=[{"role": "user", "content": "hi"}], tools=TOOLS)
    assert [(t.id, t.name, t.input) for t in result.tool_uses] == [
        ("call_9", "get_financial_metric", {"ticker": "AAPL"})
    ]


@respx.mock
def test_tool_call_with_stringified_json_arguments_is_parsed() -> None:
    respx.post(CHAT_URL).mock(return_value=ok(message={
        "role": "assistant", "content": "",
        "tool_calls": [{"function": {"name": "get_financial_metric", "arguments": '{"ticker": "AAPL"}'}}],
    }))  # fmt: skip
    result = llm().complete(system="S", messages=[{"role": "user", "content": "hi"}])
    assert result.tool_uses[0].input == {"ticker": "AAPL"}


@respx.mock
def test_tool_call_missing_an_id_gets_a_synthesised_one() -> None:
    respx.post(CHAT_URL).mock(return_value=ok(message={
        "role": "assistant", "content": "",
        "tool_calls": [{"function": {"name": "search_filings", "arguments": {}}}],
    }))  # fmt: skip
    result = llm().complete(system="S", messages=[{"role": "user", "content": "hi"}])
    assert result.tool_uses[0].id == "call_0"


@respx.mock
def test_malformed_json_arguments_become_an_empty_dict_not_a_crash() -> None:
    respx.post(CHAT_URL).mock(return_value=ok(message={
        "role": "assistant", "content": "",
        "tool_calls": [{"function": {"name": "search_filings", "arguments": "{not json"}}],
    }))  # fmt: skip
    result = llm().complete(system="S", messages=[{"role": "user", "content": "hi"}])
    assert result.tool_uses[0].input == {}


# ------------------------------------------------------------------ truncation / token limits
@respx.mock
def test_truncated_answers_raise_instead_of_returning_partial_text() -> None:
    respx.post(CHAT_URL).mock(return_value=ok(done_reason="length"))
    with pytest.raises(GenerationError, match="truncated"):
        llm().complete(system="S", messages=[{"role": "user", "content": "hi"}])


# ------------------------------------------------------------------ error mapping
@respx.mock
def test_unreachable_server_gives_an_actionable_error_not_a_raw_exception() -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(GenerationError, match="is it running"):
        llm(max_retries=0).complete(system="S", messages=[{"role": "user", "content": "hi"}])


@respx.mock
def test_connection_errors_are_retried_before_giving_up() -> None:
    route = respx.post(CHAT_URL).mock(side_effect=[httpx.ConnectError("refused"), ok()])
    result = llm(max_retries=1).complete(system="S", messages=[{"role": "user", "content": "hi"}])
    assert result.text == "Hello [S1]."
    assert route.call_count == 2


@respx.mock
def test_timeout_gives_a_message_about_raising_the_timeout_setting() -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(GenerationError, match="did not respond within"):
        llm(max_retries=0).complete(system="S", messages=[{"role": "user", "content": "hi"}])


@respx.mock
def test_read_timeouts_are_not_retried() -> None:
    """A slow generation must not be silently resent - that would just double the wait."""
    route = respx.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(GenerationError):
        llm(max_retries=3).complete(system="S", messages=[{"role": "user", "content": "hi"}])
    assert route.call_count == 1


@respx.mock
def test_model_not_pulled_gives_the_pull_command() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(404, json={"error": "model 'x' not found"})
    )
    with pytest.raises(GenerationError, match="ollama pull"):
        llm(model="llama3.2:3b").complete(system="S", messages=[{"role": "user", "content": "hi"}])


@respx.mock
def test_server_error_is_surfaced_with_its_status_code() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(GenerationError, match="HTTP 500"):
        llm().complete(system="S", messages=[{"role": "user", "content": "hi"}])


@respx.mock
def test_non_json_body_is_a_clean_error() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(GenerationError, match="non-JSON"):
        llm().complete(system="S", messages=[{"role": "user", "content": "hi"}])


def test_never_falls_back_to_anthropic_on_failure() -> None:
    """No code path in OllamaLLM imports the Anthropic SDK: a local run can never silently bill."""
    assert "import anthropic" not in inspect.getsource(ollama_module)


# ------------------------------------------------------------------ describe_status (doctor)
@respx.mock
def test_describe_status_ok_when_the_exact_model_is_pulled() -> None:
    respx.get(f"{BASE}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "llama3.2:3b"}]})
    )
    ok_, detail = describe_status(OllamaSettings(base_url=BASE, model="llama3.2:3b"))
    assert ok_ and "pulled" in detail


@respx.mock
def test_describe_status_flags_a_close_but_different_tag() -> None:
    respx.get(f"{BASE}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "llama3.2:1b"}]})
    )
    ok_, detail = describe_status(OllamaSettings(base_url=BASE, model="llama3.2:3b"))
    assert ok_ and "exact tag" in detail


@respx.mock
def test_describe_status_not_pulled_names_the_fix() -> None:
    respx.get(f"{BASE}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})
    )
    ok_, detail = describe_status(OllamaSettings(base_url=BASE, model="llama3.2:3b"))
    assert not ok_ and "ollama pull llama3.2:3b" in detail


@respx.mock
def test_describe_status_unreachable_never_raises() -> None:
    respx.get(f"{BASE}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    ok_, detail = describe_status(OllamaSettings(base_url=BASE))
    assert not ok_ and "not reachable" in detail
