"""Domain models shared by every layer.

These are the *contract* between ingestion, indexing, retrieval, generation and evaluation.
They are frozen (hashable, safe to cache and pass across threads) and strict about unknown
fields, so a typo in a key fails loudly instead of silently producing ``None``.

Data flow, in terms of these types::

    FilingRef ──parse──▶ Section ──chunk──▶ Chunk ──index──▶ (vector store, BM25)
                                                   ▲
    companyfacts JSON ──▶ FinancialFact            │ retrieve
                                                   ▼
    question ──▶ QueryAnalysis ──▶ RetrievedChunk[] ──generate──▶ Answer(Citation[])
"""

from __future__ import annotations

import hashlib
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- enums
class FormType(StrEnum):
    TEN_K = "10-K"
    TEN_Q = "10-Q"
    EIGHT_K = "8-K"


class FiscalPeriod(StrEnum):
    FY = "FY"
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"


class ChunkType(StrEnum):
    TEXT = "text"
    TABLE = "table"


class QueryType(StrEnum):
    """Question taxonomy. Drives routing *and* per-type evaluation breakdowns."""

    FACT_LOOKUP = "fact_lookup"  # "Who is Apple's auditor?"        -> text retrieval
    NUMERIC = "numeric"  # "What was NVDA revenue in FY2024?"        -> XBRL tool
    COMPUTED_METRIC = "computed_metric"  # "What was JPM's ROE?"     -> XBRL + calculator
    TREND = "trend"  # "How has MSFT operating margin changed?"      -> XBRL series (+ text for why)
    COMPARISON = "comparison"  # "Compare AMZN and WMT gross margin" -> multi-company
    QUALITATIVE = "qualitative"  # "What risks does TSLA cite?"      -> synthesis over text
    CHANGE_DETECTION = "change_detection"  # "What's new in XOM's risks vs last year?"
    OUT_OF_SCOPE = "out_of_scope"  # "Should I buy AAPL?"            -> decline / redirect


# --------------------------------------------------------------------------- filings & text
class FilingRef(_Model):
    """Pointer to one filing on EDGAR."""

    cik: str = Field(pattern=r"^\d{10}$", description="Zero-padded 10-digit CIK.")
    ticker: str
    company: str
    form: FormType
    accession: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    filed: date
    period_of_report: date
    fiscal_year: int = Field(ge=1993, le=2100)
    fiscal_period: FiscalPeriod
    primary_doc: str
    url: str

    @property
    def label(self) -> str:
        period = "" if self.fiscal_period is FiscalPeriod.FY else f" {self.fiscal_period.value}"
        return f"{self.ticker} {self.form.value} FY{self.fiscal_year}{period}"


class Section(_Model):
    """A canonical Item of a filing (e.g. Item 1A - Risk Factors)."""

    filing: FilingRef
    item: str = Field(description="Canonical item id such as '1', '1A', '7', '7A', '8'.")
    title: str
    text: str


class ChunkMetadata(_Model):
    """Everything we can filter or group by. Mirrors the vector-store payload."""

    ticker: str
    cik: str
    company: str
    form: FormType
    fiscal_year: int
    fiscal_period: FiscalPeriod
    accession: str
    item: str
    item_title: str = ""
    chunk_type: ChunkType = ChunkType.TEXT
    filed: date
    source_url: str
    ordinal: int = Field(ge=0, description="Position of the chunk within its section.")
    token_count: int = Field(ge=0)


class Chunk(_Model):
    """A retrievable unit of text.

    ``text`` is what the LLM reads. ``embed_text`` is what gets embedded / BM25-indexed; it is
    ``text`` prefixed by a contextual header when that feature is enabled.
    """

    id: str
    text: str = Field(min_length=1)
    embed_text: str | None = None
    metadata: ChunkMetadata

    @property
    def indexed_text(self) -> str:
        return self.embed_text if self.embed_text is not None else self.text

    @staticmethod
    def make_id(accession: str, item: str, ordinal: int, text: str) -> str:
        """Deterministic id: identical input always yields the same id (idempotent re-index)."""
        digest = hashlib.sha256(f"{accession}|{item}|{ordinal}|{text}".encode()).hexdigest()
        return digest[:20]


# --------------------------------------------------------------------------- retrieval
class RetrievedChunk(_Model):
    """A chunk plus the evidence of *why* it was retrieved (per-stage ranks and scores)."""

    chunk: Chunk
    score: float
    dense_rank: int | None = None
    sparse_rank: int | None = None
    dense_score: float | None = None
    sparse_score: float | None = None
    rerank_score: float | None = None


class QueryAnalysis(_Model):
    """Structured reading of the user's question."""

    query: str
    query_type: QueryType = QueryType.FACT_LOOKUP
    tickers: tuple[str, ...] = ()
    fiscal_years: tuple[int, ...] = ()
    forms: tuple[FormType, ...] = ()
    items: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()


# --------------------------------------------------------------------------- structured data
class FinancialFact(_Model):
    """One reported XBRL value, normalised.

    ``metric`` is our canonical name (``revenue``); ``tag`` is the raw us-gaap concept it came
    from, kept for auditability. Instant facts (balance sheet) have ``start=None``.
    """

    ticker: str
    cik: str
    metric: str
    tag: str
    value: float
    unit: str = Field(description="'USD', 'USD/shares', 'shares', 'pure' ...")
    period_type: Literal["duration", "instant"]
    start: date | None = None
    end: date
    fiscal_year: int
    fiscal_period: FiscalPeriod
    form: FormType
    filed: date
    accession: str
    derived: bool = Field(
        default=False,
        description="True when computed by us (e.g. Q4 = FY - 9M YTD) rather than reported.",
    )

    @model_validator(mode="after")
    def _check_period(self) -> FinancialFact:
        if self.period_type == "duration" and self.start is None:
            raise ValueError("duration facts require a start date")
        if self.period_type == "instant" and self.start is not None:
            raise ValueError("instant facts must not have a start date")
        if self.start is not None and self.start > self.end:
            raise ValueError("start must not be after end")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_days(self) -> int | None:
        return None if self.start is None else (self.end - self.start).days


# --------------------------------------------------------------------------- generation
class Citation(_Model):
    """A verified pointer from an answer sentence to a source passage."""

    source_id: str = Field(pattern=r"^S\d+$", description="Label used in the answer, e.g. 'S2'.")
    chunk_id: str
    ticker: str
    form: FormType
    fiscal_year: int
    item: str
    url: str
    quote: str = Field(description="The supporting passage, trimmed for display.")


class ToolCallRecord(_Model):
    name: str
    arguments: dict[str, object]
    result_summary: str
    latency_ms: float = Field(ge=0)
    is_error: bool = False


class Usage(_Model):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


class Answer(_Model):
    """Final response returned to the user (and stored per-example in evaluation runs)."""

    question: str
    text: str
    citations: tuple[Citation, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    query_type: QueryType | None = None
    abstained: bool = False
    abstain_reason: str | None = None
    model: str = ""
    prompt_version: str = ""
    trace_id: str = ""
    latency_ms: float = Field(default=0.0, ge=0)
    usage: Usage = Usage()
    warnings: tuple[str, ...] = Field(
        default=(), description="Validation findings: uncited claims, unverified numbers, ..."
    )

    @property
    def is_grounded(self) -> bool:
        """True when the answer either abstained or carries at least one citation/tool call."""
        return self.abstained or bool(self.citations) or bool(self.tool_calls)
