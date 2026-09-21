"""Deterministic tool router: an offline "agent" that needs no LLM.

It reads the question with the rule-based analyser and, for questions whose answer is a *number*
(reported figure, ratio, growth, comparison), calls the same tools an LLM agent would and writes
the answer from the tool output. Everything else (qualitative, fact lookup, change detection)
is delegated to a fallback pipeline.

Why it exists: it demonstrates and *measures* the core design claim - numbers come from
structured data through deterministic tools, not from text - with real data and no API key,
and it is the honest baseline the LLM agent has to beat on language flexibility (paraphrase,
multi-hop reasoning), not on arithmetic.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from finsight.agent.tools import AgentContext, ToolError, ToolOutput, dispatch
from finsight.analytics.ratios import RATIOS
from finsight.core.logging import bind_trace_id
from finsight.core.schemas import Answer, QueryAnalysis, QueryType, ToolCallRecord
from finsight.generation.prompts import DECLINE_ADVICE, NO_EVIDENCE
from finsight.generation.verification import unverified_numbers

ROUTER_VERSION = "router-v1"
_NUMERIC_TYPES = {
    QueryType.NUMERIC, QueryType.COMPUTED_METRIC, QueryType.TREND, QueryType.COMPARISON,
}  # fmt: skip


class ToolRouterAgent:
    def __init__(self, ctx: AgentContext, fallback: Callable[[str], Answer]) -> None:
        analyzer = ctx.retriever.analyzer
        if analyzer is None:
            raise ValueError("the router needs a retriever with a query analyzer")
        self._ctx, self._analyzer, self._fallback = ctx, analyzer, fallback

    # ------------------------------------------------------------------ public
    def answer(self, question: str) -> Answer:
        started, trace_id = time.perf_counter(), bind_trace_id()
        analysis = self._analyzer.analyze(question)

        def finish(
            text: str, calls: list[ToolCallRecord], evidence: tuple[str, ...] = (), **kw: Any
        ) -> Answer:
            warnings = (
                tuple(f"unverified figure: {n}" for n in unverified_numbers(text, evidence))
                if evidence
                else ()
            )
            return Answer(
                question=question, text=text, tool_calls=tuple(calls), query_type=analysis.query_type,
                model=ROUTER_VERSION, prompt_version=ROUTER_VERSION, trace_id=trace_id, warnings=warnings,
                latency_ms=(time.perf_counter() - started) * 1000, **kw,
            )  # fmt: skip

        if analysis.query_type is QueryType.OUT_OF_SCOPE:
            return finish(DECLINE_ADVICE, [], abstained=True, abstain_reason="out_of_scope")
        if analysis.query_type not in _NUMERIC_TYPES:
            delegated = self._fallback(question)
            return delegated.model_copy(update={"trace_id": trace_id})

        calls: list[ToolCallRecord] = []
        try:
            text, evidence = self._route(analysis, calls)
        except ToolError as exc:
            return finish(
                f"{NO_EVIDENCE} ({exc})", calls, abstained=True, abstain_reason="tool_error"
            )
        return finish(text, calls, evidence)

    # ------------------------------------------------------------------ routing
    def _call(
        self, calls: list[ToolCallRecord], name: str, args: dict[str, Any]
    ) -> tuple[dict[str, Any], ToolOutput]:
        started = time.perf_counter()
        try:
            out = dispatch(self._ctx, name, args)
        except ToolError as exc:
            calls.append(ToolCallRecord(name=name, arguments=args, result_summary=str(exc),
                                        latency_ms=(time.perf_counter() - started) * 1000, is_error=True))  # fmt: skip
            raise
        calls.append(ToolCallRecord(name=name, arguments=args, result_summary=out.text[:200].replace("\n", " "),
                                    latency_ms=(time.perf_counter() - started) * 1000))  # fmt: skip
        return json.loads(out.text), out

    def _company(self, ticker: str) -> str:
        c = self._ctx.universe.company(ticker)
        return c.aliases[0] if c.aliases else c.name

    def _route(self, a: QueryAnalysis, calls: list[ToolCallRecord]) -> tuple[str, tuple[str, ...]]:
        metric = a.metrics[0] if a.metrics else None
        if metric is None:
            raise ToolError("no financial metric recognised in the question")
        if a.query_type is QueryType.COMPARISON:
            return self._comparison(a, metric, calls)
        if len(a.tickers) != 1:
            raise ToolError("expected exactly one company in the question")
        ticker = a.tickers[0]
        if a.query_type is QueryType.COMPUTED_METRIC or metric in RATIOS:
            return self._ratio(a, ticker, metric, calls)
        if a.query_type is QueryType.TREND and len(a.fiscal_years) >= 2:
            return self._trend(a, ticker, metric, calls)
        return self._numeric(a, ticker, metric, calls)

    def _year(self, a: QueryAnalysis) -> int:
        if not a.fiscal_years:
            raise ToolError("no fiscal year given; please name one")
        return a.fiscal_years[-1]

    def _numeric(
        self, a: QueryAnalysis, ticker: str, metric: str, calls: list[ToolCallRecord]
    ) -> tuple[str, tuple[str, ...]]:
        year = self._year(a)
        payload, out = self._call(
            calls,
            "get_financial_metric",
            {"ticker": ticker, "metric": metric, "fiscal_years": [year]},
        )
        v = payload["values"][0]
        text = (f"{self._company(ticker)}'s {payload['label'].lower()} for fiscal {year} was {v['formatted']} "
                f"(XBRL {v['xbrl_tag']}, {v['form']} accession {v['accession']}).")  # fmt: skip
        return text, out.evidence

    def _ratio(
        self, a: QueryAnalysis, ticker: str, ratio: str, calls: list[ToolCallRecord]
    ) -> tuple[str, tuple[str, ...]]:
        year = self._year(a)
        payload, out = self._call(
            calls, "compute_ratio", {"ticker": ticker, "ratio": ratio, "fiscal_year": year}
        )
        inputs = "; ".join(f"{i['metric']} = {i['formatted']}" for i in payload["inputs"])
        text = (f"{self._company(ticker)}'s {payload['label'].lower()} in fiscal {year} was {payload['formatted']} "
                f"({payload['formula']}; {inputs}).")  # fmt: skip
        return text, out.evidence

    def _trend(
        self, a: QueryAnalysis, ticker: str, metric: str, calls: list[ToolCallRecord]
    ) -> tuple[str, tuple[str, ...]]:
        first, last = min(a.fiscal_years), max(a.fiscal_years)
        payload, out = self._call(
            calls,
            "get_financial_metric",
            {"ticker": ticker, "metric": metric, "fiscal_years": [first, last]},
        )
        by_year = {v["fiscal_year"]: v for v in payload["values"]}
        if first not in by_year or last not in by_year:
            raise ToolError(f"{metric} is not available for both fiscal {first} and {last}")
        v0, v1 = by_year[first], by_year[last]
        change = (v1["value"] - v0["value"]) / abs(v0["value"])
        growth = f"{change * 100:.1f}%"
        text = (f"{self._company(ticker)}'s {payload['label'].lower()} changed by {growth} from fiscal {first} "
                f"({v0['formatted']}) to fiscal {last} ({v1['formatted']}).")  # fmt: skip
        return text, (*out.evidence, growth)

    def _comparison(
        self, a: QueryAnalysis, metric: str, calls: list[ToolCallRecord]
    ) -> tuple[str, tuple[str, ...]]:
        if len(a.tickers) < 2:
            raise ToolError("a comparison needs two companies")
        year = self._year(a)
        payload, out = self._call(
            calls,
            "compare_companies",
            {"tickers": list(a.tickers), "metric": metric, "fiscal_year": year},
        )
        ranking = payload["ranking"]
        top = ranking[0]
        rest = ", ".join(f"{self._company(r['ticker'])} {r['formatted']}" for r in ranking[1:])
        text = (f"{self._company(top['ticker'])} had the higher {payload['label'].lower()} in fiscal {year}: "
                f"{top['formatted']} (versus {rest}).")  # fmt: skip
        return text, out.evidence
