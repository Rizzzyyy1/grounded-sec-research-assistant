"""Agent tools: the only way numbers and passages reach an answer.

* ``get_financial_metric`` / ``compute_ratio`` / ``compare_companies`` read the verified XBRL fact
  store and the ratio library - the model never calculates.
* ``search_filings`` runs hybrid retrieval; every passage it returns is given a citation label
  (``S1``, ``S2`` ...) by a per-run :class:`SourceRegistry`, so the final answer can be validated
  with the same machinery as the single-shot pipeline.
* ``get_risk_factor_changes`` diffs two years of Item 1A.

Each tool returns a JSON document *and* the plain evidence strings that justify it; the agent's
numeric-consistency check accepts a figure only if it appears in that evidence.

**Every reported fact a numeric tool touches also gets a citation label**, the same ``S1, S2, ...``
sequence and the same :class:`~finsight.generation.context.Source`/registry machinery
``search_filings`` already used - see :func:`_register_fact`. A ratio or comparison registers *each
input fact separately* rather than inventing one citation for the computed result: the result
often combines facts from different filings (year-over-year growth spans two 10-Ks), and a single
citation cannot resolve to two URLs, so the model is asked to cite every fact it used, e.g.
``[S1, S2]``, and the tool JSON already carries the exact formula (``compute_ratio``'s
``"formula"``) so the calculation itself is visible without being asserted as a citable fact of its
own. This never fabricates a source: a value this store cannot trace to a catalogued filing gets no
citation label at all (:func:`_register_fact` returns ``None``), and the model is told a bare
figure with no ``source_id`` must not be cited.

(``get_price_history`` from the design is intentionally not implemented: it would add a live
market-data dependency that cannot be verified offline, and the assistant gives no price views.)
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from finsight.analytics.peers import peer_table
from finsight.analytics.ratios import RATIOS, RatioError, compute_ratio, format_value
from finsight.analytics.risk_diff import diff_risk_factors
from finsight.config.universe import Universe
from finsight.core.filters import RetrievalFilters
from finsight.core.schemas import Chunk, FinancialFact, FiscalPeriod
from finsight.generation.context import Context, FactSource, Source
from finsight.ingestion.xbrl.concepts import CANONICAL_METRICS
from finsight.ingestion.xbrl.store import FactStore
from finsight.retrieval.retriever import Retriever

_SNIPPET_CHARS = 900


class ToolError(Exception):
    """A tool could not do what was asked; the message is shown to the model so it can recover."""


@dataclass
class SourceRegistry:
    """Assigns stable citation labels to chunks and facts as they surface during one agent run.

    Tool calls run concurrently (``orchestrator.py``'s ``ThreadPoolExecutor``), and more than one
    of them can register a source in the same run - two ``search_filings`` calls, or a
    ``compute_ratio`` racing a ``get_financial_metric`` - so every mutation is serialised through
    one lock. Without it, two threads computing ``_next_id()`` from the same pre-mutation lengths
    could hand out the same label to two different sources, and a citation would silently resolve
    to the wrong one.
    """

    _by_chunk: dict[str, Source] = field(default_factory=dict)
    _by_fact: dict[tuple[str, str, int, str], FactSource] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def _next_id(self) -> str:
        return f"S{len(self._by_chunk) + len(self._by_fact) + 1}"

    def label(self, chunk: Chunk) -> str:
        with self._lock:
            if chunk.id not in self._by_chunk:
                self._by_chunk[chunk.id] = Source(self._next_id(), chunk)
            return self._by_chunk[chunk.id].id

    def label_fact(self, fact: FinancialFact, *, url: str, metric_label: str) -> str:
        key = (fact.ticker, fact.metric, fact.fiscal_year, fact.fiscal_period.value)
        with self._lock:
            if key not in self._by_fact:
                detail = (
                    f"{metric_label}: {money(fact.value, fact.unit)} (XBRL tag {fact.tag}, "
                    f"{fact.form.value} FY{fact.fiscal_year} {fact.fiscal_period.value}, "
                    f"accession {fact.accession})"
                )
                self._by_fact[key] = FactSource(
                    id=self._next_id(), ticker=fact.ticker, fiscal_year=fact.fiscal_year,
                    metric=fact.metric, tag=fact.tag, url=url, detail=detail,
                )  # fmt: skip
            return self._by_fact[key].id

    def context(self) -> Context:
        with self._lock:
            return Context((*self._by_chunk.values(), *self._by_fact.values()), "", 0)


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


def _fact_json(f: FinancialFact, source_id: str | None) -> dict[str, Any]:
    return {
        "fiscal_year": f.fiscal_year, "fiscal_period": f.fiscal_period.value,
        "value": f.value, "formatted": money(f.value, f.unit), "unit": f.unit,
        "period_end": f.end.isoformat(), "form": f.form.value, "accession": f.accession,
        "xbrl_tag": f.tag, "derived_by_finsight": f.derived, "source_id": source_id,
    }  # fmt: skip


def _register_fact(ctx: AgentContext, fact: FinancialFact) -> str | None:
    """Give one reported fact a citation label, or ``None`` if its filing was never catalogued.

    Never fabricates a source: a fact whose accession has no row in the ``filings`` table (an
    incomplete ingest) gets no citation label at all rather than a guessed or broken URL. The
    agent is told a ``source_id: null`` value must be stated but not cited.
    """
    with ctx.db_lock:
        url = ctx.facts.filing_url(fact.accession)
    if url is None:
        return None
    label = (
        CANONICAL_METRICS[fact.metric].label if fact.metric in CANONICAL_METRICS else fact.metric
    )
    return ctx.registry.label_fact(fact, url=url, metric_label=label)


def _cite_as(source_ids: Iterable[str | None]) -> str | None:
    """The exact bracket to paste when citing a value derived from several facts, e.g. a ratio -
    every distinct, resolvable source that went into it, in first-seen order. ``None`` when none
    of the inputs could be given a citation at all."""
    unique = list(dict.fromkeys(sid for sid in source_ids if sid))
    return f"[{', '.join(unique)}]" if unique else None


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
        "values": [_fact_json(f, _register_fact(ctx, f)) for f in facts],
        "note": "values are as reported in the filings (XBRL); latest restated value per period. "
                "Every value here is real and present regardless of source_id. Cite a value's own "
                "source_id (e.g. [S1]) when you state it; a null source_id only means this run "
                "could not resolve a citation for it - state the figure plainly, never as "
                "unavailable, and do not invent a bracket for it.",
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
    input_facts = (*current.items(), *prior.items())
    source_ids: list[str | None] = [_register_fact(ctx, f) for _m, f in input_facts]
    inputs = [
        {"metric": m, "fiscal_year": f.fiscal_year, "value": f.value,
         "formatted": money(f.value, f.unit), "xbrl_tag": f.tag, "source_id": sid}
        for (m, f), sid in zip(input_facts, source_ids, strict=True)
    ]  # fmt: skip
    payload = {
        "ticker": ticker, "fiscal_year": year, "ratio": name, "label": spec.label,
        "value": value, "formatted": formatted, "formula": spec.formula,
        "inputs": inputs,
        "cite_as": _cite_as(source_ids),
        "note": "value and formatted are the real calculated result (see formula) and are always "
                "present regardless of cite_as - state them plainly, never as unavailable. cite_as "
                "is every input's source_id together (e.g. [S1, S2]); it is null only when none of "
                "the inputs could be traced to a catalogued filing, never a sign the value is "
                "missing. Never invent one citation for the calculation itself.",
    }  # fmt: skip
    evidence = (formatted, f"{value:.4f}", f"{value * 100:.1f}", f"{value:.2f}",
                *(money(f.value) for f in (*current.values(), *prior.values())))  # fmt: skip
    return ToolOutput(_dump(payload), evidence)


@dataclass(frozen=True)
class _ValueResult:
    """A metric or ratio value, kept apart from its citation so the two can never be confused:
    ``value``/``formatted`` are the real answer and are always present; ``cite_as`` is ``None``
    only when this run could not trace it to a catalogued filing - never a sign the value itself
    is missing. ``formula``/``inputs`` are set only for a ratio, mirroring ``compute_ratio``'s own
    payload shape so a comparison is exactly as inspectable as a single-company ratio call."""

    value: float
    formatted: str
    label: str
    cite_as: str | None
    formula: str | None = None
    inputs: tuple[dict[str, Any], ...] = ()


def _value_for(ctx: AgentContext, ticker: str, name: str, year: int) -> _ValueResult:
    if name in RATIOS:
        spec = RATIOS[name]
        cur = {m: _need(ctx, ticker, m, year) for m in spec.inputs}
        prior = {m: _need(ctx, ticker, m, year - 1) for m in spec.prior_inputs}
        value = compute_ratio(
            name, {m: f.value for m, f in cur.items()}, {m: f.value for m, f in prior.items()}
        )
        input_facts = (*cur.items(), *prior.items())
        source_ids = [_register_fact(ctx, f) for _m, f in input_facts]
        inputs = tuple(
            {"metric": m, "fiscal_year": f.fiscal_year, "value": f.value,
             "formatted": money(f.value, f.unit), "xbrl_tag": f.tag, "source_id": sid}
            for (m, f), sid in zip(input_facts, source_ids, strict=True)
        )  # fmt: skip
        return _ValueResult(
            value, format_value(value, spec.kind), spec.label, _cite_as(source_ids),
            formula=spec.formula, inputs=inputs,
        )  # fmt: skip
    fact = _need(ctx, ticker, _metric(name), year)
    sid = _register_fact(ctx, fact)
    return _ValueResult(
        fact.value, money(fact.value, fact.unit), CANONICAL_METRICS[name].label, _cite_as([sid])
    )


_VALUE_NOTE = (
    "value and formatted are the real reported or calculated result for that company - always "
    "state them, in the fiscal year and unit shown here, without converting or re-deriving them. "
    "cite_as is null only when this run could not trace the underlying fact(s) to a catalogued "
    "filing; that means the number is uncited, never that the number itself is missing - do not "
    "say a value is unavailable just because cite_as is null."
)


def compare_companies(ctx: AgentContext, args: Mapping[str, Any]) -> ToolOutput:
    tickers = [_ticker(ctx, str(t)) for t in _as_list(args["tickers"])]
    if len(tickers) < 2:
        raise ToolError("compare_companies needs at least two tickers")
    name, year = str(args["metric"]), int(args["fiscal_year"])
    values: dict[str, float] = {}
    results: dict[str, _ValueResult] = {}
    skipped: dict[str, str] = {}
    label, formula = name, None
    for t in tickers:
        try:
            result = _value_for(ctx, t, name, year)
            values[t], results[t], label, formula = (
                result.value,
                result,
                result.label,
                result.formula,
            )
        except (ToolError, RatioError) as exc:
            skipped[t] = str(exc)
    if len(values) < 2:
        raise ToolError(f"fewer than two companies have {name} for fiscal {year}: {skipped}")
    rows = peer_table(values)
    ranking = []
    for row_rank in rows:
        result = results[row_rank.ticker]
        row = {
            "rank": row_rank.rank, "ticker": row_rank.ticker, "value": row_rank.value,
            "formatted": result.formatted, "percentile": row_rank.percentile,
            "cite_as": result.cite_as,
        }  # fmt: skip
        if result.inputs:
            row["inputs"] = result.inputs
        ranking.append(row)
    payload: dict[str, Any] = {
        "metric": name,
        "label": label,
        "fiscal_year": year,
        "ranking": ranking,
        "skipped": skipped,
        "note": _VALUE_NOTE,
    }
    if formula:
        payload["formula"] = formula
    return ToolOutput(_dump(payload), tuple(r.formatted for r in results.values()))


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
