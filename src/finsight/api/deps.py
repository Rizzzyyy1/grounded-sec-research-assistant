"""Service container and dependency providers.

``Services`` holds everything the API needs and is built once at startup (heavy resources: the
indexes and embedding model). Tests build one from fakes; production builds it from settings.
Answerers are created lazily per mode and cached.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Request

from finsight.agent.orchestrator import ResearchAgent
from finsight.agent.router import ToolRouterAgent
from finsight.agent.tools import AgentContext
from finsight.api.schemas import Mode
from finsight.config.settings import Settings
from finsight.core.exceptions import ConfigError
from finsight.core.schemas import Answer
from finsight.generation.llm import LLMClient
from finsight.generation.offline import ExtractiveLLM
from finsight.generation.pipeline import RagPipeline

Answerer = Callable[[str], Answer]


def have_llm_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


@dataclass
class Services:
    settings: Settings
    ctx: AgentContext
    index_chunks: int
    embedding_model: str
    llm_factory: Callable[[], LLMClient] | None = None  # None -> no generative LLM available
    #: The provider `llm_factory` was actually built from ("claude" | "ollama"), or the reason it
    #: could not be, when `llm_factory` is None - always set, so /readyz and error messages can name
    #: the exact fix instead of a generic "add an API key" (see `api/main.py:build_services`).
    llm_provider: str = "none: FINSIGHT_LLM_PROVIDER=auto and no Claude credentials were found"
    _answerers: dict[str, Answerer] = field(default_factory=dict)

    @property
    def llm_available(self) -> bool:
        return self.llm_factory is not None

    @property
    def default_mode(self) -> str:
        return "agent" if self.llm_available else "router"

    def resolve_mode(self, mode: Mode) -> str:
        chosen = self.default_mode if mode == "auto" else mode
        if chosen in {"agent"} and not self.llm_available:
            raise ConfigError(
                f"mode 'agent' needs a configured LLM, but none is available ({self.llm_provider}). "
                "Set ANTHROPIC_API_KEY (or `ant auth login`) for Claude, or run "
                "`ollama serve` + `ollama pull <model>` and start with `finsight serve --llm ollama`."
            )
        return chosen

    def _rag(self) -> RagPipeline:
        llm: LLMClient = self.llm_factory() if self.llm_factory else ExtractiveLLM()
        return RagPipeline(
            self.ctx.retriever, llm, self.settings.llm, analyzer=self.ctx.retriever.analyzer
        )

    def answerer(self, mode: str) -> Answerer:
        if mode not in self._answerers:
            if mode == "rag":
                self._answerers[mode] = self._rag().answer
            elif mode == "router":
                # numeric questions -> tools; everything else -> extractive RAG (no API key needed)
                offline = RagPipeline(self.ctx.retriever, ExtractiveLLM(), self.settings.llm,
                                      analyzer=self.ctx.retriever.analyzer)  # fmt: skip
                self._answerers[mode] = ToolRouterAgent(self.ctx, offline.answer).answer
            elif mode == "agent":
                assert self.llm_factory is not None
                self._answerers[mode] = ResearchAgent(
                    self.llm_factory(), self.ctx, self.settings.llm
                ).answer
            else:  # pragma: no cover - guarded by the Mode literal
                raise ConfigError(f"unknown mode {mode!r}")
        return self._answerers[mode]


def get_services(request: Request) -> Services:
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise ConfigError("service is starting up or the indexes have not been built")
    return services  # type: ignore[no-any-return]
