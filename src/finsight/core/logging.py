"""Structured logging (structlog).

Human-readable console output in development, JSON lines in production. A ``trace_id`` bound
with :func:`bind_trace_id` is attached to every log line emitted in that context (including
from worker threads/tasks created afterwards), which is what lets a single user query be
followed across retrieval, generation and tool calls.
"""

from __future__ import annotations

import logging
import sys
import uuid

import structlog


def configure_logging(level: str = "INFO", *, json_logs: bool = False) -> None:
    """Configure stdlib logging and structlog. Safe to call more than once."""
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[*shared, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, stream=sys.stderr, format="%(message)s", force=True)


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger


def bind_trace_id(trace_id: str | None = None) -> str:
    """Bind (and return) a trace id for the current context."""
    tid = trace_id or uuid.uuid4().hex[:16]
    structlog.contextvars.bind_contextvars(trace_id=tid)
    return tid


def clear_trace_id() -> None:
    structlog.contextvars.unbind_contextvars("trace_id")
