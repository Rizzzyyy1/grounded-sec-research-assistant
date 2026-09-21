"""The shipped YAML configs must always load and validate."""

from __future__ import annotations

from pathlib import Path

import pytest

from finsight.config.settings import RetrievalSettings
from finsight.config.universe import Universe, load_presets, load_universe
from finsight.core.exceptions import ConfigError
from finsight.ingestion.xbrl.concepts import CANONICAL_METRICS

pytestmark = pytest.mark.unit


def test_shipped_universe_is_valid(repo_root: Path) -> None:
    u = load_universe(repo_root / "configs" / "universe.yaml")
    assert len(u.companies) == 12
    assert u.fiscal_years == (2021, 2022, 2023, 2024, 2025)
    assert len(set(u.tickers)) == 12
    # deliberate diversity: several fiscal year ends and sectors
    assert len({c.fiscal_year_end for c in u.companies}) >= 4
    assert len({c.sector for c in u.companies}) >= 6


def test_alias_table_resolves_common_names(repo_root: Path) -> None:
    table = load_universe(repo_root / "configs" / "universe.yaml").alias_table()
    assert table["apple"] == "AAPL"
    assert table["google"] == "GOOGL"
    assert table["jpmorgan"] == "JPM"
    assert table["j&j"] == "JNJ"


def test_company_lookup_is_case_insensitive_on_ticker(repo_root: Path) -> None:
    u = load_universe(repo_root / "configs" / "universe.yaml")
    assert u.company("aapl").name == "Apple Inc."
    with pytest.raises(KeyError):
        u.company("ZZZZ")


def test_duplicate_tickers_rejected() -> None:
    c = {"ticker": "AAPL", "name": "Apple", "sector": "IT", "fiscal_year_end": "09-30"}
    with pytest.raises(ValueError, match="duplicate"):
        Universe.model_validate({"name": "x", "fiscal_years": [2024], "companies": [c, c]})


def test_shipped_retrieval_presets_are_valid(repo_root: Path) -> None:
    presets = load_presets(repo_root / "configs" / "retrieval.yaml", RetrievalSettings)
    assert {"dense_only", "bm25_only", "hybrid", "hybrid_rerank"} <= set(presets)
    assert presets["dense_only"].mode == "dense"
    assert presets["hybrid_rerank"].rerank is True


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_universe(tmp_path / "nope.yaml")


def test_bad_preset_key_fails_loudly(tmp_path: Path) -> None:
    f = tmp_path / "p.yaml"
    f.write_text("presets:\n  bad: {modee: hybrid}\n")
    with pytest.raises(ValueError, match="modee"):
        load_presets(f, RetrievalSettings)


def test_shipped_known_gaps_reference_real_metrics_with_reasons(repo_root: Path) -> None:
    """A typo'd metric name would make a declared gap silently do nothing."""
    universe = load_universe(repo_root / "configs" / "universe.yaml")
    for company in universe.companies:
        for metric, reason in company.known_gaps.items():
            assert metric in CANONICAL_METRICS, f"{company.ticker}: unknown metric {metric!r}"
            assert len(reason) > 15, f"{company.ticker}/{metric}: give a real reason"


def test_xom_is_pinned_to_the_predecessor_cik(repo_root: Path) -> None:
    """After the 2026 holding-company reorganisation the XOM ticker maps to a new CIK with no
    10-K history; the universe must pin the CIK that actually holds the annual reports."""
    universe = load_universe(repo_root / "configs" / "universe.yaml")
    assert universe.company("XOM").cik == "0000034088"
    assert all(c.cik is None for c in universe.companies if c.ticker != "XOM")


def test_cik_override_must_be_ten_digits() -> None:
    bad = {
        "ticker": "AAPL",
        "name": "A",
        "sector": "IT",
        "fiscal_year_end": "09-30",
        "cik": "34088",
    }
    with pytest.raises(ValueError, match="cik"):
        Universe.model_validate({"name": "x", "fiscal_years": [2024], "companies": [bad]})
