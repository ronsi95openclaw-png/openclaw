"""Funding rate regime classification for perpetual futures."""
from __future__ import annotations

from typing import List


def classify_funding_regime(funding_rate_8h: float) -> str:
    """Returns 'EXTREME_POSITIVE', 'EXTREME_NEGATIVE', or 'NORMAL'."""
    if funding_rate_8h > 0.001:    # > 0.1% per 8h
        return "EXTREME_POSITIVE"
    if funding_rate_8h < -0.0005:  # < -0.05% per 8h
        return "EXTREME_NEGATIVE"
    return "NORMAL"


def is_funding_driven_regime(funding_rate_8h: float, candles: List) -> bool:
    """True when funding is so extreme it likely dominates price action."""
    return abs(funding_rate_8h) > 0.001
