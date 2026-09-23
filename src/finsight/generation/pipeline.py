"""Single-shot RAG pipeline - the standing baseline every other system is measured against.

    question -> scope check -> retrieve -> (no evidence? abstain) -> context -> Claude
             -> abstention check -> citation validation -> numeric consistency check -> Answer

Two cheap exits never touch the model: out-of-scope requests (advice) and empty retrieval. The
result is always an :class:`Answer`, so the evaluation harness scores this pipeline and the
agent (Phase 6) through the same interface.
"""

from __future__ import annotations

import time
from typing import Any

from finsight.config.settings import LLMSettings
from finsight.core.filters import RetrievalFilters
from finsight.core.logging import bind_trace_id, get_logger
from finsight.core.schemas import Answer, QueryType, Usage
from finsight.generation.citations import attribute_claims, repair_citations
from finsight.generation.context import Source, build_context
from finsight.generation.guardrails import flag_suspicious_sources, is_out_of_scope
from finsight.generation.llm import LLMClient
from finsight.generation.prompts import (
    ABSTAIN_TOKEN,
    DECLINE_ADVICE,
    NO_EVIDENCE,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    render_user_prompt,
)
from finsight.generation.verification import unverified_numbers
from finsight.retrieval.query_analysis import QueryAnalyzer
from finsight.retrieval.retriever import Retriever

log = get_logger(__name__)
DEFAULT_CONTEXT_BUDGET = 7000


class RagPipeline:
    def __init__(
        self,
        retriever: Retriever,
        llm: LLMClient,
        settings: LLMSettings,
        *,
        analyzer: QueryAnalyzer | None = None,
        context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET,
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._settings = settings
        self._analyzer = analyzer
        self._budget = context_budget_tokens

    def answer(self, question: str, *, filters: RetrievalFilters | None = None) -> Answer:
        started = time.perf_counter()
        trace_id = bind_trace_id()

        def finish(**fields: Any) -> Answer:
            return Answer(
                question=question,
                prompt_version=PROMPT_VERSION,
                trace_id=trace_id,
                latency_ms=(time.perf_counter() - started) * 1000,
                **fields,
            )

        analysis = self._analyzer.analyze(question) if self._analyzer else None
        query_type: QueryType | None = analysis.query_type if analysis else None

        if is_out_of_scope(analysis):
            return finish(
                text=DECLINE_ADVICE,
                abstained=True,
                abstain_reason="out_of_scope",
                query_type=query_type,
            )

        result = self._retriever.retrieve(question, filters=filters)
        if result.analysis is not None:
            query_type = result.analysis.query_type
        if not result.chunks:
            return finish(
                text=NO_EVIDENCE,
                abstained=True,
                abstain_reason="no_evidence",
                query_type=query_type,
            )

        flag_suspicious_sources(result.chunks)
        context = build_context(result.chunks, budget_tokens=self._budget)
        llm_result = self._llm.complete(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": render_user_prompt(question, context.text)}],
        )
        usage: Usage = llm_result.usage
        text = llm_result.text.strip()

        if text.startswith(ABSTAIN_TOKEN):
            reason = text.removeprefix(ABSTAIN_TOKEN).lstrip(" :-—").strip()
            return finish(
                text=reason or NO_EVIDENCE,
                abstained=True,
                abstain_reason="model_insufficient_evidence",
                query_type=query_type,
                model=llm_result.model,
                usage=usage,
            )

        text, report = attribute_claims(text, context)
        cited = {c.source_id for c in report.citations}
        # RagPipeline's Context is always built by build_context, so every source is a passage -
        # the isinstance narrows the type for mypy (Context is shared with the agent's fact
        # citations) without changing behaviour.
        passages = [s for s in context.sources if isinstance(s, Source)]
        evidence = [s.chunk.text for s in passages if s.id in cited] or [
            s.chunk.text for s in passages
        ]
        warnings = (
            *(f"citation to unknown source {label}" for label in report.invalid_ids),
            *(f"uncited claim: {sentence[:90]}" for sentence in report.uncited_sentences),
            *(f"unverified figure: {n}" for n in unverified_numbers(text, evidence)),
            *(f"citation does not support its claim: {label}" for label in report.unsupported_ids),
        )
        for warning in warnings:
            log.warning("answer.validation", warning=warning)
        return finish(
            text=repair_citations(text, report),
            citations=report.citations,
            query_type=query_type,
            model=llm_result.model,
            usage=usage,
            warnings=warnings,
        )
