"""Soak test configuration — adds --fast flag to skip slow tests."""
from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--fast",
        action="store_true",
        default=False,
        help="Skip tests marked with @pytest.mark.slow.",
    )


@pytest.fixture
def fast_mode(request: pytest.FixtureRequest) -> bool:
    """True when --fast is passed on the command line."""
    return bool(request.config.getoption("--fast"))
