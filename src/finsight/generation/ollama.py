"""A local, zero-cost model served by Ollama (https://ollama.com) - no API key required.

Every other :class:`~finsight.generation.llm.LLMClient` in this codebase (the real one and the
extractive fake) speaks one **canonical message shape**: ``messages`` is a list of
``{"role": ..., "content": ...}`` where ``content`` is either a plain string, or a list of
``{"type": "text" | "tool_use" | "tool_result", ...}`` blocks (the shape ``agent/orchestrator.py``
builds and the shape :class:`~finsight.generation.llm.AnthropicLLM` echoes back as
``LLMResult.raw_content``). :class:`OllamaLLM` is the only place that shape is translated to and
from Ollama's own chat format, so nothing upstream (the orchestrator, the tool loop, the RAG
pipeline) needs to know which provider is running.

Design choices that matter for the "zero-cost" claim:

* **No fallback.** If Ollama is unreachable or the model is not pulled, :meth:`complete` raises a
  clear :class:`GenerationError`. It never silently switches to a paid provider.
* **Cost is always $0.00.** Token counts are recorded (Ollama reports them), but no price table is
  applied - there is nothing to price.
* Connection failures are retried (the server may still be starting); a slow *generation* is not -
  resending a large prompt after a read timeout would just double the wait for no benefit.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential
from tenacity.wait import wait_base

from finsight.config.settings import LLMSettings, OllamaSettings
from finsight.core.exceptions import GenerationError
from finsight.core.logging import get_logger
from finsight.core.schemas import Usage
from finsight.generation.llm import LLMResult, ToolUse

log = get_logger(__name__)

_INSTALL_HINT = "install from https://ollama.com/download, then run `ollama serve`"


def _to_ollama_messages(system: str, messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Canonical Anthropic-shaped messages -> Ollama's chat format (see module docstring)."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    tool_names: dict[str, str] = {}  # tool_use id -> name, needed when we reach its tool_result
    for m in messages:
        role, content = m["role"], m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "".join(b["text"] for b in content if b["type"] == "text")
            calls = [b for b in content if b["type"] == "tool_use"]
            tool_names.update({b["id"]: b["name"] for b in calls})
            msg: dict[str, Any] = {"role": "assistant", "content": text}
            if calls:
                msg["tool_calls"] = [
                    {"function": {"name": b["name"], "arguments": b["input"]}} for b in calls
                ]
            out.append(msg)
            continue
        # role == "user" with block content: the tool-result turn from agent/orchestrator.py.
        for b in content:
            name = tool_names.get(b["tool_use_id"], "unknown_tool")
            out.append({"role": "tool", "tool_name": name, "content": str(b["content"])})
    return out


def _to_ollama_tools(tools: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def build_request(
    settings: OllamaSettings,
    llm_settings: LLMSettings,
    *,
    system: str,
    messages: Sequence[dict[str, Any]],
    tools: Sequence[dict[str, Any]] | None,
    model: str | None,
    max_tokens: int | None,
) -> dict[str, Any]:
    """Assemble the ``/api/chat`` request body. A pure function so its shape is unit-tested."""
    request: dict[str, Any] = {
        "model": model or settings.model,
        "messages": _to_ollama_messages(system, messages),
        "stream": False,
        "options": {
            "num_ctx": settings.context_tokens,
            "num_predict": max_tokens or llm_settings.max_tokens,
            # Evaluation must be reproducible (docs/EVALUATION.md); a local model sampling at the
            # Ollama default temperature gave a different tool call - sometimes a different tool
            # entirely - for the identical question on consecutive runs. temperature=0 + a fixed
            # seed is the standard best effort: still not bit-exact across batch sizes on every
            # backend, but far closer than leaving sampling on.
            "temperature": 0,
            "seed": 0,
        },
    }
    ollama_tools = _to_ollama_tools(tools)
    if ollama_tools:
        request["tools"] = ollama_tools
    return request


def _parse_response(data: dict[str, Any], requested_model: str) -> LLMResult:
    message = data.get("message") or {}
    text = str(message.get("content") or "")
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    tool_uses: list[ToolUse] = []
    for i, call in enumerate(message.get("tool_calls") or []):
        fn = call.get("function") or {}
        name = str(fn.get("name") or "")
        args = fn.get("arguments") or {}
        if isinstance(args, str):  # some models emit a JSON string instead of an object
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        call_id = str(call.get("id") or f"call_{i}")
        tool_uses.append(ToolUse(call_id, name, dict(args)))
        blocks.append({"type": "tool_use", "id": call_id, "name": name, "input": dict(args)})

    done_reason = str(data.get("done_reason") or "stop")
    if done_reason == "length":
        raise GenerationError(
            "the answer was truncated at the output token limit; raise llm.max_tokens, "
            "ollama.context_tokens, or shorten the context"
        )
    usage = Usage(
        input_tokens=int(data.get("prompt_eval_count") or 0),
        output_tokens=int(data.get("eval_count") or 0),
        cost_usd=0.0,  # local model: nothing to price
    )
    return LLMResult(
        text=text,
        stop_reason=done_reason,
        usage=usage,
        model=str(data.get("model") or requested_model),
        tool_uses=tuple(tool_uses),
        raw_content=blocks,
    )


class OllamaLLM:
    """Calls a local model through Ollama's HTTP API. Needs no API key."""

    def __init__(
        self,
        settings: OllamaSettings,
        llm_settings: LLMSettings | None = None,
        *,
        http: httpx.Client | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        self._settings = settings
        self._llm_settings = llm_settings or LLMSettings()
        self._owns_http = http is None
        self._http = http or httpx.Client(base_url=settings.base_url, timeout=settings.timeout_s)
        self._retryer = Retrying(
            stop=stop_after_attempt(settings.max_retries + 1),
            wait=retry_wait or wait_exponential(multiplier=0.5, min=0.5, max=5),
            # Only a transport-level failure (server not up yet) is worth retrying; a slow
            # *generation* (ReadTimeout) would just be resent and wait twice as long.
            retry=retry_if_exception_type((httpx.ConnectError, httpx.ConnectTimeout)),
            reraise=True,
        )

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> OllamaLLM:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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
            self._settings, self._llm_settings, system=system, messages=messages,
            tools=tools, model=model, max_tokens=max_tokens,
        )  # fmt: skip
        used_model = request["model"]
        try:
            response = self._retryer(self._http.post, "/api/chat", json=request)
        except httpx.ConnectError as exc:
            raise GenerationError(
                f"could not reach Ollama at {self._settings.base_url} - is it running? "
                f"({_INSTALL_HINT}, or `brew services start ollama`)"
            ) from exc
        except httpx.TimeoutException as exc:
            raise GenerationError(
                f"Ollama did not respond within {self._settings.timeout_s:.0f}s for model "
                f"{used_model!r}. A large prompt or a slow/CPU-only machine can cause this; "
                "raise FINSIGHT_OLLAMA__TIMEOUT_S or use a smaller model."
            ) from exc
        except httpx.TransportError as exc:
            raise GenerationError(f"could not reach Ollama: {exc}") from exc

        if response.status_code == 404:
            raise GenerationError(
                f"Ollama model {used_model!r} is not available. Run: ollama pull {used_model}"
            )
        if response.status_code != 200:
            detail = response.text[:300]
            raise GenerationError(f"Ollama returned HTTP {response.status_code}: {detail}")
        try:
            data = response.json()
        except ValueError as exc:
            raise GenerationError("Ollama returned a non-JSON response") from exc
        return _parse_response(data, used_model)


def describe_status(settings: OllamaSettings) -> tuple[bool, str]:
    """Best-effort reachability + model check for ``finsight doctor``. Never raises.

    ``doctor`` runs this on every invocation, including in hermetic test suites where nothing is
    listening on ``base_url`` - the broad ``except Exception`` is deliberate so a diagnostic this
    optional can never be the thing that makes ``finsight doctor`` crash.
    """
    try:
        r = httpx.get(f"{settings.base_url}/api/tags", timeout=3.0)
        r.raise_for_status()
        names = {m.get("name") for m in r.json().get("models", [])}
    except Exception:
        return False, f"not reachable at {settings.base_url} ({_INSTALL_HINT})"
    if settings.model in names:
        return True, f"reachable, {settings.model!r} pulled"
    if any(str(n).split(":")[0] == settings.model.split(":")[0] for n in names if n):
        return (
            True,
            f"reachable, but exact tag {settings.model!r} not found (have: {sorted(names)})",
        )
    return (
        False,
        f"reachable, but {settings.model!r} is not pulled (run: ollama pull {settings.model})",
    )
