"""Chunk enrichment: the contextual header that makes repetitive filings distinguishable.

Every 10-K says "gross margin decreased due to ..." in nearly the same words. Without context a
chunk from Apple's FY2023 MD&A and one from Walmart's FY2021 MD&A embed almost identically, so
dense retrieval cannot tell them apart. Prefixing the *indexed* text (never the text shown to the
LLM) with issuer, form, year and Item restores that signal at zero indexing cost. Whether it
actually helps is an ablation (docs/EVALUATION.md A4), not an assumption.
"""

from __future__ import annotations

from finsight.core.schemas import Chunk, ChunkMetadata, ChunkType


def context_header(meta: ChunkMetadata) -> str:
    period = "" if meta.fiscal_period.value == "FY" else f" {meta.fiscal_period.value}"
    where = f"Item {meta.item}" if meta.item != "UNK" else "Document"
    title = f" - {meta.item_title}" if meta.item_title else ""
    kind = " | Table" if meta.chunk_type is ChunkType.TABLE else ""
    filing = f"{meta.form.value} FY{meta.fiscal_year}{period}"
    return f"{meta.company} ({meta.ticker}) | {filing} | {where}{title}{kind}"


def add_context_header(chunk: Chunk) -> Chunk:
    """Return a copy whose ``embed_text`` is ``header + newline + text``."""
    return chunk.model_copy(
        update={"embed_text": f"{context_header(chunk.metadata)}\n{chunk.text}"}
    )
