"""Corpus processing: downloaded filings -> sections -> chunks -> ``chunks.parquet``.

``chunks.parquet`` is the *corpus of record*: every downstream index (vector, BM25) is derived
from it and can be rebuilt at any time. Per-filing statistics (which Items were found, how many
chunks) are written next to it so section-detection quality is measurable, not assumed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from finsight.config.settings import ChunkingSettings
from finsight.core.exceptions import ParsingError
from finsight.core.logging import get_logger
from finsight.core.schemas import (
    Chunk,
    ChunkMetadata,
    ChunkType,
    FilingRef,
    FiscalPeriod,
    FormType,
)
from finsight.processing.chunking import chunk_section
from finsight.processing.html_parser import parse_filing_html
from finsight.processing.sections import CORE_ITEMS, UNKNOWN_ITEM, split_sections

log = get_logger(__name__)


@dataclass(frozen=True)
class FilingStats:
    accession: str
    ticker: str
    fiscal_year: int
    n_blocks: int
    n_tables: int
    n_chunks: int
    n_table_chunks: int
    items: dict[str, int]  # item -> characters

    @property
    def core_items_missing(self) -> list[str]:
        return [i for i in CORE_ITEMS if i not in self.items]

    @property
    def structured(self) -> bool:
        return UNKNOWN_ITEM not in self.items


@dataclass
class CorpusReport:
    chunks_path: Path
    n_chunks: int = 0
    stats: list[FilingStats] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def core_detection_rate(self) -> float:
        if not self.stats:
            return 0.0
        return sum(1 for s in self.stats if not s.core_items_missing) / len(self.stats)


def process_filing(
    ref: FilingRef, raw: bytes, cfg: ChunkingSettings
) -> tuple[list[Chunk], FilingStats]:
    blocks = parse_filing_html(raw)
    if not blocks:
        raise ParsingError(f"{ref.label}: no visible text found")
    sections = split_sections(blocks)
    chunks = [c for section in sections for c in chunk_section(section, ref, cfg)]
    stats = FilingStats(
        accession=ref.accession,
        ticker=ref.ticker,
        fiscal_year=ref.fiscal_year,
        n_blocks=len(blocks),
        n_tables=sum(b.is_table for b in blocks),
        n_chunks=len(chunks),
        n_table_chunks=sum(c.metadata.chunk_type is ChunkType.TABLE for c in chunks),
        items={s.item: sum(len(b.text) for b in s.blocks) for s in sections},
    )
    return chunks, stats


# --------------------------------------------------------------------------- parquet I/O
def _chunk_record(c: Chunk) -> dict[str, Any]:
    m = c.metadata
    return {
        "id": c.id, "text": c.text, "embed_text": c.embed_text,
        "ticker": m.ticker, "cik": m.cik, "company": m.company, "form": m.form.value,
        "fiscal_year": m.fiscal_year, "fiscal_period": m.fiscal_period.value,
        "accession": m.accession, "item": m.item, "item_title": m.item_title,
        "chunk_type": m.chunk_type.value, "filed": m.filed, "source_url": m.source_url,
        "ordinal": m.ordinal, "token_count": m.token_count,
    }  # fmt: skip


def write_chunks(chunks: list[Chunk], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist([_chunk_record(c) for c in chunks])
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)


def read_chunks(path: Path) -> list[Chunk]:
    out: list[Chunk] = []
    for r in pq.read_table(path).to_pylist():
        filed: date = r["filed"]
        meta = ChunkMetadata(
            ticker=r["ticker"], cik=r["cik"], company=r["company"], form=FormType(r["form"]),
            fiscal_year=r["fiscal_year"], fiscal_period=FiscalPeriod(r["fiscal_period"]),
            accession=r["accession"], item=r["item"], item_title=r["item_title"],
            chunk_type=ChunkType(r["chunk_type"]), filed=filed, source_url=r["source_url"],
            ordinal=r["ordinal"], token_count=r["token_count"],
        )  # fmt: skip
        out.append(Chunk(id=r["id"], text=r["text"], embed_text=r["embed_text"], metadata=meta))
    return out


# --------------------------------------------------------------------------- corpus
def ref_from_row(row: Any) -> FilingRef:
    """Rebuild a FilingRef from a row of the DuckDB ``filings`` catalogue."""
    return FilingRef(
        cik=row.cik, ticker=row.ticker, company=row.company, form=FormType(row.form),
        accession=row.accession, filed=_as_date(row.filed),
        period_of_report=_as_date(row.period_of_report), fiscal_year=int(row.fiscal_year),
        fiscal_period=FiscalPeriod(row.fiscal_period), primary_doc=row.primary_doc, url=row.url,
    )  # fmt: skip


def _as_date(value: Any) -> date:
    converted: date = value.date() if hasattr(value, "date") else value
    return converted


def process_corpus(
    catalogue: Any,
    cfg: ChunkingSettings,
    out_path: Path,
    *,
    on_progress: Callable[[str], None] = lambda _m: None,
) -> CorpusReport:
    """``catalogue`` is the filings DataFrame (rows with ``local_path``)."""
    report = CorpusReport(chunks_path=out_path)
    all_chunks: list[Chunk] = []
    for row in catalogue.itertuples():
        if not row.local_path:
            continue
        ref = ref_from_row(row)
        try:
            chunks, stats = process_filing(ref, Path(row.local_path).read_bytes(), cfg)
        except (ParsingError, OSError) as exc:
            report.failures.append((ref.label, str(exc)))
            log.error("process.filing_failed", filing=ref.label, error=str(exc))
            continue
        all_chunks.extend(chunks)
        report.stats.append(stats)
        on_progress(f"{ref.label}: {stats.n_chunks} chunks, items={len(stats.items)}")
    write_chunks(all_chunks, out_path)
    report.n_chunks = len(all_chunks)
    return report
