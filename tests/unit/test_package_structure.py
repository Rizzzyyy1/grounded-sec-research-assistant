"""Every module in the package must import cleanly and document its responsibility.

This keeps the (large) skeleton honest: a syntax error or a missing docstring in any planned
module is caught immediately, long before the module is implemented.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import finsight

pytestmark = pytest.mark.unit


def _all_modules() -> list[str]:
    return [m.name for m in pkgutil.walk_packages(finsight.__path__, prefix="finsight.")]


def test_there_are_many_modules() -> None:
    assert len(_all_modules()) > 60


@pytest.mark.parametrize("name", _all_modules())
def test_module_imports_and_has_docstring(name: str) -> None:
    if name.startswith("finsight.ui.") or name == "finsight.cli":
        pytest.skip("UI modules pull optional deps; CLI is covered by test_cli")
    module = importlib.import_module(name)
    assert module.__doc__ and module.__doc__.strip(), f"{name} needs a module docstring"
