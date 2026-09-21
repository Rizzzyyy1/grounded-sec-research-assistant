"""Tone analysis of filing text with a finance lexicon.

Generic sentiment tools mislabel finance ("liability", "tax", "cost" are neutral there, not
negative), which is why Loughran & McDonald (2011) built a finance-specific dictionary.

**This module ships only a small illustrative seed list** of clearly-finance terms so the
pipeline works out of the box; it is *not* the full Loughran-McDonald master dictionary, whose
licence terms require a separate download. Pass ``lexicon=load_lexicon(path)`` with the official
CSV to use the real thing. Scores are per 1,000 words so filings of different length compare.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_WORD = re.compile(r"[a-z]+")

SEED_LEXICON: dict[str, frozenset[str]] = {
    "negative": frozenset(
        {"loss", "losses", "decline", "declined", "adverse", "adversely", "impairment", "litigation",
         "failure", "failed", "weak", "weakness", "decrease", "decreased", "harm", "harmed", "risk",
         "risks", "default", "terminate", "terminated", "restructuring", "downturn", "shortage",
         "disruption", "disruptions", "unfavorable", "deteriorate", "deterioration", "difficult"}
    ),
    "positive": frozenset(
        {"gain", "gains", "improve", "improved", "improvement", "strong", "strength", "growth",
         "profitable", "profitability", "opportunity", "opportunities", "success", "successful",
         "increase", "increased", "favorable", "outperform", "achieve", "achieved", "innovation"}
    ),
    "uncertainty": frozenset(
        {"may", "might", "could", "uncertain", "uncertainty", "uncertainties", "approximately",
         "depend", "depends", "unpredictable", "believe", "anticipate", "possible", "possibly"}
    ),
    "litigious": frozenset(
        {"lawsuit", "lawsuits", "litigation", "plaintiff", "plaintiffs", "defendant", "court",
         "allege", "alleged", "allegations", "settlement", "injunction", "claims", "proceedings"}
    ),
}  # fmt: skip


@dataclass(frozen=True)
class Tone:
    words: int
    per_1000: dict[str, float]

    @property
    def net_tone(self) -> float:
        """(positive - negative) / (positive + negative); in [-1, 1], 0 when neither occurs."""
        pos, neg = self.per_1000.get("positive", 0.0), self.per_1000.get("negative", 0.0)
        return (pos - neg) / (pos + neg) if pos + neg else 0.0


def load_lexicon(path: Path) -> dict[str, frozenset[str]]:
    """Load the official Loughran-McDonald CSV (columns: Word, Negative, Positive, Uncertainty, Litigious)."""
    columns = {
        "negative": "Negative",
        "positive": "Positive",
        "uncertainty": "Uncertainty",
        "litigious": "Litigious",
    }
    out: dict[str, set[str]] = {k: set() for k in columns}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for key, col in columns.items():
                if row.get(col) and row[col] != "0":
                    out[key].add(row["Word"].lower())
    return {k: frozenset(v) for k, v in out.items()}


def tone(text: str, lexicon: Mapping[str, frozenset[str]] = SEED_LEXICON) -> Tone:
    words = _WORD.findall(text.lower())
    n = len(words)
    if n == 0:
        return Tone(0, dict.fromkeys(lexicon, 0.0))
    return Tone(n, {k: 1000.0 * sum(w in terms for w in words) / n for k, terms in lexicon.items()})
