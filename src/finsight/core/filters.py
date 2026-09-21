"""Backend-agnostic retrieval filters.

The same object drives the in-memory store, Qdrant payload conditions (see ``indexing``) and BM25
pre-filter masks, so a filter can never mean different things in different backends. Filters are
applied *before* top-k (pre-filtering): post-filtering can return nothing when the wanted
documents rank below the cut-off.
"""

from __future__ import annotations

from dataclasses import dataclass

from finsight.core.schemas import ChunkMetadata, ChunkType, FiscalPeriod, FormType


@dataclass(frozen=True)
class RetrievalFilters:
    tickers: tuple[str, ...] = ()
    fiscal_years: tuple[int, ...] = ()
    forms: tuple[FormType, ...] = ()
    items: tuple[str, ...] = ()
    chunk_types: tuple[ChunkType, ...] = ()
    fiscal_periods: tuple[FiscalPeriod, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.tickers, self.fiscal_years, self.forms, self.items, self.chunk_types,
             self.fiscal_periods)
        )  # fmt: skip

    def matches(self, meta: ChunkMetadata) -> bool:
        """Every non-empty field must match (fields are AND-ed, values within a field OR-ed)."""
        return (
            (not self.tickers or meta.ticker in self.tickers)
            and (not self.fiscal_years or meta.fiscal_year in self.fiscal_years)
            and (not self.forms or meta.form in self.forms)
            and (not self.items or meta.item in self.items)
            and (not self.chunk_types or meta.chunk_type in self.chunk_types)
            and (not self.fiscal_periods or meta.fiscal_period in self.fiscal_periods)
        )

    def describe(self) -> str:
        fields = (
            ("tickers", self.tickers),
            ("years", self.fiscal_years),
            ("forms", self.forms),
            ("items", self.items),
            ("types", self.chunk_types),
            ("periods", self.fiscal_periods),
        )
        parts = [f"{name}={[str(v) for v in values]}" for name, values in fields if values]
        return ", ".join(parts) or "none"
