"""Liveness and readiness."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from finsight import __version__
from finsight.api.deps import Services
from finsight.api.schemas import HealthOut, ReadyOut

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthOut, summary="Liveness probe")
def healthz() -> HealthOut:
    return HealthOut(version=__version__)


@router.get("/readyz", response_model=ReadyOut, summary="Readiness: indexes and data loaded")
def readyz(request: Request, response: Response) -> ReadyOut:
    services: Services | None = getattr(request.app.state, "services", None)
    if services is None:
        response.status_code = 503
        return ReadyOut(status="degraded", index_chunks=0, embedding_model="", companies_with_facts=0,
                        llm_credentials=False, llm_provider="none: service is starting up",
                        default_mode="none")  # fmt: skip
    with services.ctx.db_lock:
        companies = len(services.ctx.facts.tickers())
    return ReadyOut(
        status="ready", index_chunks=services.index_chunks, embedding_model=services.embedding_model,
        companies_with_facts=companies, llm_credentials=services.llm_available,
        llm_provider=services.llm_provider, default_mode=services.default_mode,
    )  # fmt: skip
