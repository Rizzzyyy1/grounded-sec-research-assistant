"""The company universe: which companies/filings the system covers.

Kept in ``configs/universe.yaml`` (data, not code) so widening or narrowing the scope is a
one-line change and evaluation runs record exactly which universe they used.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from finsight.core.exceptions import ConfigError
from finsight.core.schemas import FormType

T = TypeVar("T", bound=BaseModel)


class CompanySpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str = Field(pattern=r"^[A-Z.\-]{1,6}$")
    name: str
    sector: str
    fiscal_year_end: str = Field(
        pattern=r"^(0[1-9]|1[0-2])-\d{2}$",
        description="MM-DD of the fiscal year end; drives fiscal-period alignment tests.",
    )
    aliases: tuple[str, ...] = ()
    cik: str | None = Field(
        default=None,
        pattern=r"^\d{10}$",
        description=(
            "Pin the CIK instead of resolving it from the ticker. Needed after a holding-company "
            "reorganisation: the ticker moves to a new registrant (e.g. XOM -> ExxonMobil "
            "Holdings Corp, 2026) while all historical filings stay under the old CIK."
        ),
    )

    known_gaps: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Canonical metric -> reason it is legitimately absent for this company (e.g. "
            "'does not pay dividends'). Turns a coverage gap from 'unknown' into 'explained'."
        ),
    )

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.ticker, self.name, *self.aliases)


class Universe(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    fiscal_years: tuple[int, ...]
    forms: tuple[FormType, ...] = (FormType.TEN_K,)
    companies: tuple[CompanySpec, ...]

    @model_validator(mode="after")
    def _unique_tickers(self) -> Universe:
        tickers = [c.ticker for c in self.companies]
        dupes = {t for t in tickers if tickers.count(t) > 1}
        if dupes:
            raise ValueError(f"duplicate tickers in universe: {sorted(dupes)}")
        return self

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(c.ticker for c in self.companies)

    def company(self, ticker: str) -> CompanySpec:
        for c in self.companies:
            if c.ticker == ticker.upper():
                return c
        raise KeyError(ticker)

    def alias_table(self) -> dict[str, str]:
        """Lower-cased name/alias/ticker -> ticker, used by rule-based query analysis."""
        return {n.lower(): c.ticker for c in self.companies for n in c.all_names}


def _read_yaml(path: Path) -> object:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc


def load_universe(path: Path) -> Universe:
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return Universe.model_validate(raw)


def load_presets(path: Path, model: type[T]) -> dict[str, T]:
    """Load named experiment presets, each validated against ``model``.

    File shape::

        presets:
          hybrid_rerank: {mode: hybrid, rerank: true}
    """
    raw = _read_yaml(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("presets"), dict):
        raise ConfigError(f"{path} must contain a top-level 'presets' mapping")
    return {name: model.model_validate(cfg or {}) for name, cfg in raw["presets"].items()}
