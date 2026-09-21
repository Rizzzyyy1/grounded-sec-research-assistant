"""Gold datasets: schema, validation and JSONL I/O.

A gold example says what a correct answer looks like *without* an LLM being involved:

* ``expected.numeric`` - a value with a relative tolerance (numeric accuracy is the metric that
  matters most for a finance assistant, and it needs no judge);
* ``expected.answer_contains`` - names that must appear (e.g. the winner of a comparison);
* ``expected.abstain`` - the correct behaviour is to decline / say "not found";
* ``gold_sources`` - the filing sections that contain the evidence, so retrieval can be scored
  at section level with no LLM either.

Provenance is explicit on every record, because how a question was made determines what a score
on it means (see docs/EVALUATION.md).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finsight.core.exceptions import EvaluationError
from finsight.core.schemas import QueryType

Split = Literal["dev", "test"]
# "draft" = written by an LLM, structured answers derived from XBRL, sections NOT human-verified
Provenance = Literal["xbrl", "template", "adversarial", "draft", "human"]
Unit = Literal["usd", "ratio", "usd_per_share"]


class _G(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NumericExpectation(_G):
    value: float
    unit: Unit
    rel_tol: float = Field(gt=0, le=0.5)


class Expected(_G):
    numeric: NumericExpectation | None = None
    answer_contains: tuple[str, ...] = ()
    abstain: bool = False

    @model_validator(mode="after")
    def _abstain_excludes_content(self) -> Expected:
        if self.abstain and (self.numeric or self.answer_contains):
            raise ValueError("an abstain example cannot also expect content")
        return self


class GoldSource(_G):
    ticker: str
    fiscal_year: int
    item: str | None = Field(default=None, description="None = any section of that filing")


class GoldExample(_G):
    id: str
    split: Split
    type: QueryType
    question: str = Field(min_length=10)
    expected: Expected
    gold_sources: tuple[GoldSource, ...] = ()
    required_tools: tuple[str, ...] = ()
    provenance: Provenance
    notes: str = ""


def load_gold(path: Path, *, split: Split | None = None) -> list[GoldExample]:
    if not path.is_file():
        raise EvaluationError(f"gold file not found: {path} (run `finsight eval gold`)")
    examples: list[GoldExample] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            examples.append(GoldExample.model_validate_json(line))
        except ValueError as exc:
            raise EvaluationError(f"{path}:{n}: invalid gold example: {exc}") from exc
    ids = [e.id for e in examples]
    dupes = sorted(i for i, c in Counter(ids).items() if c > 1)
    if dupes:
        raise EvaluationError(f"duplicate gold ids: {dupes[:5]}")
    return [e for e in examples if split is None or e.split == split]


def write_gold(examples: list[GoldExample], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(json.loads(e.model_dump_json()), sort_keys=True) for e in examples]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize(examples: list[GoldExample]) -> dict[str, dict[str, int]]:
    """Counts by type and by split - shown whenever a dataset is built or loaded."""
    return {
        "by_type": dict(Counter(e.type.value for e in examples)),
        "by_split": dict(Counter(e.split for e in examples)),
        "by_provenance": dict(Counter(e.provenance for e in examples)),
    }
