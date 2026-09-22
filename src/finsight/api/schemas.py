"""API request/response models (kept separate from the domain models on purpose).

Domain objects (``Answer``, ``Citation``) are reused where the shape is genuinely the same; the
wrappers add what only an HTTP client needs: validation limits, the not-advice disclaimer and
stable envelope fields.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finsight.core.schemas import Answer

DISCLAIMER = (
    "FinSight provides information drawn from public SEC filings, not investment advice. "
    "Verify figures against the cited source."
)
Mode = Literal["auto", "router", "rag", "agent"]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(
        min_length=3, max_length=2000, examples=["What was Apple's revenue in fiscal 2024?"]
    )
    mode: Mode = Field(
        default="auto",
        description="auto = Claude agent when credentials exist, otherwise the deterministic tool router; "
        "router = XBRL tools, no LLM; rag = single-shot retrieval; agent = Claude with tools.",
    )


class QueryResponse(_Out):
    answer: Answer
    mode: str
    disclaimer: str = DISCLAIMER


class CompanyOut(_Out):
    ticker: str
    name: str
    sector: str
    fiscal_year_end: str
    known_gaps: dict[str, str]


class MetricPoint(_Out):
    fiscal_year: int
    fiscal_period: str
    value: float
    unit: str
    period_end: str
    xbrl_tag: str
    derived: bool


class FinancialsResponse(_Out):
    ticker: str
    metrics: dict[str, list[MetricPoint]]


class RatioPoint(_Out):
    fiscal_year: int
    value: float
    formatted: str


class RatioSeries(_Out):
    label: str
    formula: str
    points: list[RatioPoint]
    skipped: dict[int, str] = Field(
        default_factory=dict, description="fiscal year -> why it could not be computed"
    )


class RatiosResponse(_Out):
    ticker: str
    ratios: dict[str, RatioSeries]


class PassageOut(_Out):
    chunk_id: str
    ticker: str
    form: str
    fiscal_year: int
    item: str
    item_title: str
    source_url: str
    text: str


class HealthOut(_Out):
    status: Literal["ok"] = "ok"
    version: str


class ReadyOut(_Out):
    status: Literal["ready", "degraded"]
    index_chunks: int
    embedding_model: str
    companies_with_facts: int
    llm_credentials: bool
    #: Which LLM is actually serving `mode=agent` ("claude", "ollama (<model>)"), or "none: <why>"
    #: when none is - see `Settings.llm_provider` / `finsight serve --llm`.
    llm_provider: str
    default_mode: str


class ErrorOut(_Out):
    error: str
    detail: str
    request_id: str | None = None
