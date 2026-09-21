"""CLI smoke tests and the doctor preflight."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from finsight import __version__
from finsight.cli import app, run_checks
from finsight.core.schemas import FinancialFact, FiscalPeriod, FormType
from finsight.ingestion.xbrl.facts import ParsedFacts
from finsight.ingestion.xbrl.store import FactStore

pytestmark = pytest.mark.unit
runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_config_show_is_valid_json_without_secrets() -> None:
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["retrieval"]["mode"] == "hybrid"
    assert "api_key" not in result.stdout.lower()


def test_doctor_passes_on_missing_optional_things(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing SEC UA / API key / extras are warnings, not failures."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0


def test_run_checks_reports_anthropic_key_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    by_name = {c.name: c for c in run_checks()}
    assert by_name["Anthropic credentials"].ok
    assert "sk-ant" not in by_name["Anthropic credentials"].detail  # never echo the key


def test_doctor_output_names_every_optional_group() -> None:
    """Regression: rich swallowed '[data]' as markup, leaving blank check names."""
    result = runner.invoke(app, ["doctor"])
    for extra in ("data", "ml", "llm", "eval", "api", "ui"):
        assert f"extra: {extra}" in result.stdout


def test_check_names_are_unique() -> None:
    names = [c.name for c in run_checks()]
    assert len(names) == len(set(names))


def test_filings_without_user_agent_fails_with_a_fix_it_message(repo_root: Path) -> None:
    result = runner.invoke(
        app, ["filings", "AAPL", "--universe", str(repo_root / "configs" / "universe.yaml")]
    )
    assert result.exit_code == 1
    assert "User-Agent" in result.stdout


def test_filings_rejects_tickers_outside_the_universe(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINSIGHT_SEC__USER_AGENT", "Jane Doe jane@example.com")
    result = runner.invoke(
        app, ["filings", "ZZZZ", "--universe", str(repo_root / "configs" / "universe.yaml")]
    )
    assert result.exit_code == 1
    assert "not in universe" in result.stdout


def test_ingest_without_user_agent_fails_with_a_fix_it_message(repo_root: Path) -> None:
    result = runner.invoke(
        app, ["ingest", "--universe", str(repo_root / "configs" / "universe.yaml")]
    )
    assert result.exit_code == 1
    assert "User-Agent" in result.stdout


def test_coverage_without_a_store_says_to_ingest_first(repo_root: Path) -> None:
    result = runner.invoke(
        app, ["coverage", "--universe", str(repo_root / "configs" / "universe.yaml")]
    )
    assert result.exit_code == 1
    assert "finsight ingest" in result.stdout


def test_coverage_writes_a_report_from_a_populated_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINSIGHT_BASE_DIR", str(tmp_path))
    universe = tmp_path / "u.yaml"
    universe.write_text(
        "name: t\nfiscal_years: [2024]\ncompanies:\n"
        "  - {ticker: TECH, name: Tech, sector: IT, fiscal_year_end: '09-30'}\n"
    )
    fact = FinancialFact(
        ticker="TECH",
        cik="0000000001",
        metric="revenue",
        tag="Revenues",
        value=1.0,
        unit="USD",
        period_type="duration",
        start=date(2023, 10, 1),
        end=date(2024, 9, 28),
        fiscal_year=2024,
        fiscal_period=FiscalPeriod.FY,
        form=FormType.TEN_K,
        filed=date(2024, 11, 1),
        accession="0000000001-24-000001",
    )
    db = tmp_path / "data" / "processed" / "facts.duckdb"
    with FactStore(db) as store:
        store.replace_company_facts("TECH", ParsedFacts([fact], []))

    out = tmp_path / "report.md"
    result = runner.invoke(app, ["coverage", "--universe", str(universe), "--write", str(out)])
    assert result.exit_code == 0, result.stdout
    assert "# Data coverage report" in out.read_text()
    assert "unexplained gaps" in result.stdout.replace("\n", " ")


def test_every_documented_command_is_registered() -> None:
    """Regression: a scripted edit once deleted `serve` and `ui` without any test noticing."""
    top = {c.name or c.callback.__name__ for c in app.registered_commands}  # type: ignore[union-attr]
    assert {
        "version",
        "doctor",
        "filings",
        "ingest",
        "coverage",
        "process",
        "index",
        "ask",
        "serve",
        "ui",
    } <= top
    groups = {g.name: g.typer_instance for g in app.registered_groups}
    assert set(groups) == {"config", "eval"}
    eval_cmds = {c.name for c in groups["eval"].registered_commands}  # type: ignore[union-attr]
    assert eval_cmds == {"gold", "retrieval", "ablate", "run", "compare"}


def test_serve_and_ui_show_help_without_starting_anything() -> None:
    for command in ("serve", "ui"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0 and "--port" in result.stdout
