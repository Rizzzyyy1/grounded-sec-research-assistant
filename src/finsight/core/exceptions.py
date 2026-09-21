"""Exception hierarchy.

Every error the application raises deliberately derives from :class:`FinSightError`, so the
API layer can map them to HTTP status codes in one place and callers can catch broadly
without swallowing programming errors (``TypeError`` etc.).
"""

from __future__ import annotations


class FinSightError(Exception):
    """Base class for all expected, domain-level errors."""


class ConfigError(FinSightError):
    """Missing or invalid configuration (e.g. no SEC User-Agent)."""


class IngestionError(FinSightError):
    """Failure while downloading or persisting source data."""


class ParsingError(FinSightError):
    """A filing could not be parsed into sections / chunks."""


class IndexingError(FinSightError):
    """Index build/load problem, including embedding-model mismatches."""


class RetrievalError(FinSightError):
    """Failure while retrieving evidence."""


class GenerationError(FinSightError):
    """LLM call failed, was truncated, or was refused."""


class GuardrailViolation(FinSightError):
    """Request or output blocked by a guardrail (out of scope, unsafe, unsupported)."""


class EvaluationError(FinSightError):
    """Invalid gold data or a failed evaluation run."""
