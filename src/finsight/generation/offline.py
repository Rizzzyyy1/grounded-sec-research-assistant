"""Extractive backend: an :class:`LLMClient` that needs no API key.

It answers by *quoting* the source sentences that overlap most with the question, citing each.
That makes it useful in three ways: the whole pipeline (retrieval -> context -> citations ->
validation) runs end to end without credentials; it is a deterministic zero-cost test double;
and it is the honest **lower baseline** in evaluation - any generative model has to beat "just
quote the best passage" to justify its cost.

It never produces a number it did not copy from a source, so it trivially passes the numeric
consistency check; what it cannot do is synthesise, compare or calculate.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from typing import Any

from finsight.core.schemas import Usage
from finsight.generation.llm import LLMResult
from finsight.generation.prompts import ABSTAIN_TOKEN
from finsight.indexing.sparse_index import tokenize

_SOURCE = re.compile(r'<source id="(S\d+)"[^>]*type="(\w+)"[^>]*>\n(.*?)\n</source>', re.DOTALL)
_QUESTION = re.compile(r"Analyst question:\s*(.*)\Z", re.DOTALL)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_MAX_SENTENCES = 3


class ExtractiveLLM:
    name = "extractive-baseline"

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        prompt = str(messages[-1]["content"])
        q = _QUESTION.search(prompt)
        terms = set(tokenize(q.group(1) if q else prompt))
        scored: list[tuple[float, int, str, str]] = []  # (overlap, order, sentence, source id)
        order = 0
        for source_id, kind, body in _SOURCE.findall(prompt):
            text = html.unescape(body)
            units = (
                text.splitlines() if kind == "table" else _SENTENCE.split(" ".join(text.split()))
            )
            for unit in units:
                overlap = len(terms & set(tokenize(unit)))
                if overlap and len(unit) > 20:
                    scored.append((overlap / (len(terms) or 1), order, unit.strip(), source_id))
                    order += 1
        if not scored:
            text_out = f"{ABSTAIN_TOKEN}: none of the provided passages address the question."
        else:
            chosen: list[tuple[float, int, str, str]] = []
            seen: set[str] = set()
            # Chunk overlap repeats boundary sentences in neighbouring passages: quote each once.
            for item in sorted(scored, key=lambda t: (-t[0], t[1])):
                key = " ".join(item[2].lower().split())
                if key not in seen:
                    seen.add(key)
                    chosen.append(item)
                if len(chosen) == _MAX_SENTENCES:
                    break
            text_out = " ".join(f"{sentence} [{sid}]" for _s, _o, sentence, sid in chosen)
        return LLMResult(text=text_out, stop_reason="end_turn", usage=Usage(), model=self.name)
