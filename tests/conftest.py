"""Shared test fixtures.

Every test gets a clean environment: no stray FINSIGHT_* variables from the developer's shell
or ``.env`` file, and a fresh settings singleton.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from finsight.config.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for key in list(os.environ):
        if key.startswith("FINSIGHT_"):
            monkeypatch.delenv(key)
    # Point at an empty dir so a developer's real .env is never read during tests.
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
