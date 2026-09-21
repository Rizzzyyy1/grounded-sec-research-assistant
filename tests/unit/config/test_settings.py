"""Settings: defaults, env overrides, validation and guards."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from finsight.config.settings import ChunkingSettings, RetrievalSettings, Settings, get_settings
from finsight.core.exceptions import ConfigError

pytestmark = pytest.mark.unit


def test_defaults_are_sane() -> None:
    s = Settings()
    assert s.retrieval.mode == "hybrid"
    assert s.retrieval.rrf_k == 60
    assert s.chunking.target_tokens == 400
    assert s.llm.model == "claude-opus-5"
    assert s.sec.requests_per_second <= 10  # SEC hard cap


def test_nested_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINSIGHT_RETRIEVAL__FINAL_K", "5")
    monkeypatch.setenv("FINSIGHT_RETRIEVAL__MODE", "dense")
    monkeypatch.setenv("FINSIGHT_LOG_LEVEL", "DEBUG")
    s = Settings()
    assert s.retrieval.final_k == 5
    assert s.retrieval.mode == "dense"
    assert s.log_level == "DEBUG"


def test_derived_paths_hang_off_base_dir(tmp_path: Path) -> None:
    s = Settings(base_dir=tmp_path)
    assert s.raw_dir == tmp_path / "data" / "raw"
    assert s.fact_db_path == tmp_path / "data" / "processed" / "facts.duckdb"
    assert s.runs_dir == tmp_path / "reports" / "runs"


def test_ensure_dirs_creates_layout(tmp_path: Path) -> None:
    s = Settings(base_dir=tmp_path)
    s.ensure_dirs()
    for d in (s.raw_dir, s.processed_dir, s.index_dir, s.eval_dir, s.runs_dir):
        assert d.is_dir()


@pytest.mark.parametrize("ua", ["", "   ", "just-a-name", "no-at-sign example.com"])
def test_sec_user_agent_rejects_missing_or_email_less(
    monkeypatch: pytest.MonkeyPatch, ua: str
) -> None:
    monkeypatch.setenv("FINSIGHT_SEC__USER_AGENT", ua)
    with pytest.raises(ConfigError, match="User-Agent"):
        Settings().require_sec_user_agent()


def test_sec_user_agent_accepts_name_and_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINSIGHT_SEC__USER_AGENT", "Jane Doe jane@example.com")
    assert Settings().require_sec_user_agent() == "Jane Doe jane@example.com"


def test_rate_limit_cannot_exceed_sec_cap() -> None:
    with pytest.raises(ValidationError):
        Settings(sec={"requests_per_second": 25})  # type: ignore[arg-type]


def test_final_k_cannot_exceed_rerank_candidates() -> None:
    with pytest.raises(ValidationError, match="final_k"):
        RetrievalSettings(rerank=True, rerank_top_n=5, final_k=10)
    # ...but is fine when reranking is off
    assert RetrievalSettings(rerank=False, rerank_top_n=5, final_k=10).final_k == 10


def test_min_tokens_must_be_below_target() -> None:
    with pytest.raises(ValidationError, match="min_tokens"):
        ChunkingSettings(target_tokens=100, min_tokens=100)


def test_groups_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        RetrievalSettings(final_kk=3)  # type: ignore[call-arg]


def test_settings_are_immutable() -> None:
    s = Settings()
    with pytest.raises(ValidationError):
        s.log_level = "ERROR"  # type: ignore[misc]


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def _all_keys(node: object) -> set[str]:
    if isinstance(node, dict):
        return {str(k).lower() for k in node} | {k for v in node.values() for k in _all_keys(v)}
    return set()


def test_no_secret_fields_exist() -> None:
    """Credentials must never be part of settings (they would leak via `config show`)."""
    keys = _all_keys(Settings().model_dump())
    forbidden = {k for k in keys if any(w in k for w in ("api_key", "secret", "token", "password"))}
    # `*_tokens` fields are token *budgets*, not credentials.
    forbidden = {k for k in forbidden if not k.endswith("_tokens") and k != "max_tokens"}
    assert not forbidden, f"secret-like fields in Settings: {forbidden}"
