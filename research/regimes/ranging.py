"""Ranging / consolidation regime indicators.

Functions:
  is_ranging               — ADX below threshold + Bollinger confirmation
  range_bounds             — (support, resistance) from recent high/low
  range_compression_ratio  — current range / historical average range
"""
from __future__ import annotations

from typing import List, Tuple

from research.types import Candle
from research.regimes.trend import adx
from research.regimes.volatility import bollinger_width


# ── public API ────────────────────────────────────────────────────────────────

def is_ranging(candles: List[Candle], adx_threshold: float = 20.0) -> bool:
    """True when the market is in a ranging / sideways regime.

    A ranging market requires BOTH:
    1. ADX below ``adx_threshold`` (weak directional movement), AND
    2. Bollinger Band width within a moderate range (not compressed, not blown out).

    Returns False when fewer than 30 candles are available.
    """
    if len(candles) < 30:
        return False

    adx_val = adx(candles)
    if adx_val >= adx_threshold:
        return False

    # Bollinger Band width sanity: if BB is extremely compressed the market
    # is in a pre-breakout squeeze, not a true range.
    closes = [c.close for c in candles]
    bb_w   = bollinger_width(closes)

    # Ranging → BB width between 1% and 15% (heuristic for crypto)
    return 1.0 <= bb_w <= 15.0


def range_bounds(candles: List[Candle], lookback: int = 20) -> Tuple[float, float]:
    """(support, resistance) from recent highest-high and lowest-low.

    Parameters
    ----------
    candles:
        Price candles.
    lookback:
        Number of bars to examine.

    Returns
    -------
    (support, resistance)
        If fewer than 2 candles are available both values will be the last
        close price.
    """
    if len(candles) < 2:
        last = candles[-1].close if candles else 0.0
        return last, last

    window = candles[-lookback:] if len(candles) >= lookback else candles
    support    = min(c.low  for c in window)
    resistance = max(c.high for c in window)
    return support, resistance


def range_compression_ratio(candles: List[Candle], window: int = 20) -> float:
    """Current range / historical average range.

    ``current range`` = high - low of the most recent bar.
    ``historical average range`` = mean (high - low) over the last ``window`` bars.

    < 0.5  → strong range compression (coiling / squeeze).
    ~1.0   → normal.
    > 1.5  → range expansion.

    Returns 1.0 when insufficient data.
    """
    if len(candles) < window + 1:
        return 1.0

    # Current bar range
    current_range = candles[-1].high - candles[-1].low
    if current_range < 0:
        current_range = 0.0

    # Historical average bar range
    historical_window = candles[-window - 1 : -1]
    avg_range = sum(c.high - c.low for c in historical_window) / len(historical_window)

    if avg_range <= 0:
        return 1.0

    return current_range / avg_range
