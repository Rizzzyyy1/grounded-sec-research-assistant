"""Middleware: request ids, timing, structured access logs and a small rate limiter."""

from __future__ import annotations

import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from finsight.core.logging import bind_trace_id, clear_trace_id, get_logger

log = get_logger("finsight.api")
Call = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id (honouring an inbound ``X-Request-ID``), time the call and log it."""

    async def dispatch(self, request: Request, call_next: Call) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        bind_trace_id(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            log.info(
                "http.request", method=request.method, path=request.url.path, ms=round(elapsed, 1)
            )
            clear_trace_id()
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-ms"] = f"{elapsed:.1f}"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window limit per client on the expensive endpoints (LLM calls cost money)."""

    def __init__(self, app: object, *, limit: int = 30, window_s: float = 60.0,
                 prefix: str = "/v1/query", clock: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
        super().__init__(app)  # type: ignore[arg-type]
        self._limit, self._window, self._prefix, self._clock = limit, window_s, prefix, clock
        self._hits: dict[str, deque[float]] = {}

    async def dispatch(self, request: Request, call_next: Call) -> Response:
        if request.method != "POST" or not request.url.path.startswith(self._prefix):
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        now = self._clock()
        hits = self._hits.setdefault(client, deque())
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            retry_after = max(1, int(self._window - (now - hits[0])))
            return JSONResponse(
                {"error": "rate_limited", "detail": f"more than {self._limit} queries per "
                 f"{int(self._window)}s; retry in {retry_after}s"},
                status_code=429, headers={"Retry-After": str(retry_after)},
            )  # fmt: skip
        hits.append(now)
        return await call_next(request)
