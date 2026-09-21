"""Claude client wrapper (official Anthropic SDK).

What it owns, so nothing else has to:

* **Model per role** - ids come from ``Settings.llm``; nothing is hard-coded at call sites.
* **Adaptive thinking + effort** - ``thinking={"type": "adaptive"}`` and ``output_config.effort``
  (the current API; ``budget_tokens`` is removed on these models).
* **Prompt caching** - the stable system prompt carries a ``cache_control`` breakpoint, so the
  fixed prefix is billed at the cache-read rate on repeat calls (``cache_read_tokens`` is
  recorded so this is verifiable, not assumed).
* **Stop reasons** - ``max_tokens`` (a truncated answer) and ``refusal`` are surfaced as errors;
  they are never returned as if they were normal answers.
* **Refusal fallback** - optionally routes a declined request to a fallback model server-side.
* **Cost accounting** - tokens x price, cache-aware, for evaluation reports and the API.

A tiny :class:`LLMClient` protocol lets tests and the offline mode substitute fakes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from finsight.config.settings import LLMSettings
from finsight.core.exceptions import GenerationError
from finsight.core.logging import get_logger
from finsight.core.schemas import Usage

log = get_logger(__name__)

#: USD per million tokens (input, output). Cache reads bill at 0.1x input, cache writes 1.25x.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
_CACHE_READ_FACTOR = 0.10
_CACHE_WRITE_FACTOR = 1.25


def compute_cost(model: str, usage: Usage) -> float:
    if model not in PRICES:
        log.warning("llm.unknown_model_price", model=model)
        return 0.0
    price_in, price_out = PRICES[model]
    return (
        usage.input_tokens * price_in
        + usage.cache_read_tokens * price_in * _CACHE_READ_FACTOR
        + usage.cache_write_tokens * price_in * _CACHE_WRITE_FACTOR
        + usage.output_tokens * price_out
    ) / 1_000_000


@dataclass(frozen=True)
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class LLMResult:
    text: str
    stop_reason: str
    usage: Usage
    model: str
    tool_uses: tuple[ToolUse, ...] = ()
    #: The assistant content exactly as returned; the tool loop must echo it back unchanged.
    raw_content: Any = field(default=None, repr=False)


class LLMClient(Protocol):
    def complete(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult: ...


def build_request(
    settings: LLMSettings,
    *,
    system: str,
    messages: Sequence[dict[str, Any]],
    tools: Sequence[dict[str, Any]] | None,
    model: str | None,
    max_tokens: int | None,
) -> dict[str, Any]:
    """Assemble the Messages API request. A pure function so its shape is unit-tested."""
    request: dict[str, Any] = {
        "model": model or settings.model,
        "max_tokens": max_tokens or settings.max_tokens,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": settings.effort},
        # The system prompt is the stable prefix: mark it cacheable.
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": list(messages),
    }
    if tools:
        request["tools"] = list(tools)
    if settings.refusal_fallback_model:
        request["betas"] = ["server-side-fallback-2026-06-01"]
        request["fallbacks"] = [{"model": settings.refusal_fallback_model}]
    return request


def _usage_from(response_usage: Any, model: str) -> Usage:
    base = Usage(
        input_tokens=int(getattr(response_usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(response_usage, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(response_usage, "cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(response_usage, "cache_creation_input_tokens", 0) or 0),
    )
    return base.model_copy(update={"cost_usd": compute_cost(model, base)})


class AnthropicLLM:
    """Real Claude calls. Credentials come from the SDK's chain (env var or ``ant auth login``)."""

    def __init__(self, settings: LLMSettings) -> None:
        import anthropic  # noqa: PLC0415

        self._anthropic = anthropic
        self._settings = settings
        self._client = anthropic.Anthropic(
            timeout=settings.timeout_s, max_retries=settings.max_retries
        )

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        request = build_request(
            self._settings, system=system, messages=messages, tools=tools, model=model,
            max_tokens=max_tokens,
        )  # fmt: skip
        api = self._client.beta.messages if "betas" in request else self._client.messages
        a = self._anthropic
        try:
            response = api.create(**request)
        except a.AuthenticationError as exc:
            raise GenerationError(
                "Anthropic authentication failed: set ANTHROPIC_API_KEY or run `ant auth login`"
            ) from exc
        except a.RateLimitError as exc:
            raise GenerationError("Anthropic rate limit reached (retries exhausted)") from exc
        except a.BadRequestError as exc:
            raise GenerationError(f"Anthropic rejected the request: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise GenerationError("could not reach the Anthropic API") from exc
        except a.APIStatusError as exc:
            raise GenerationError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc

        stop = str(response.stop_reason)
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            raise GenerationError(f"the model declined this request (category: {category})")
        if stop == "max_tokens":
            raise GenerationError(
                "the answer was truncated at max_tokens; raise llm.max_tokens or "
                "shorten the context"
            )

        used_model = str(response.model)
        text = "".join(b.text for b in response.content if b.type == "text")
        uses = tuple(
            ToolUse(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"
        )
        return LLMResult(
            text=text,
            stop_reason=stop,
            usage=_usage_from(response.usage, used_model),
            model=used_model,
            tool_uses=uses,
            raw_content=response.content,
        )
