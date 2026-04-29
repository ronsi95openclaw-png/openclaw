"""Normalize and truncate raw user input before it hits the router."""
from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")


def optimize_input(text: str, max_chars: int = 2000) -> str:
    """Collapse whitespace and cap length."""
    return _WHITESPACE.sub(" ", text).strip()[:max_chars]
