"""Statistics and the LLM judge."""

from __future__ import annotations

import numpy as np
import pytest

from finsight.core.schemas import Usage
from finsight.evaluation.judges import (
    JUDGE_PROMPT_VERSION,
    FaithfulnessResult,
    LLMJudge,
    parse_verdicts,
)
from finsight.evaluation.stats import (
    bootstrap_ci,
    cohens_kappa,
    mcnemar_exact,
    paired_bootstrap_diff,
)
from finsight.generation.llm import LLMResult

pytestmark = pytest.mark.unit


def test_bootstrap_ci_brackets_the_mean_and_narrows_with_more_data() -> None:
    rng = np.random.default_rng(0)
    small = bootstrap_ci(rng.binomial(1, 0.6, 30).tolist(), n_boot=2000)
    large = bootstrap_ci(rng.binomial(1, 0.6, 600).tolist(), n_boot=2000)
    assert small.lo <= small.mean <= small.hi
    assert (large.hi - large.lo) < (small.hi - small.lo)
    assert 0.5 < large.mean < 0.7


def test_bootstrap_is_deterministic_for_a_seed_and_handles_edges() -> None:
    v = [0, 1, 1, 0, 1]
    assert bootstrap_ci(v, seed=3) == bootstrap_ci(v, seed=3)
    assert all(np.isnan([bootstrap_ci([]).mean]))
    const = bootstrap_ci([1.0] * 10)
    assert (const.mean, const.lo, const.hi) == (1.0, 1.0, 1.0)


def test_paired_diff_detects_a_real_improvement_and_not_noise() -> None:
    better = [1.0] * 40 + [0.0] * 10
    worse = [1.0] * 25 + [0.0] * 25
    d = paired_bootstrap_diff(better, worse, n_boot=3000)
    assert d.mean == pytest.approx(0.3) and d.excludes_zero()
    same = paired_bootstrap_diff(better, better, n_boot=1000)
    assert same.mean == 0 and not same.excludes_zero()
    with pytest.raises(ValueError, match="equal length"):
        paired_bootstrap_diff([1], [1, 2])


def test_mcnemar_exact_known_values() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(5, 5) == 1.0
    assert mcnemar_exact(0, 5) == pytest.approx(2 * (1 / 32))  # 0.0625
    assert mcnemar_exact(10, 0) == pytest.approx(2 / 1024)
    assert mcnemar_exact(3, 1) == mcnemar_exact(1, 3)  # symmetric


def test_cohens_kappa() -> None:
    assert cohens_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    assert cohens_kappa([1, 1, 0, 0], [0, 0, 1, 1]) == -1.0
    assert cohens_kappa(["a", "b"], ["a", "a"]) == pytest.approx(0.0)
    with pytest.raises(ValueError, match="equal-length"):
        cohens_kappa([1], [1, 2])


# ------------------------------------------------------------------ judge
class ScriptedJudgeLLM:
    def __init__(self, text: str) -> None:
        self.text, self.calls = text, []

    def complete(self, *, system, messages, tools=None, model=None, max_tokens=None):  # type: ignore[no-untyped-def]
        self.calls.append({"system": system, "messages": messages, "model": model})
        return LLMResult(text=self.text, stop_reason="end_turn", usage=Usage(), model="judge")


def test_parse_verdicts_and_score() -> None:
    r = parse_verdicts(
        'Sure! {"claims": [{"claim": "a", "verdict": "supported"}, '
        '{"claim": "b", "verdict": "unsupported"}, {"claim": "c", "verdict": "contradicted"}, '
        '{"claim": "d", "verdict": "supported"}]} done'
    )
    assert r.score == pytest.approx(0.5)
    assert r.unsupported == ["b", "c"]


def test_no_claims_scores_one_and_bad_output_is_rejected() -> None:
    assert FaithfulnessResult(()).score == 1.0
    with pytest.raises(ValueError, match="JSON"):
        parse_verdicts("I think it is fine")
    with pytest.raises(ValueError, match="unknown verdict"):
        parse_verdicts('{"claims": [{"claim": "a", "verdict": "maybe"}]}')


def test_judge_uses_its_own_model_and_sees_sources_and_answer() -> None:
    fake = ScriptedJudgeLLM('{"claims": [{"claim": "x", "verdict": "supported"}]}')
    result = LLMJudge(fake, model="claude-sonnet-5").faithfulness(
        "The answer.", "<source>S</source>"
    )
    assert result.score == 1.0
    call = fake.calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert "<source>S</source>" in call["messages"][0]["content"]
    assert "The answer." in call["messages"][0]["content"]
    assert JUDGE_PROMPT_VERSION == "judge-v1"
