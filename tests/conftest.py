"""Shared test helpers for the whole suite.

Local data under ``data/`` is gitignored, so a fresh clone does not have it. A test that needs such a file declares
it, and is skipped (not failed) when the file is missing:

* ``@pytest.mark.needs_data("data/raw/nba_open/team_box_2027.parquet")`` on a test function;
* ``need_data("data/...")`` from a fixture (the ``need_data`` fixture returns the helper).
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def missing_data_reason(*paths: str) -> str | None:
    """``"needs data/..."`` naming the paths (relative to the project root) that do not exist, or None."""
    missing = [p for p in paths if not (ROOT / p).exists()]
    return f"needs {', '.join(missing)}" if missing else None


def skip_unless_data(*paths: str) -> None:
    """Skip the running test unless every path exists."""
    reason = missing_data_reason(*paths)
    if reason:
        pytest.skip(reason)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "needs_data(*paths): skip unless these local data files exist")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        paths = [p for mark in item.iter_markers("needs_data") for p in mark.args]
        reason = missing_data_reason(*paths) if paths else None
        if reason:
            item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture
def need_data() -> Callable[..., None]:
    return skip_unless_data
