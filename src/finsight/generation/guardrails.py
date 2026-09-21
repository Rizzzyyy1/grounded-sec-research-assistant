"""Guardrails: scope, abstention and prompt-injection hygiene.

* **Scope** - requests for investment advice or price predictions are declined *before* any
  retrieval or model call (cheaper, and safer than asking a model to decline).
* **Abstention** - if retrieval finds nothing, we say so without calling the model at all.
* **Injection hygiene** - filings are a semi-trusted corpus, but the pipeline treats retrieved text
  as hostile: it is HTML-escaped when rendered (so it cannot close a ``<source>`` tag) and the
  system prompt tells the model to ignore instructions found in sources. Suspicious passages are
  logged so they can be reviewed; they are not silently dropped (a filing legitimately
  discussing "instructions" would otherwise vanish).
"""

from __future__ import annotations

import re

from finsight.core.logging import get_logger
from finsight.core.schemas import QueryAnalysis, QueryType, RetrievedChunk

log = get_logger(__name__)

_INJECTION = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above) (?:instructions|prompts?)"
    r"|disregard (?:all |any |the )?(?:previous|prior|above)"
    r"|you are now\b|new instructions:|system prompt|reveal your (?:prompt|instructions)"
    r"|act as (?:an? )?(?:different|unrestricted)",
    re.IGNORECASE,
)


def is_out_of_scope(analysis: QueryAnalysis | None) -> bool:
    return analysis is not None and analysis.query_type is QueryType.OUT_OF_SCOPE


def flag_suspicious_sources(retrieved: list[RetrievedChunk]) -> list[str]:
    """Chunk ids whose text looks like an injection attempt (logged, never trusted)."""
    flagged = [r.chunk.id for r in retrieved if _INJECTION.search(r.chunk.text)]
    for chunk_id in flagged:
        log.warning("guardrail.suspicious_source", chunk_id=chunk_id)
    return flagged
