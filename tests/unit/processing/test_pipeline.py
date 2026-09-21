"""process_filing / parquet round-trip / corpus processing."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from finsight.config.settings import ChunkingSettings
from finsight.core.exceptions import ParsingError
from finsight.core.schemas import ChunkType, FilingRef, FiscalPeriod, FormType
from finsight.processing.pipeline import (
    process_corpus,
    process_filing,
    read_chunks,
    ref_from_row,
    write_chunks,
)

pytestmark = pytest.mark.unit

REF = FilingRef(
    cik="0000320193", ticker="AAPL", company="Apple Inc.", form=FormType.TEN_K,
    accession="0000320193-24-000123", filed=date(2024, 11, 1), period_of_report=date(2024, 9, 28),
    fiscal_year=2024, fiscal_period=FiscalPeriod.FY, primary_doc="a.htm", url="https://x/a.htm",
)  # fmt: skip
CFG = ChunkingSettings(target_tokens=80, min_tokens=10)

PROSE = " ".join(f"Sentence number {i} about the business and its risks." for i in range(30))
DOC = f"""<html><body>
<ix:header><ix:hidden>hidden junk</ix:hidden></ix:header>
<div>Table of Contents</div>
<table><tr><td>Item 1</td><td>Business</td><td>3</td></tr>
<tr><td>Item 1A</td><td>Risk Factors</td><td>9</td></tr>
<tr><td>Item 7</td><td>MD&amp;A</td><td>30</td></tr></table>
<div><span>Item 1. Business</span></div><div>{PROSE}</div>
<div><span>Item 1A. Risk Factors</span></div><div>{PROSE}</div><div>{PROSE}</div>
<div><span>Item 7. Management's Discussion and Analysis</span></div><div>{PROSE}</div>
<table><tr><td>Net sales</td><td>$</td><td>391,035</td></tr>
<tr><td>Cost</td><td>$</td><td>210,352</td></tr></table>
<div><span>Item 8. Financial Statements</span></div><div>{PROSE}</div>
</body></html>""".encode()


def test_process_filing_produces_items_chunks_and_stats() -> None:
    chunks, stats = process_filing(REF, DOC, CFG)
    assert set(stats.items) == {"1", "1A", "7", "8"}
    assert stats.n_chunks == len(chunks) > 4
    assert stats.n_tables == 2  # the TOC and the financial table
    assert stats.n_table_chunks == 1  # the TOC belongs to no section, so it is not chunked
    assert stats.structured
    assert stats.core_items_missing == ["7A"]
    assert not any("hidden junk" in c.text for c in chunks)
    assert {c.metadata.item for c in chunks} == {"1", "1A", "7", "8"}
    assert all(c.metadata.accession == REF.accession for c in chunks)
    assert any(c.metadata.chunk_type is ChunkType.TABLE and "391,035" in c.text for c in chunks)


def test_filing_without_text_raises() -> None:
    with pytest.raises(ParsingError, match="no visible text"):
        process_filing(REF, b"<html><body><ix:header>x</ix:header></body></html>", CFG)


def test_parquet_roundtrip_preserves_everything(tmp_path: Path) -> None:
    chunks, _ = process_filing(REF, DOC, CFG)
    path = tmp_path / "out" / "chunks.parquet"
    write_chunks(chunks, path)
    assert read_chunks(path) == chunks
    assert not list(path.parent.glob("*.tmp"))  # atomic write leaves no temp file


def _catalogue(tmp_path: Path, *, html: bytes | None, exists: bool = True) -> pd.DataFrame:
    path = tmp_path / "a.htm"
    if exists and html is not None:
        path.write_bytes(html)
    return pd.DataFrame(
        [
            {
                "ticker": "AAPL",
                "cik": REF.cik,
                "company": REF.company,
                "form": "10-K",
                "accession": REF.accession,
                "filed": pd.Timestamp("2024-11-01"),
                "period_of_report": pd.Timestamp("2024-09-28"),
                "fiscal_year": 2024,
                "fiscal_period": "FY",
                "primary_doc": "a.htm",
                "url": REF.url,
                "local_path": str(path),
            }
        ]
    )


def test_ref_from_row_rebuilds_a_filing_ref(tmp_path: Path) -> None:
    row = next(_catalogue(tmp_path, html=DOC).itertuples())
    assert ref_from_row(row) == REF


def test_process_corpus_writes_parquet_and_reports_detection(tmp_path: Path) -> None:
    out = tmp_path / "chunks.parquet"
    messages: list[str] = []
    report = process_corpus(_catalogue(tmp_path, html=DOC), CFG, out, on_progress=messages.append)
    assert report.n_chunks == len(read_chunks(out)) > 0
    assert len(report.stats) == 1
    assert report.core_detection_rate == 0.0  # 7A is absent in the synthetic doc
    assert report.failures == []
    assert messages
    assert not report.failures


def test_unparseable_or_missing_filings_are_reported_not_fatal(tmp_path: Path) -> None:
    empty = process_corpus(_catalogue(tmp_path, html=b""), CFG, tmp_path / "c1.parquet")
    assert len(empty.failures) == 1
    assert "AAPL 10-K FY2024" in empty.failures[0][0]
    missing = process_corpus(
        _catalogue(tmp_path, html=None, exists=False), CFG, tmp_path / "c2.parquet"
    )
    assert len(missing.failures) == 1


def test_rows_without_a_local_path_are_skipped(tmp_path: Path) -> None:
    catalogue = _catalogue(tmp_path, html=DOC)
    catalogue["local_path"] = None
    report = process_corpus(catalogue, CFG, tmp_path / "c.parquet")
    assert (report.n_chunks, report.stats, report.failures) == (0, [], [])
