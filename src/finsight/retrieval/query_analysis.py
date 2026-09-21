"""Rule-based query understanding: tickers, fiscal years, forms, Items, metrics, question type.

Deterministic and free (no LLM call): a regex/alias pass handles the overwhelming majority of
analyst questions in microseconds and is exactly reproducible in evaluation. Extracted tickers,
years and forms become retrieval *pre-filters*; the question type drives routing (text retrieval
vs. XBRL tools) and per-type evaluation breakdowns.

Design choices worth knowing:

* Company *names* and aliases match case-insensitively ("apple", "J&J"), but bare ticker
  *symbols* only match in upper case ("KO", "PG"), so ordinary words never become filters.
* Item hints are returned but are **not** turned into filters by default: a wrong hint would
  silently exclude the right passage, so they are exposed for boosting/diagnostics instead.
"""

from __future__ import annotations

import re

from finsight.config.universe import Universe
from finsight.core.filters import RetrievalFilters
from finsight.core.schemas import FormType, QueryAnalysis, QueryType

# ---------------------------------------------------------------------------- vocabularies
_ITEM_HINTS: tuple[tuple[str, str], ...] = (
    (r"risk factors?|risks? (?:that|which)|what risks?|key risks?|risks? (?:does|did|do)", "1A"),
    (r"md&a|management'?s discussion|results of operations|liquidity and capital", "7"),
    (r"legal proceedings|litigation|lawsuits?", "3"),
    (r"cyber\s?security|information security|data breach", "1C"),
    (r"quantitative and qualitative|market risk|interest rate risk|foreign currency risk", "7A"),
    (r"controls and procedures|internal control", "9A"),
    (r"executive compensation|executive pay", "11"),
    (r"properties|real estate holdings|headquarters", "2"),
    (r"business overview|our business|company overview|segments?", "1"),
    (r"financial statements|notes to the financial|footnotes?", "8"),
)  # fmt: skip

RATIO_METRICS = frozenset(
    {
        "gross_margin", "operating_margin", "net_margin", "roe", "roa", "roic", "current_ratio",
        "debt_to_equity", "interest_coverage", "free_cash_flow", "asset_turnover", "dupont",
        "effective_tax_rate",
    }
)  # fmt: skip

_METRIC_SYNONYMS: tuple[tuple[str, str], ...] = (
    (r"gross margin", "gross_margin"),
    (r"operating margin|operating profit margin", "operating_margin"),
    (r"net (?:profit )?margin|profit margin", "net_margin"),
    (r"return on equity|\broe\b", "roe"),
    (r"return on assets|\broa\b", "roa"),
    (r"return on invested capital|\broic\b", "roic"),
    (r"current ratio", "current_ratio"),
    (r"debt[- ]to[- ]equity|leverage ratio", "debt_to_equity"),
    (r"interest coverage", "interest_coverage"),
    (r"free cash flow|\bfcf\b", "free_cash_flow"),
    (r"asset turnover", "asset_turnover"),
    (r"du ?pont", "dupont"),
    (r"effective tax rate", "effective_tax_rate"),
    (r"gross profit", "gross_profit"),
    (r"operating income|operating profit|\bebit\b", "operating_income"),
    (r"net income|net earnings|bottom line", "net_income"),
    (r"earnings per share|\beps\b", "eps_diluted"),
    (r"total assets", "total_assets"),
    (r"total liabilities", "total_liabilities"),
    (r"shareholders'? equity|stockholders'? equity", "shareholders_equity"),
    (r"operating cash flow|cash (?:flow )?from operations", "operating_cash_flow"),
    (r"capital expenditures?|\bcapex\b", "capex"),
    (r"research and development|\br&d\b", "research_and_development"),
    (r"long[- ]term debt", "long_term_debt"),
    (r"cash and (?:cash )?equivalents|cash balance", "cash_and_equivalents"),
    (r"dividends?", "dividends_paid"),
    (r"buybacks?|share repurchases?|repurchased", "share_repurchases"),
    (r"inventory|inventories", "inventory"),
    (r"revenues?|net sales|\bsales\b|top line", "revenue"),
)  # fmt: skip

