"""Numeric consistency check: every figure in an answer must come from somewhere we can point to.

Finance's worst failure is a confidently wrong number. The prompt tells the model to copy figures
verbatim and never to calculate; this module *enforces* it after the fact. Each number in the
answer is compared with the numbers present in the cited sources (and any tool results); a figure
that appears nowhere is reported as unverified so the caller can flag or repair it.

Comparison is by normalised value (``$391,035`` == ``391035``; ``46.2%`` == ``46.2``), tolerant of
the small formatting differences a model may introduce, but deliberately *not* tolerant of
changed magnitudes: "$391.0 billion" against a source saying "$391,035 million" is flagged,
because a reader cannot verify it against the filing without doing the conversion themselves.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from finsight.generation.citations import strip_labels

_NUMBER = re.compile(r"(?<![\w.])\$?\(?\d[\d,]*(?:\.\d+)?\)?%?(?![\w])")
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
# Identifiers that merely *contain* digits: EDGAR accession numbers and form names.
_IDENTIFIER = re.compile(r"\b\d{10}-\d{2}-\d{6}\b|\b\d{1,2}-[KQ]\b", re.IGNORECASE)
_MIN_DIGITS = 2  # single digits ("3 segments") are too ambiguous to police


def normalise(token: str) -> str | None:
    """Canonical numeric string, or None when the token is not a checkable figure."""
    cleaned = token.replace("$", "").replace(",", "").replace("%", "").strip("()")
    if not cleaned or cleaned == ".":
        return None
    if _YEAR.match(cleaned):
        return None  # fiscal years are labels, not figures
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")  # 46.20 == 46.2, 2.0 == 2
    cleaned = cleaned.lstrip("0") or "0"
    # Count digits *after* normalising, so "2.0%" and "2%" are treated identically.
    if sum(ch.isdigit() for ch in cleaned) < _MIN_DIGITS:
        return None
    return cleaned


def figures_in(text: str) -> set[str]:
    return {
        n for t in _NUMBER.findall(_IDENTIFIER.sub(" ", text)) if (n := normalise(t)) is not None
    }


def unverified_numbers(answer: str, evidence: Iterable[str]) -> list[str]:
    """Figures stated in ``answer`` that occur in none of the ``evidence`` texts."""
    known: set[str] = set()
    for text in evidence:
        known |= figures_in(text)
    unverified: list[str] = []
    for token in _NUMBER.findall(_IDENTIFIER.sub(" ", strip_labels(answer))):
        n = normalise(token)
        if n is not None and n not in known and token not in unverified:
            unverified.append(token)
    return unverified
