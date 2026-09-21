"""FastAPI application factory.

``create_app(services=...)`` accepts a prebuilt :class:`Services` (tests, embedding) - otherwise the
lifespan loads the real indexes once at startup. Domain errors are mapped to HTTP statuses in one
place, so route handlers stay free of try/except noise.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from finsight import __version__
from finsight.api.deps import Services, have_llm_credentials
from finsight.api.middleware import RateLimitMiddleware, RequestContextMiddleware
from finsight.api.routers import companies, health, query
from finsight.config.settings import Settings, get_settings
from finsight.core.exceptions import (
    ConfigError,
    EvaluationError,
    FinSightError,
    GenerationError,
    GuardrailViolation,
    IndexingError,
    IngestionError,
    RetrievalError,
)
from finsight.core.logging import configure_logging, get_logger

log = get_logger("finsight.api")

#: domain error -> (HTTP status, machine-readable code)
ERROR_MAP: dict[type[FinSightError], tuple[int, str]] = {
    ConfigError: (503, "not_configured"),
    IndexingError: (503, "index_unavailable"),
    RetrievalError: (502, "retrieval_failed"),
    GenerationError: (502, "generation_failed"),
    IngestionError: (502, "upstream_failed"),
    GuardrailViolation: (422, "guardrail"),
    EvaluationError: (400, "evaluation_error"),
}
DESCRIPTION = (
    "Grounded financial research over SEC filings. Numbers come from XBRL via deterministic tools; "
    "explanations come from retrieval, with validated citations. **Not investment advice.**"
)


def build_services(settings: Settings) -> Services:
    """Load the real indexes and stores (used by the lifespan)."""
    from finsight.agent.tools import AgentContext  # noqa: PLC0415
    from finsight.generation.llm import AnthropicLLM  # noqa: PLC0415
    from finsight.ingestion.xbrl.store import FactStore  # noqa: PLC0415
    from finsight.stack import load_stack  # noqa: PLC0415

    stack = load_stack(settings)
    facts = FactStore(settings.fact_db_path)
    ctx = AgentContext(facts=facts, retriever=stack.retriever(), universe=stack.universe)
    factory = (lambda: AnthropicLLM(settings.llm)) if have_llm_credentials() else None
    return Services(settings=settings, ctx=ctx, index_chunks=stack.manifest.n_chunks,
                    embedding_model=stack.manifest.embedding_model, llm_factory=factory)  # fmt: skip


def create_app(services: Services | None = None, *, rate_limit: int | None = None) -> FastAPI:
    settings = get_settings()
    limit = rate_limit if rate_limit is not None else settings.api_rate_limit

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, json_logs=settings.log_json)
        if services is not None:
            app.state.services = services
        else:
            try:
                app.state.services = build_services(settings)
            except FinSightError as exc:  # start anyway so /healthz works and /readyz reports why
                log.error("api.startup_degraded", error=str(exc))
        yield

    app = FastAPI(
        title="FinSight API", version=__version__, description=DESCRIPTION, lifespan=lifespan
    )
    app.add_middleware(RateLimitMiddleware, limit=limit)
    app.add_middleware(RequestContextMiddleware)
    if services is not None:  # tests use TestClient without a lifespan context manager
        app.state.services = services

    @app.exception_handler(FinSightError)
    async def domain_error(request: Request, exc: FinSightError) -> JSONResponse:
        status, code = next(
            (v for k, v in ERROR_MAP.items() if isinstance(exc, k)), (500, "internal_error")
        )
        body: dict[str, Any] = {"error": code, "detail": str(exc),
                                "request_id": getattr(request.state, "request_id", None)}  # fmt: skip
        log.warning("api.domain_error", code=code, detail=str(exc))
        return JSONResponse(body, status_code=status)

    for r in (health.router, query.router, companies.router):
        app.include_router(r)
    return app


def app_factory() -> FastAPI:  # for `uvicorn finsight.api.main:app_factory --factory`
    return create_app()