_ADVICE = re.compile(
    r"\b(?:should (?:i|we) (?:buy|sell|invest|hold|short)"
    r"|(?:is|are|was|would|will)\b[^?.!]{0,40}?"
    r"\b(?:good|great|bad|smart|safe) (?:buy|investment|stock|bet|company to (?:buy|invest))"
    r"|worth (?:buying|investing)|buy or sell|price target"
    r"|(?:good|great|right|best|wrong) time to (?:buy|sell|invest|load up|get in|get out)"
    r"|load(?:ing)? up on|(?:over|under)[- ]?valued"
    r"|best (?:one|stocks?|compan(?:y|ies))\b[^?.!]{0,30}\bto (?:buy|invest)"
    r"|(?:would|do|could|should) you (?:recommend|suggest|advise)"
    r"|recommend\b[^?.!]{0,40}\binvestors?"
    r"|will (?:the |its |their )?(?:stock|shares?|price)\b[^?.!]{0,30}"
    r"\b(?:go|rise|fall|increase|drop|double|beat|outperform)"
    r"|do you think\b[^?.!]{0,50}\b(?:stock|shares?)\b[^?.!]{0,40}"
    r"\b(?:beat|outperform|rise|fall|go up|go down|double|drop|crash)"
    r"|predict (?:the )?(?:stock|price)|which stock should)\b",
    re.IGNORECASE,
)
_CHANGE = re.compile(
    r"\b(?:what(?:'s| is| has| have)? (?:new|changed|different)|new risks?|newly added"
    r"|added|removed|no longer"
    r"|compared (?:to|with) (?:last|the prior|the previous) year"
    r"|changes? (?:in|to) (?:the )?(?:risk|language)|differences? (?:in|between))\b",
    re.IGNORECASE,
)
_COMPARE = re.compile(
    r"\b(?:compare|comparison|versus|vs\.?|relative to|better than|higher than|lower than"
    r"|against)\b",
    re.IGNORECASE,
)
_WHY = re.compile(
    r"\b(?:why|what (?:drove|caused|explains?|led to)|drivers?|reasons? for|due to what)\b",
    re.IGNORECASE,
)
_TREND = re.compile(
    r"\b(?:trend|over (?:the )?(?:last|past) \w+ years?|since 20\d\d"
    r"|from 20\d\d (?:to|through|until)|year[- ]over[- ]year|growth|grown|grew|evolv\w*"
    r"|changed over|history of|cagr"
    r"|has (?:\w+ )?(?:increased|decreased|risen|fallen|grown|declined))\b",
    re.IGNORECASE,
)
_QUALITATIVE = re.compile(
    r"\b(?:how|risk|risks|strategy|describe|explain|discuss|outlook|competition|competitive"
    r"|regulat\w+|supply chain|challenges?|opportunit\w+|what does|tell me about)\b",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(?:fy\s?'?|fiscal(?: year)?\s+)?(20[12]\d)\b", re.IGNORECASE)
_YEAR_SHORT = re.compile(r"\bfy\s?'?(\d{2})\b", re.IGNORECASE)
_YEAR_RANGE = re.compile(
    r"\b(20[12]\d)\s*(?:-|to|through|until|and)\s*(?:fy\s?)?(20[12]\d)\b", re.IGNORECASE
)
_TEN_Q = re.compile(r"\b10-?q\b|quarterly (?:report|filing)|\bq[1-4]\b", re.IGNORECASE)
_TEN_K = re.compile(r"\b10-?k\b|annual (?:report|filing)", re.IGNORECASE)


class QueryAnalyzer:
    def __init__(self, universe: Universe) -> None:
        self._universe = universe
        names: dict[str, str] = {}
        for company in universe.companies:
            for name in (company.name, *company.aliases):
                names[name.lower()] = company.ticker
        # Longest names first so "procter & gamble" wins over a shorter overlapping alias.
        self._name_patterns = [
            (re.compile(rf"(?<![\w&]){re.escape(n)}(?![\w&])", re.IGNORECASE), t)
            for n, t in sorted(names.items(), key=lambda kv: -len(kv[0]))
        ]
        self._symbol_patterns = [
            (re.compile(rf"(?<![\w&.]){re.escape(c.ticker)}(?![\w&])"), c.ticker)
            for c in universe.companies
        ]

    # ------------------------------------------------------------------ extractors
    def tickers(self, query: str) -> tuple[str, ...]:
        found: list[tuple[int, str]] = []
        for pattern, ticker in [*self._symbol_patterns, *self._name_patterns]:
            if m := pattern.search(query):
                found.append((m.start(), ticker))
        ordered: list[str] = []
        for _pos, ticker in sorted(found):
            if ticker not in ordered:
                ordered.append(ticker)
        return tuple(ordered)

    @staticmethod
    def fiscal_years(query: str) -> tuple[int, ...]:
        years: set[int] = set()
        for a, b in _YEAR_RANGE.findall(query):
            lo, hi = sorted((int(a), int(b)))
            years.update(range(lo, min(hi, lo + 15) + 1))
        years.update(int(y) for y in _YEAR.findall(query))
        years.update(2000 + int(y) for y in _YEAR_SHORT.findall(query))
        return tuple(sorted(years))

    @staticmethod
    def forms(query: str) -> tuple[FormType, ...]:
        out: list[FormType] = []
        if _TEN_K.search(query):
            out.append(FormType.TEN_K)
        if _TEN_Q.search(query):
            out.append(FormType.TEN_Q)
        return tuple(out)

    @staticmethod
    def items(query: str) -> tuple[str, ...]:
        hits: list[str] = []
        for pattern, item in _ITEM_HINTS:
            if re.search(pattern, query, re.IGNORECASE) and item not in hits:
                hits.append(item)
        return tuple(hits)

    @staticmethod
    def metrics(query: str) -> tuple[str, ...]:
        found: list[tuple[int, str]] = []
        for pattern, metric in _METRIC_SYNONYMS:
            if m := re.search(pattern, query, re.IGNORECASE):
                found.append((m.start(), metric))
        out: list[str] = []
        for _pos, metric in sorted(found):
            if metric not in out:
                out.append(metric)
        # "gross margin" should not also report the substring-matched "gross profit" as asked for.
        return tuple(out)

    # ------------------------------------------------------------------ classification
    @staticmethod
    def classify(  # noqa: PLR0911 - an ordered rule cascade reads best as early returns
        query: str,
        tickers: tuple[str, ...],
        years: tuple[int, ...],
        metrics: tuple[str, ...],
        items: tuple[str, ...],
    ) -> QueryType:
        if _ADVICE.search(query):
            return QueryType.OUT_OF_SCOPE
        if _CHANGE.search(query) and ("1A" in items or re.search(r"\brisks?\b", query, re.I)):
            return QueryType.CHANGE_DETECTION
        if len(tickers) >= 2 or (_COMPARE.search(query) and (tickers or metrics)):
            return QueryType.COMPARISON
        if _WHY.search(query):
            return QueryType.QUALITATIVE
        if _TREND.search(query) or (len(years) >= 2 and metrics):
            return QueryType.TREND
        if any(m in RATIO_METRICS for m in metrics):
            return QueryType.COMPUTED_METRIC
        if metrics and (years or re.match(r"\s*(?:what|how much)\b", query, re.IGNORECASE)):
            return QueryType.NUMERIC
        if _QUALITATIVE.search(query):
            return QueryType.QUALITATIVE
        return QueryType.FACT_LOOKUP

    def analyze(self, query: str) -> QueryAnalysis:
        tickers = self.tickers(query)
        years = self.fiscal_years(query)
        metrics = self.metrics(query)
        items = self.items(query)
        return QueryAnalysis(
            query=query,
            query_type=self.classify(query, tickers, years, metrics, items),
            tickers=tickers,
            fiscal_years=years,
            forms=self.forms(query),
            items=items,
            metrics=metrics,
        )


def to_filters(analysis: QueryAnalysis, *, use_item_hints: bool = False) -> RetrievalFilters:
    """Turn an analysis into retrieval pre-filters (item hints only if explicitly requested)."""
    return RetrievalFilters(
        tickers=analysis.tickers,
        fiscal_years=analysis.fiscal_years,
        forms=analysis.forms,
        items=analysis.items if use_item_hints else (),
    )
