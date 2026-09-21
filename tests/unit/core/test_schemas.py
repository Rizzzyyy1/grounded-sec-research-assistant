"""Domain schema invariants."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from finsight.core.schemas import (
    Answer,
    Chunk,
    ChunkMetadata,
    FilingRef,
    FinancialFact,
    FiscalPeriod,
    FormType,
    Usage,
)

pytestmark = pytest.mark.unit


def _filing(**over: object) -> FilingRef:
    base: dict[str, object] = dict(
        cik="0000320193",
        ticker="AAPL",
        company="Apple Inc.",
        form=FormType.TEN_K,
        accession="0000320193-24-000123",
        filed=date(2024, 11, 1),
        period_of_report=date(2024, 9, 28),
        fiscal_year=2024,
        fiscal_period=FiscalPeriod.FY,
        primary_doc="aapl-20240928.htm",
        url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
    )
    base.update(over)
    return FilingRef(**base)  # type: ignore[arg-type]


def _meta(**over: object) -> ChunkMetadata:
    base: dict[str, object] = dict(
        ticker="AAPL",
        cik="0000320193",
        company="Apple Inc.",
        form=FormType.TEN_K,
        fiscal_year=2024,
        fiscal_period=FiscalPeriod.FY,
        accession="0000320193-24-000123",
        item="1A",
        filed=date(2024, 11, 1),
        source_url="https://example.com",
        ordinal=0,
        token_count=12,
    )
    base.update(over)
    return ChunkMetadata(**base)  # type: ignore[arg-type]


def test_filing_label() -> None:
    assert _filing().label == "AAPL 10-K FY2024"
    q = _filing(form=FormType.TEN_Q, fiscal_period=FiscalPeriod.Q2)
    assert q.label == "AAPL 10-Q FY2024 Q2"


@pytest.mark.parametrize("cik", ["320193", "abc", "00003201930"])
def test_cik_must_be_ten_digits(cik: str) -> None:
    with pytest.raises(ValidationError):
        _filing(cik=cik)


def test_accession_format_enforced() -> None:
    with pytest.raises(ValidationError):
        _filing(accession="000032019324000123")


def test_models_are_frozen_and_hashable_by_value() -> None:
    f = _filing()
    with pytest.raises(ValidationError):
        f.ticker = "MSFT"  # type: ignore[misc]


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        _filing(surprise="x")


def test_chunk_id_is_deterministic_and_sensitive() -> None:
    a = Chunk.make_id("0000320193-24-000123", "1A", 0, "hello")
    assert a == Chunk.make_id("0000320193-24-000123", "1A", 0, "hello")
    assert a != Chunk.make_id("0000320193-24-000123", "1A", 1, "hello")
    assert a != Chunk.make_id("0000320193-24-000123", "1A", 0, "hello!")
    assert len(a) == 20


def test_chunk_indexed_text_prefers_embed_text() -> None:
    plain = Chunk(id="x", text="body", metadata=_meta())
    assert plain.indexed_text == "body"
    enriched = Chunk(id="x", text="body", embed_text="AAPL | 10-K\nbody", metadata=_meta())
    assert enriched.indexed_text.startswith("AAPL")
    assert enriched.text == "body"  # the LLM still reads the unadorned text


def test_empty_chunk_text_rejected() -> None:
    with pytest.raises(ValidationError):
        Chunk(id="x", text="", metadata=_meta())


def _fact(**over: object) -> FinancialFact:
    base: dict[str, object] = dict(
        ticker="AAPL",
        cik="0000320193",
        metric="revenue",
        tag="RevenueFromContractWithCustomerExcludingAssessedTax",
        value=391_035_000_000.0,
        unit="USD",
        period_type="duration",
        start=date(2023, 10, 1),
        end=date(2024, 9, 28),
        fiscal_year=2024,
        fiscal_period=FiscalPeriod.FY,
        form=FormType.TEN_K,
        filed=date(2024, 11, 1),
        accession="0000320193-24-000123",
    )
    base.update(over)
    return FinancialFact(**base)  # type: ignore[arg-type]


def test_duration_fact_requires_start() -> None:
    with pytest.raises(ValidationError, match="start"):
        _fact(start=None)


def test_instant_fact_must_not_have_start() -> None:
    with pytest.raises(ValidationError, match="instant"):
        _fact(period_type="instant")


def test_start_after_end_rejected() -> None:
    with pytest.raises(ValidationError, match="after"):
        _fact(start=date(2025, 1, 1))


def test_duration_days_is_computed() -> None:
    assert _fact().duration_days == 363


def test_usage_addition() -> None:
    total = Usage(input_tokens=10, output_tokens=5, cost_usd=0.01) + Usage(
        input_tokens=1, output_tokens=2, cache_read_tokens=7, cost_usd=0.02
    )
    assert (total.input_tokens, total.output_tokens, total.cache_read_tokens) == (11, 7, 7)
    assert total.cost_usd == pytest.approx(0.03)


def test_answer_grounding_flag() -> None:
    assert not Answer(question="q", text="a").is_grounded
    assert Answer(question="q", text="a", abstained=True).is_grounded
