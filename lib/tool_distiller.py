"""Truncate large tool/API results to the minimum usable payload."""
from __future__ import annotations

from typing import Any


def distill(result: Any, max_chars: int = 1500, head_chars: int = 600) -> Any:
    """Return result as-is if small; truncate strings that exceed max_chars."""
    if result is None:
        return result
    if isinstance(result, str) and len(result) > max_chars:
        return result[:head_chars] + "\n...[truncated]"
    return result
