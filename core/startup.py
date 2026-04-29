"""Ensure all runtime directories exist before any module writes to them.

Call ensure_data_dirs() once at the top of receiver.py and any other entry point.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).parent.parent

_REQUIRED_DIRS = [
    _ROOT / "data",
    _ROOT / "data" / "logs",
    _ROOT / "memory",
]


def ensure_data_dirs() -> None:
    for d in _REQUIRED_DIRS:
        d.mkdir(parents=True, exist_ok=True)
