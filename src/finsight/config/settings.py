"""Typed application settings.

Design notes
------------
* One immutable ``Settings`` object, built from environment variables (prefix ``FINSIGHT_``,
  nested groups separated by ``__``) and an optional ``.env`` file.
  Example: ``FINSIGHT_RETRIEVAL__FINAL_K=10``.
* Groups (``SecSettings``, ``RetrievalSettings`` ...) are plain pydantic models on purpose:
  the *same* models are reused for experiment presets in ``configs/*.yaml`` so a preset can
  never drift from what the code accepts.
* Secrets are deliberately absent. The Anthropic SDK resolves credentials itself
  (``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile), so nothing sensitive can end up in
  ``finsight config show``, logs or serialised eval-run configs.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from finsight.core.exceptions import ConfigError

# SEC asks for "Company/Name contact@email" style identification.
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class _Group(BaseModel):
    """Base for settings groups: immutable and strict about unknown keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SecSettings(_Group):
    """SEC EDGAR access. See https://www.sec.gov/os/accessing-edgar-data."""

    user_agent: str = Field(
        default="",
        description="Required by SEC fair-access policy, e.g. 'Jane Doe jane@example.com'.",
    )
    # The published cap is 10 req/s; stay comfortably below it.
    requests_per_second: float = Field(default=8.0, gt=0, le=10.0)
    timeout_s: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=5, ge=0)
    cache_responses: bool = True


class EmbeddingSettings(_Group):
    backend: Literal["fastembed", "hashing"] = "fastembed"
    model_name: str = "BAAI/bge-small-en-v1.5"
    batch_size: int = Field(default=64, ge=1)
    # BGE models are trained with an instruction prefix on the *query* side only.
    query_prefix: str = "Represent this sentence for searching relevant passages: "


class ChunkingSettings(_Group):
    target_tokens: int = Field(default=400, ge=50)
    overlap_ratio: float = Field(default=0.15, ge=0.0, lt=0.5)
    min_tokens: int = Field(default=40, ge=1)
    tables_as_chunks: bool = True
    context_header: bool = True

    @model_validator(mode="after")
    def _min_below_target(self) -> ChunkingSettings:
        if self.min_tokens >= self.target_tokens:
            raise ValueError("min_tokens must be smaller than target_tokens")
        return self


class RetrievalSettings(_Group):
    mode: Literal["dense", "sparse", "hybrid"] = "hybrid"
    dense_k: int = Field(default=30, ge=1)
    sparse_k: int = Field(default=30, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    dense_weight: float = Field(default=1.0, ge=0.0)
    sparse_weight: float = Field(default=1.0, ge=0.0)
    # Off by default: ablation A1 (reports/ablation_A1_dev.md) found the reranker improves
    # ordering (nDCG) but not recall@8 - all 8 chunks reach the model whatever their order - at
    # roughly 16x the latency. It stays available as the `hybrid_rerank` preset.
    rerank: bool = False
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_top_n: int = Field(default=30, ge=1)
    final_k: int = Field(default=8, ge=1)
    max_chunks_per_filing: int = Field(default=4, ge=1)

    @model_validator(mode="after")
    def _final_within_candidates(self) -> RetrievalSettings:
        if self.rerank and self.final_k > self.rerank_top_n:
            raise ValueError("final_k cannot exceed rerank_top_n when reranking is on")
        return self


class LLMSettings(_Group):
    """Model ids per *role*.

    Roles are separate so cost can be tuned independently (evaluation makes thousands of
    calls; the judge and the query analyser rarely need the strongest model). All default to
    the same model so behaviour is identical until you deliberately change one.
    """

    model: str = "claude-opus-5"
    analysis_model: str = "claude-opus-5"
    judge_model: str = "claude-opus-5"
    # Adaptive thinking spends output tokens too, so leave generous room
    # (the SDK's recommended non-streaming default).
    max_tokens: int = Field(default=16000, ge=256)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    timeout_s: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    max_agent_steps: int = Field(default=8, ge=1)
    #: If the primary model's safety classifier declines a request, the API transparently retries
    #: on this model (server-side fallbacks). ``None`` disables it. Financial questions rarely
    #: trigger refusals, so this is insurance rather than a hot path.
    refusal_fallback_model: str | None = "claude-opus-4-8"


class OllamaSettings(_Group):
    """A local, zero-cost model served by Ollama (https://ollama.com) - no API key needed.

    Ollama must already be running (``ollama serve``, or ``brew services start ollama``) with
    the configured model pulled (``ollama pull <model>``);
    :class:`~finsight.generation.ollama.OllamaLLM` fails with an actionable message rather than
    falling back to a paid provider.
    """

    base_url: str = "http://localhost:11434"
    model: str = "llama3.2:3b"
    #: Context window to request from Ollama (``options.num_ctx``). Larger needs more RAM/VRAM.
    context_tokens: int = Field(default=8192, ge=256)
    timeout_s: float = Field(default=120.0, gt=0)
    #: Retries for *transport* failures only (connection refused, timeout). A 404 "model not
    #: found" is never retried - retrying it cannot succeed and would just look like a hang.
    max_retries: int = Field(default=2, ge=0)


class Settings(BaseSettings):
    """Root settings object. Obtain it with :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_prefix="FINSIGHT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False
    base_dir: Path = Field(default_factory=Path.cwd)

    sec: SecSettings = SecSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    #: Qdrant server URL (docker compose). None = embedded, single-process local storage.
    qdrant_url: str | None = None
    #: Queries per minute allowed per client on the (LLM-cost-bearing) /v1/query endpoints.
    api_rate_limit: int = Field(default=30, ge=1)
    chunking: ChunkingSettings = ChunkingSettings()
    retrieval: RetrievalSettings = RetrievalSettings()
    llm: LLMSettings = LLMSettings()
    ollama: OllamaSettings = OllamaSettings()

    # ------------------------------------------------------------------ derived paths
    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def interim_dir(self) -> Path:
        return self.data_dir / "interim"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "indexes"

    @property
    def eval_dir(self) -> Path:
        return self.data_dir / "eval"

    @property
    def runs_dir(self) -> Path:
        return self.base_dir / "reports" / "runs"

    @property
    def configs_dir(self) -> Path:
        return self.base_dir / "configs"

    @property
    def fact_db_path(self) -> Path:
        return self.processed_dir / "facts.duckdb"

    # ------------------------------------------------------------------ guards
    def require_sec_user_agent(self) -> str:
        """Return the SEC User-Agent or raise a helpful :class:`ConfigError`."""
        ua = self.sec.user_agent.strip()
        if not ua or not _EMAIL_RE.search(ua):
            raise ConfigError(
                "SEC requires a descriptive User-Agent that includes a contact email. "
                "Set FINSIGHT_SEC__USER_AGENT='Your Name your@email.com' (see .env.example)."
            )
        return ua

    def ensure_dirs(self) -> None:
        for path in (
            self.raw_dir,
            self.interim_dir,
            self.processed_dir,
            self.index_dir,
            self.eval_dir,
            self.runs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton. Call ``get_settings.cache_clear()`` in tests."""
    return Settings()
