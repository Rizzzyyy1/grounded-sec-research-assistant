"""BM25 lexical index (bm25s) with finance-aware tokenisation and metadata pre-filtering.

Lexical search is what rescues exact figures, names and defined terms that dense models blur
("$391,035", "ASC 606", "10-K", a product name). The tokeniser therefore keeps numbers with
their separators, currency and percent signs, and hyphenated terms intact.

Filtering uses bm25s's ``weight_mask``: documents outside the filter score zero and are dropped
from the result, so a filter behaves as a true pre-filter.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from finsight.core.exceptions import IndexingError
from finsight.core.filters import RetrievalFilters
from finsight.core.schemas import Chunk
from finsight.indexing.vector_store import Hit

_TOKEN = re.compile(
    r"\d{1,3}-[a-z](?![a-z0-9])"  # form names: 10-K, 10-Q, 8-K
    r"|\$?\d[\d,]*(?:\.\d+)?%?"  # numbers keep $ , . %
    r"|[a-z][a-z0-9&'\-]*",  # words keep & ' -
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "is",
        "it", "its", "of", "on", "or", "that", "the", "this", "to", "was", "were", "will", "with",
        "which",
    }
)  # fmt: skip


def tokenize(text: str) -> list[str]:
    """Lower-cased tokens; numbers keep commas/decimals/$/%, hyphenated words stay whole."""
    tokens = (t.lower().strip("'-,.") for t in _TOKEN.findall(text))
    return [t for t in tokens if t and t not in _STOPWORDS]


class SparseIndex:
    def __init__(self) -> None:
        self._bm25: Any = None
        self._chunks: list[Chunk] = []
        self._masks: dict[str, np.ndarray[Any, Any]] = {}

    # ------------------------------------------------------------------ build / persist
    def build(self, chunks: Sequence[Chunk]) -> None:
        import bm25s  # noqa: PLC0415

        self._chunks = list(chunks)
        self._bm25 = bm25s.BM25()
        self._bm25.index([tokenize(c.indexed_text) for c in self._chunks], show_progress=False)
        self._build_field_arrays()

    def _build_field_arrays(self) -> None:
        metas = [c.metadata for c in self._chunks]
        self._masks = {
            "ticker": np.array([m.ticker for m in metas]),
            "fiscal_year": np.array([m.fiscal_year for m in metas]),
            "form": np.array([m.form.value for m in metas]),
            "item": np.array([m.item for m in metas]),
            "chunk_type": np.array([m.chunk_type.value for m in metas]),
            "fiscal_period": np.array([m.fiscal_period.value for m in metas]),
        }

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._bm25.save(str(directory))
        (directory / "ids.json").write_text(json.dumps([c.id for c in self._chunks]))

    @classmethod
    def load(cls, directory: Path, catalogue: Mapping[str, Chunk]) -> SparseIndex:
        import bm25s  # noqa: PLC0415

        try:
            ids: list[str] = json.loads((directory / "ids.json").read_text())
            missing = [i for i in ids if i not in catalogue]
        except FileNotFoundError as exc:
            raise IndexingError(f"no BM25 index at {directory}; run `finsight index`") from exc
        if missing:
            raise IndexingError(
                f"BM25 index references {len(missing)} chunks not in the corpus; rebuild the index"
            )
        index = cls()
        index._bm25 = bm25s.BM25.load(str(directory))
        index._chunks = [catalogue[i] for i in ids]
        index._build_field_arrays()
        return index

    # ------------------------------------------------------------------ search
    def _mask(self, filters: RetrievalFilters | None) -> np.ndarray[Any, Any] | None:
        if filters is None or filters.is_empty:
            return None
        mask: np.ndarray[Any, Any] = np.ones(len(self._chunks), dtype=bool)
        criteria: list[tuple[str, list[Any]]] = [
            ("ticker", list(filters.tickers)),
            ("fiscal_year", list(filters.fiscal_years)),
            ("form", [f.value for f in filters.forms]),
            ("item", list(filters.items)),
            ("chunk_type", [c.value for c in filters.chunk_types]),
            ("fiscal_period", [p.value for p in filters.fiscal_periods]),
        ]
        for key, values in criteria:
            if values:
                mask &= np.isin(self._masks[key], values)
        return mask

    def search(self, query: str, k: int, filters: RetrievalFilters | None = None) -> list[Hit]:
        if self._bm25 is None or not self._chunks:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        mask = self._mask(filters)
        if mask is not None and not mask.any():
            return []
        k = min(k, int(mask.sum()) if mask is not None else len(self._chunks))
        docs, scores = self._bm25.retrieve(
            [tokens],
            k=k,
            weight_mask=None if mask is None else mask.astype(np.float32),
            show_progress=False,
        )
        return [
            Hit(self._chunks[int(i)].id, float(s))
            for i, s in zip(docs[0], scores[0], strict=True)
            if s > 0  # zero = no query term matched (or masked out)
        ]

    def __len__(self) -> int:
        return len(self._chunks)
