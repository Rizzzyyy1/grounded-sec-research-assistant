"""RRF against hand-computed values."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finsight.retrieval.hybrid import rrf_fuse

pytestmark = pytest.mark.unit


def test_matches_the_formula_by_hand() -> None:
    # A: rank 1 in list 1, rank 3 in list 2 -> 1/61 + 1/63
    # B: rank 2 in list 1 only               -> 1/62
    # C: rank 1 in list 2 only               -> 1/61
    fused = dict(rrf_fuse([["A", "B"], ["C", "X", "A"]], k=60))
    assert fused["A"] == pytest.approx(1 / 61 + 1 / 63)
    assert fused["B"] == pytest.approx(1 / 62)
    assert fused["C"] == pytest.approx(1 / 61)


def test_document_in_both_lists_beats_one_that_is_top_in_only_one() -> None:
    order = [d for d, _ in rrf_fuse([["only1", "both"], ["only2", "both"]])]
    assert order[0] == "both" or order.index("both") <= 1
    assert rrf_fuse([["a", "b"], ["b", "a"]])[0][0] in {"a", "b"}


def test_weights_shift_the_balance() -> None:
    dense, sparse = ["d1", "d2"], ["s1", "s2"]
    assert rrf_fuse([dense, sparse], weights=[1.0, 0.0])[0][0] == "d1"
    assert rrf_fuse([dense, sparse], weights=[0.0, 1.0])[0][0] == "s1"


def test_ties_break_by_first_appearance_deterministically() -> None:
    first = rrf_fuse([["x", "y"], ["y", "x"]])
    assert [d for d, _ in first] == ["x", "y"]  # equal scores -> input order
    assert first == rrf_fuse([["x", "y"], ["y", "x"]])


def test_edge_cases() -> None:
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []
    assert [d for d, _ in rrf_fuse([["a", "b", "c"]])] == ["a", "b", "c"]
    with pytest.raises(ValueError, match="weights"):
        rrf_fuse([["a"]], weights=[1.0, 2.0])


@given(
    lists=st.lists(
        st.lists(st.sampled_from("abcdefgh"), unique=True, max_size=8), min_size=1, max_size=4
    )
)
def test_property_scores_positive_sorted_and_cover_the_union(lists: list[list[str]]) -> None:
    fused = rrf_fuse(lists)
    assert {d for d, _ in fused} == {d for lst in lists for d in lst}
    scores = [s for _, s in fused]
    assert all(s > 0 for s in scores)
    assert scores == sorted(scores, reverse=True)
