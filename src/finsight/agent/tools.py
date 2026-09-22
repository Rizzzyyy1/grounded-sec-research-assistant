"""Agent tools: the only way numbers and passages reach an answer.

* ``get_financial_metric`` / ``compute_ratio`` / ``compare_companies`` read the verified XBRL fact
  store and the ratio library - the model never calculates.
* ``search_filings`` runs hybrid retrieval; every passage it returns is given a citation label
  (``S1``, ``S2`` ...) by a per-run :class:`SourceRegistry`, so the final answer can be validated
  with the same machinery as the single-shot pipeline.
* ``get_risk_factor_changes`` diffs two years of Item 1A.

Each tool returns a JSON document *and* the plain evidence strings that justify it; the agent's
numeric-consistency check accepts a figure only if it appears in that evidence.

(``get_price_history`` from the design is intentionally not implemented: it would add a live
market-data dependency that cannot be verified offline, and the assistant gives no price views.)
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from finsight.analytics.peers import peer_table
from finsight.analytics.ratios import RATIOS, RatioError, compute_ratio, format_value
from finsight.analytics.risk_diff import diff_risk_factors
from finsight.config.universe import Universe
from finsight.core.filters import RetrievalFilters
from finsight.core.schemas import Chunk, FinancialFact, FiscalPeriod
from finsight.generation.context import Context, Source
from finsight.ingestion.xbrl.concepts import CANONICAL_METRICS
from finsight.ingestion.xbrl.store import FactStore
from finsight.retrieval.retriever import Retriever

_SNIPPET_CHARS = 900


class ToolError(Exception):
    """A tool could not do what was asked; the message is shown to the model so it can recover."""


@dataclass
class SourceRegistry:
    """Assigns stable citation labels to chunks as they surface during one agent run."""

    _by_chunk: dict[str, Source] = field(default_factory=dict)

    def label(self, chunk: Chunk) -> str:
        if chunk.id not in self._by_chunk:
            self._by_chunk[chunk.id] = Source(f"S{len(self._by_chunk) + 1}", chunk)
        return self._by_chunk[chunk.id].id

    def context(self) -> Context:
        sources = tuple(self._by_chunk.values())
        return Context(sources, "", 0)


@dataclass
class AgentContext:
    facts: FactStore
    retriever: Retriever
    universe: Universe
    registry: SourceRegistry = field(default_factory=SourceRegistry)
    db_lock: threading.Lock = field(default_factory=threading.Lock)
    search_lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass(frozen=True)
class ToolOutput:
    text: str  # JSON returned to the model
    evidence: tuple[str, ...] = ()  # strings a stated figure may be verified against


# --------------------------------------------------------------------------- helpers
def money(value: float, unit: str = "USD") -> str:
    if unit == "USD/shares":
        return f"${value:,.2f}"
    return f"${value / 1e6:,.0f} million"


def _ticker(ctx: AgentContext, raw: str) -> str:
    try:
        return ctx.universe.company(raw.strip()).ticker
    except KeyError:
        known = ", ".join(ctx.universe.tickers)
        raise ToolError(f"unknown company {raw!r}; covered tickers: {known}") from None


def _metric(name: str) -> str:
    if name not in CANONICAL_METRICS:
        # A model reaching for get_financial_metric with a ratio name (e.g. "roe") is a common,
        # recoverable mistake - name the right tool instead of just listing what this one accepts.
        if name in RATIOS:
            raise ToolError(f"{name!r} is a ratio, not a reported metric; use compute_ratio")
        raise ToolError(
            f"unknown metric {name!r}; available: {', '.join(sorted(CANONICAL_METRICS))}"
        )
    return name


def _fact(
    ctx: AgentContext, ticker: str, metric: str, year: int, period: FiscalPeriod
) -> FinancialFact | None:
    with ctx.db_lock:
        return ctx.facts.get_fact(ticker, metric, year, period)


def _need(ctx: AgentContext, ticker: str, metric: str, year: int) -> FinancialFact:
    fact = _fact(ctx, ticker, metric, year, FiscalPeriod.FY)
    if fact is None:
        company = ctx.universe.company(ticker)
        reason = company.known_gaps.get(metric)
        detail = f" ({reason})" if reason else ""
        raise ToolError(f"{ticker} has no {metric} for fiscal {year}{detail}")
    return fact


def _fact_json(f: FinancialFact) -> dict[str, Any]:
    return {
        "fiscal_year": f.fiscal_year, "fiscal_period": f.fiscal_period.value,
        "value": f.value, "formatted": money(f.value, f.unit), "unit": f.unit,
        "period_end": f.end.isoformat(), "form": f.form.value, "accession": f.accession,
        "xbrl_tag": f.tag, "derived_by_finsight": f.derived,
    }  # fmt: skip


def _dump(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, default=str)


def _as_list(value: Any) -> list[Any]:
    """Coerce an array-typed tool argument that arrived in the wrong shape.

    Anthropic's tool use reliably matches the declared JSON Schema, but smaller/local models
    (observed with Ollama function calling) sometimes stringify an array argument - e.g.
    ``fiscal_years: "[2024]"`` instead of ``[2024]`` - or send a single bare value instead of a
    one-item list. Accepting the reasonable shapes here means one weird argument degrades that one
    tool call, not a hard ``ToolError`` that ends the agent's turn.
    """
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return parsed if isinstance(parsed, list) else [parsed]
    return [value]


# --------------------------------------------------------------------------- handlers
def get_financial_metric(ctx: AgentContext, args: Mapping[str, Any]) -> ToolOutput:
    ticker, metric = _ticker(ctx, str(args["ticker"])), _metric(str(args["metric"]))
    period = FiscalPeriod(str(args.get("period", "FY")))
    years = [int(y) for y in _as_list(args.get("fiscal_years"))]
    # ALL store access happens inside one critical section: a DuckDB connection must not be used
    # by two threads at once, and parallel tool calls do exactly that if the lock is released
    # between the two queries.
    with ctx.db_lock:
        df = ctx.facts.get_metric(ticker, metric, period=period, years=years or None)
        facts = [
            f
            for y in df["fiscal_year"]
            for f in [ctx.facts.get_fact(ticker, metric, int(y), period)]
            if f
        ]
    if not facts:
        raise ToolError(f"no {metric} data for {ticker} ({period.value}) in the requested years")
    payload = {
        "ticker": ticker, "metric": metric, "label": CANONICAL_METRICS[metric].label,
        "values": [_fact_json(f) for f in facts],
        "note": "values are as reported in the filings (XBRL); latest restated value per period",
    }  # fmt: skip
    evidence = tuple(
        x for f in facts for x in (money(f.value, f.unit), f"{f.value:,.0f}", f"{f.value}")
    )
    return ToolOutput(_dump(payload), evidence)


def compute_ratio_tool(ctx: AgentContext, args: Mapping[str, Any]) -> ToolOutput:
    ticker, name, year = (
        _ticker(ctx, str(args["ticker"])),
        str(args["ratio"]),
        int(args["fiscal_year"]),
    )
    spec = RATIOS.get(name)
    if spec is None:
        raise ToolError(f"unknown ratio {name!r}; available: {', '.join(sorted(RATIOS))}")
    current = {m: _need(ctx, ticker, m, year) for m in spec.inputs}
    prior = {m: _need(ctx, ticker, m, year - 1) for m in spec.prior_inputs}
    try:
        value = compute_ratio(
            name, {m: f.value for m, f in current.items()}, {m: f.value for m, f in prior.items()}
        )
    except RatioError as exc:
        raise ToolError(str(exc)) from exc
    formatted = format_value(value, spec.kind)
    payload = {
        "ticker": ticker, "fiscal_year": year, "ratio": name, "label": spec.label,
        "value": value, "formatted": formatted, "formula": spec.formula,
        "inputs": [{"metric": m, "fiscal_year": f.fiscal_year, "value": f.value,
                    "formatted": money(f.value, f.unit), "xbrl_tag": f.tag}
                   for m, f in (*current.items(), *prior.items())],
    }  # fmt: skip
    evidence = (formatted, f"{value:.4f}", f"{value * 100:.1f}", f"{value:.2f}",
                *(money(f.value) for f in (*current.values(), *prior.values())))  # fmt: skip
    return ToolOutput(_dump(payload), evidence)


def _value_for(ctx: AgentContext, ticker: str, name: str, year: int) -> tuple[float, str, str]:
    """(value, formatted, label) for a metric or a ratio."""
    if name in RATIOS:
        spec = RATIOS[name]
        cur = {m: _need(ctx, ticker, m, year).value for m in spec.inputs}
        prior = {m: _need(ctx, ticker, m, year - 1).value for m in spec.prior_inputs}
        value = compute_ratio(name, cur, prior)
        return value, format_value(value, spec.kind), spec.label
    fact = _need(ctx, ticker, _metric(name), year)
    return fact.value, money(fact.value, fact.unit), CANONICAL_METRICS[name].label


def compare_companies(ctx: AgentContext, args: Mapping[str, Any]) -> ToolOutput:
    tickers = [_ticker(ctx, str(t)) for t in _as_list(args["tickers"])]
    if len(tickers) < 2:
        raise ToolError("compare_companies needs at least two tickers")
    name, year = str(args["metric"]), int(args["fiscal_year"])
    values: dict[str, float] = {}
    formatted: dict[str, str] = {}
    skipped: dict[str, str] = {}
    label = name
    for t in tickers:
        try:
            values[t], formatted[t], label = _value_for(ctx, t, name, year)
        except (ToolError, RatioError) as exc:
            skipped[t] = str(exc)
    if len(values) < 2:
        raise ToolError(f"fewer than two companies have {name} for fiscal {year}: {skipped}")
    rows = peer_table(values)
    payload = {
        "metric": name, "label": label, "fiscal_year": year,
        "ranking": [{"rank": r.rank, "ticker": r.ticker, "value": r.value,
                     "formatted": formatted[r.ticker], "percentile": r.percentile} for r in rows],
        "skipped": skipped,
    }  # fmt: skip
    return ToolOutput(_dump(payload), tuple(formatted.values()))


def search_filings(ctx: AgentContext, args: Mapping[str, Any]) -> ToolOutput:
    query = str(args["query"])
    tickers = tuple(_ticker(ctx, str(t)) for t in _as_list(args.get("tickers")))
    filters = RetrievalFilters(
        tickers=tickers,
        fiscal_years=tuple(int(y) for y in _as_list(args.get("fiscal_years"))),
        items=tuple(str(i) for i in _as_list(args.get("items"))),
    )
    with ctx.search_lock:
        result = ctx.retriever.retrieve(
            query, k=int(args.get("k", 6)), filters=filters, auto_filters=False
        )
    if not result.chunks:
        raise ToolError("no passages matched; try different words or fewer filters")
    passages = []
    for r in result.chunks:
        m = r.chunk.metadata
        passages.append({
            "source_id": ctx.registry.label(r.chunk),
            "filing": f"{m.ticker} {m.form.value} FY{m.fiscal_year}", "item": m.item,
            "text": r.chunk.text[:_SNIPPET_CHARS],
        })  # fmt: skip
    return ToolOutput(_dump({"passages": passages}), tuple(r.chunk.text for r in result.chunks))


def get_risk_factor_changes(ctx: AgentContext, args: Mapping[str, Any]) -> ToolOutput:
    ticker, year = _ticker(ctx, str(args["ticker"])), int(args["fiscal_year"])
    prior_year = int(args.get("prior_year", year - 1))

    def item_1a(y: int) -> list[Chunk]:
        found = [c for c in ctx.retriever.catalogue.values()
                 if c.metadata.ticker == ticker and c.metadata.fiscal_year == y and c.metadata.item == "1A"]  # fmt: skip
        return sorted(found, key=lambda c: c.metadata.ordinal)

    new, old = item_1a(year), item_1a(prior_year)
    if not new or not old:
        raise ToolError(
            f"Item 1A is not available for {ticker} in both fiscal {prior_year} and {year}"
        )
    diff = diff_risk_factors([c.text for c in old], [c.text for c in new])
    by_text_new = {c.text: c for c in new}
    by_text_old = {c.text: c for c in old}

    def show(items: list[Any], lookup: Mapping[str, Chunk]) -> list[dict[str, Any]]:
        out = []
        for change in items[:5]:
            chunk = lookup[change.text]
            out.append({"source_id": ctx.registry.label(chunk), "similarity": round(change.similarity, 2),
                        "text": change.text[:_SNIPPET_CHARS]})  # fmt: skip
        return out

    payload = {
        "ticker": ticker, "fiscal_year": year, "compared_with": prior_year,
        "summary": {"added": len(diff.added), "modified": len(diff.modified), "removed": len(diff.removed),
                    "unchanged": diff.unchanged, "churn": round(diff.churn, 2)},
        "added": show(diff.added, by_text_new), "modified": show(diff.modified, by_text_new),
        "removed": show(diff.removed, by_text_old),
    }  # fmt: skip
    return ToolOutput(_dump(payload), tuple(c.text for c in (*new, *old)))


# --------------------------------------------------------------------------- registry
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[AgentContext, Mapping[str, Any]], ToolOutput]

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_TICKER = {"type": "string", "description": "Company ticker, e.g. AAPL"}
_YEAR = {"type": "integer", "description": "The company's own fiscal year label, e.g. 2024"}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "get_financial_metric",
        "Reported financial figures from the companies' XBRL filings for one metric over one or more "
        "fiscal years. Use this for any revenue, income, asset or cash-flow number - never estimate.",
        _schema(
            {
                "ticker": _TICKER,
                "metric": {"type": "string", "enum": sorted(CANONICAL_METRICS)},
                "fiscal_years": {
                    "type": "array",
                    "items": _YEAR,
                    "description": "Omit for all available years",
                },
                "period": {
                    "type": "string",
                    "enum": ["FY", "Q1", "Q2", "Q3", "Q4"],
                    "description": "Default FY",
                },
            },
            ["ticker", "metric"],
        ),
        get_financial_metric,
    ),
    ToolSpec(
        "compute_ratio",
        "Compute a financial ratio (margins, ROE, ROA, liquidity, leverage, coverage, turnover, cash "
        "conversion) for one company and fiscal year. Returns the value, the exact formula and every input.",
        _schema(
            {
                "ticker": _TICKER,
                "ratio": {"type": "string", "enum": sorted(RATIOS)},
                "fiscal_year": _YEAR,
            },
            ["ticker", "ratio", "fiscal_year"],
        ),
        compute_ratio_tool,
    ),
    ToolSpec(
        "compare_companies",
        "Rank several companies on one metric or ratio for a fiscal year (best first), with percentiles.",
        _schema(
            {
                "tickers": {"type": "array", "items": _TICKER, "minItems": 2},
                "metric": {"type": "string", "description": "A metric or ratio name"},
                "fiscal_year": _YEAR,
            },
            ["tickers", "metric", "fiscal_year"],
        ),
        compare_companies,
    ),
    ToolSpec(
        "search_filings",
        "Search the text of 10-K filings (business, risk factors, MD&A, legal proceedings ...). Returns "
        "passages, each with a source_id you must cite. Use for explanations, drivers, risks and policies.",
        _schema(
            {
                "query": {"type": "string"},
                "tickers": {"type": "array", "items": _TICKER},
                "fiscal_years": {"type": "array", "items": _YEAR},
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "10-K Items to restrict to: 1, 1A, 3, 7, 7A, 8 ...",
                },
                "k": {"type": "integer", "minimum": 1, "maximum": 12},
            },
            ["query"],
        ),
        search_filings,
    ),
    ToolSpec(
        "get_risk_factor_changes",
        "Compare a company's risk factors (Item 1A) between two fiscal years: added, reworded and removed "
        "risks, each with a citable source_id.",
        _schema(
            {"ticker": _TICKER, "fiscal_year": _YEAR, "prior_year": _YEAR},
            ["ticker", "fiscal_year"],
        ),
        get_risk_factor_changes,
    ),
)
_BY_NAME = {t.name: t for t in TOOLS}


def tool_definitions() -> list[dict[str, Any]]:
    return [t.definition() for t in TOOLS]


def dispatch(ctx: AgentContext, name: str, args: Mapping[str, Any]) -> ToolOutput:
    spec = _BY_NAME.get(name)
    if spec is None:
        raise ToolError(f"unknown tool {name!r}; available: {', '.join(_BY_NAME)}")
    try:
        return spec.handler(ctx, args)
    except KeyError as exc:
        raise ToolError(f"missing required argument {exc}") from exc
    except (ValueError, TypeError) as exc:
        raise ToolError(f"invalid arguments: {exc}") from exc
