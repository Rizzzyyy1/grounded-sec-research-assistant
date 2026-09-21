"""LLM-as-judge for what rules cannot score: faithfulness and free-text correctness.

Controls (docs/EVALUATION.md §4): the judge model is configured separately from the generator
(``llm.judge_model``); the judge sees only the *cited context*, so unfaithfulness is separated
from retrieval misses; prompts are versioned; and judge agreement with human labels is measured
on a subset (``stats.cohens_kappa``) instead of being assumed.

Requires a Claude API key. Everything here is tested against a scripted fake judge.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from finsight.generation.llm import LLMClient

JUDGE_PROMPT_VERSION = "judge-v1"
Verdict = Literal["supported", "unsupported", "contradicted"]

_SYSTEM = """\
You are a strict evaluator of answers written for financial analysts. You are given source \
excerpts and an answer. Break the answer into its individual factual claims. For each claim, \
decide whether the sources support it, do not support it, or contradict it. Judge only against \
the sources provided; do not use outside knowledge, and do not reward fluent wording.

Reply with JSON only, in this exact shape:
{"claims": [{"claim": "<text>", "verdict": "supported" | "unsupported" | "contradicted"}]}\
"""
_JSON = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class FaithfulnessResult:
    claims: tuple[tuple[str, Verdict], ...]

    @property
    def score(self) -> float:
        """Share of claims supported; an answer with no claims (e.g. an abstention) scores 1.0."""
        if not self.claims:
            return 1.0
        return sum(v == "supported" for _c, v in self.claims) / len(self.claims)

    @property
    def unsupported(self) -> list[str]:
        return [c for c, v in self.claims if v != "supported"]


def parse_verdicts(text: str) -> FaithfulnessResult:
    match = _JSON.search(text)
    if not match:
        raise ValueError("judge did not return JSON")
    payload = json.loads(match.group(0))
    claims: list[tuple[str, Verdict]] = []
    for item in payload.get("claims", []):
        verdict = str(item.get("verdict", "")).lower()
        if verdict not in {"supported", "unsupported", "contradicted"}:
            raise ValueError(f"unknown verdict {verdict!r}")
        claims.append((str(item.get("claim", "")), verdict))  # type: ignore[arg-type]
    return FaithfulnessResult(tuple(claims))


class LLMJudge:
    def __init__(self, llm: LLMClient, model: str | None = None) -> None:
        self._llm = llm
        self._model = model

    def faithfulness(self, answer: str, sources: str) -> FaithfulnessResult:
        result = self._llm.complete(
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"{sources}\n\nAnswer to evaluate:\n{answer}"}],
            model=self._model,
        )
        return parse_verdicts(result.text)
