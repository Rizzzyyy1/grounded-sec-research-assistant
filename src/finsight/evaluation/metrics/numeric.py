"""Numeric accuracy: does the answer state the expected figure?

The most important metric for a finance assistant, and it needs no LLM. It understands the ways
a figure is actually written: ``$391,035 million``, ``$391.0 billion``, ``46.2%``, ``46.2 percent``,
``0.462`` and bare ``391,035`` (financial statements are stated in millions, so for USD amounts
an unscaled number is also tried as millions).

An answer is *correct* if **any** number in it lies within the relative tolerance of the
expectation. That is deliberately generous about extra numbers (a good answer quotes context)
and strict about the target one; the ``answer_contains`` field handles "who" in comparisons.
"""

from __future__ import annotations

import re

from finsight.evaluation.datasets import NumericExpectation

_NUMBER = re.compile(
    r"(?P<neg>[-\u2212(])?\$?\s*(?P<num>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*"
    r"(?P<tail>%|percent|per cent|trillion|billion|million|thousand|bn|mm|m\b|b\b|k\b)?",
    re.IGNORECASE,
)
_SCALE = {
    "trillion": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mm": 1e6, "m": 1e6,
    "thousand": 1e3, "k": 1e3,
}  # fmt: skip
_YEARS = range(1990, 2036)


def extract_candidates(text: str, unit: str) -> list[float]:
    """Every value the text could be stating, given the expected unit."""
    out: list[float] = []
    for m in _NUMBER.finditer(text.replace("\u2009", " ")):
        raw = m.group("num").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        sign = -1.0 if m.group("neg") in {"-", "\u2212", "("} else 1.0
        tail = (m.group("tail") or "").lower()
        # A bare 4-digit number in the year range is a label ("fiscal 2024"), not a figure. Skipping
        # it here matters because the unscaled fallback below would otherwise read it as $2.024B.
        if _is_bare_year(m.group(0), m.group("num"), tail):
            continue
        if tail in {"%", "percent", "per cent"}:
            out.append(sign * value / 100)  # 46.2% -> 0.462
            out.append(sign * value)  # tolerate a ratio stated as a plain number
        elif tail in _SCALE:
            out.append(sign * value * _SCALE[tail])
        else:
            out.append(sign * value)
            if unit == "usd":
                out.append(sign * value * 1e6)  # statements are in millions: "391,035" alone
    return out


def _is_bare_year(whole: str, number: str, tail: str) -> bool:
    return (
        not tail
        and "$" not in whole
        and "," not in number
        and "." not in number
        and int(number) in _YEARS
    )


def relative_error(actual: float, expected: float) -> float:
    if expected == 0:
        return abs(actual)
    return abs(actual - expected) / abs(expected)


def numeric_match(text: str, expectation: NumericExpectation) -> bool:
    candidates = extract_candidates(text, expectation.unit)
    return any(relative_error(c, expectation.value) <= expectation.rel_tol for c in candidates)
