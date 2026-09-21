"""Question answering: JSON and Server-Sent Events."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from finsight.api.deps import Services, get_services
from finsight.api.schemas import DISCLAIMER, QueryRequest, QueryResponse

router = APIRouter(prefix="/v1", tags=["query"])
ServicesDep = Annotated[Services, Depends(get_services)]


@router.post("/query", response_model=QueryResponse, summary="Ask a question")
def query(body: QueryRequest, services: ServicesDep) -> QueryResponse:
    """Answer with citations, a tool trace and cost/latency. Errors map to HTTP status codes
    (see the error handlers): a missing index is 503, an upstream LLM failure is 502."""
    mode = services.resolve_mode(body.mode)
    return QueryResponse(answer=services.answerer(mode)(body.question), mode=mode)


@router.post("/query/stream", summary="Ask a question; progress as Server-Sent Events")
def query_stream(body: QueryRequest, services: ServicesDep) -> EventSourceResponse:
    """Events: ``start`` -> one ``tool`` per tool call (replayed from the trace) -> ``answer`` -> ``done``.

    The answer is produced first and then streamed as events; token-level streaming of the model's
    text is not implemented (see docs/ROADMAP.md).
    """
    mode = services.resolve_mode(body.mode)

    def events() -> Iterator[dict[str, str]]:
        yield {"event": "start", "data": json.dumps({"mode": mode})}
        answer = services.answerer(mode)(body.question)
        for call in answer.tool_calls:
            yield {"event": "tool", "data": call.model_dump_json()}
        yield {"event": "answer", "data": json.dumps({"answer": json.loads(answer.model_dump_json()),
                                                       "mode": mode, "disclaimer": DISCLAIMER})}  # fmt: skip
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(events())
