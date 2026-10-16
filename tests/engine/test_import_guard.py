"""C15: the engine imports without Home Assistant."""

from __future__ import annotations

from collections.abc import Sequence
import importlib
import importlib.abc
from importlib.machinery import ModuleSpec
import pkgutil
import sys
from types import ModuleType

import pytest

_BLOCKED = "homeassistant"


def _is_blocked(name: str) -> bool:
    return name == _BLOCKED or name.startswith(f"{_BLOCKED}.")


def _is_engine(name: str) -> bool:
    return name == "engine" or name.startswith("engine.")


class _BlockHomeAssistant(importlib.abc.MetaPathFinder):
    """Meta path finder that makes every ``homeassistant`` import fail."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        if _is_blocked(fullname):
            raise ImportError(f"blocked import of {fullname!r} in the engine")
        return None


@pytest.fixture
def homeassistant_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove Home Assistant and the engine from sys.modules and block HA imports.

    Everything is restored after the test, so this also works when Home
    Assistant is installed and already imported (full test run).
    """
    for name in list(sys.modules):
        if _is_blocked(name) or _is_engine(name):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [_BlockHomeAssistant(), *sys.meta_path])


@pytest.mark.usefixtures("homeassistant_blocked")
def test_c15_blocker_blocks_homeassistant() -> None:
    """The guard itself works: importing homeassistant fails."""
    with pytest.raises(ImportError, match="blocked import"):
        importlib.import_module(_BLOCKED)


@pytest.mark.usefixtures("homeassistant_blocked")
def test_c15_engine_imports_without_homeassistant() -> None:
    """Every module of the engine package imports with homeassistant blocked."""
    engine = importlib.import_module("engine")
    imported = ["engine"]
    for module in pkgutil.walk_packages(engine.__path__, prefix="engine."):
        importlib.import_module(module.name)
        imported.append(module.name)

    assert "engine.options" in imported
    assert not [name for name in sys.modules if _is_blocked(name)]
