"""Trend regime indicators.

Functions:
  adx             — Average Directional Index
  ema_slope       — normalized EMA slope
  trend_strength  — 0–1 score from ADX
  trend_direction — 'up' | 'down' | 'neutral' from EMA alignment
  is_trending     — boolean threshold on ADX
"""
from __future__ import annotations

import math
from typing import List

from research.types import Candle


# ── helpers ───────────────────────────────────────────────────────────────────

def _ema(values: List[float], period: int) -> List[float]:
    """Exponential moving average — returns same-length list as ``values``.

    Leading values (before enough history) are seeded with the simple mean of
    the available prefix so the list is always full-length.
    """
    if not values:
        return []

    result: List[float] = []
    k = 2.0 / (period + 1)

    for i, v in enumerate(values):
        if i == 0:
            result.append(v)
        elif i < period:
            # Progressive SMA seed
            result.append(sum(values[: i + 1]) / (i + 1))
        else:
            result.append(v * k + result[-1] * (1.0 - k))
    return result


def _wilder_smooth(values: List[float], period: int) -> List[float]:
    """Wilder smoothing (used for +DI, -DI, ADX)."""
    if not values:
        return []

    result: List[float] = [0.0] * len(values)
    if len(values) < period:
        for i, v in enumerate(values):
            result[i] = v
        return result

    # Seed with sum of first ``period`` values
    result[period - 1] = sum(values[:period])
    for i in range(period, len(values)):
        result[i] = result[i - 1] - (result[i - 1] / period) + values[i]
    return result


# ── public API ────────────────────────────────────────────────────────────────

def adx(candles: List[Candle], period: int = 14) -> float:
    """Average Directional Index.

    > 25 = strong trend, < 20 = ranging / trendless.

    Returns 0.0 when fewer than ``2 * period + 1`` candles are available.
    """
    min_bars = 2 * period + 1
    if len(candles) < min_bars:
        return 0.0

    plus_dm:  List[float] = []
    minus_dm: List[float] = []
    true_ranges: List[float] = []

    for i in range(1, len(candles)):
        high_diff = candles[i].high - candles[i - 1].high
        low_diff  = candles[i - 1].low - candles[i].low

        if high_diff > low_diff and high_diff > 0:
            plus_dm.append(high_diff)
        else:
            plus_dm.append(0.0)

        if low_diff > high_diff and low_diff > 0:
            minus_dm.append(low_diff)
        else:
            minus_dm.append(0.0)

        h, lo, pc = candles[i].high, candles[i].low, candles[i - 1].close
        true_ranges.append(max(h - lo, abs(h - pc), abs(lo - pc)))

    # Wilder smooth
    sm_plus  = _wilder_smooth(plus_dm,  period)
    sm_minus = _wilder_smooth(minus_dm, period)
    sm_tr    = _wilder_smooth(true_ranges, period)

    dx_values: List[float] = []
    for i in range(period - 1, len(sm_tr)):
        atr_val = sm_tr[i]
        if atr_val <= 0:
            dx_values.append(0.0)
            continue
        di_plus  = 100.0 * sm_plus[i]  / atr_val
        di_minus = 100.0 * sm_minus[i] / atr_val
        di_sum   = di_plus + di_minus
        if di_sum <= 0:
            dx_values.append(0.0)
        else:
            dx_values.append(100.0 * abs(di_plus - di_minus) / di_sum)

    if not dx_values:
        return 0.0

    # Seed ADX with mean of first ``period`` DX values
    if len(dx_values) < period:
        return sum(dx_values) / len(dx_values)

    adx_val = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx_val = (adx_val * (period - 1) + dx) / period
    return adx_val


def ema_slope(closes: List[float], period: int = 20) -> float:
    """Normalized slope of EMA: (ema[-1] - ema[-period]) / ema[-period].

    Positive = upward slope, negative = downward slope.
    Returns 0.0 when insufficient data.
    """
    if len(closes) < period * 2:
        return 0.0

    ema_vals = _ema(closes, period)
    if len(ema_vals) < period:
        return 0.0

    old_val = ema_vals[-period]
    cur_val = ema_vals[-1]
    if old_val <= 0:
        return 0.0
    return (cur_val - old_val) / old_val


def trend_strength(candles: List[Candle], adx_period: int = 14) -> float:
    """0–1 score based on ADX.

    ADX ≤ 15  → 0.0 (no trend)
    ADX = 25  → ~0.4
    ADX = 40  → ~0.7
    ADX ≥ 60  → 1.0 (very strong trend)
    """
    adx_val = adx(candles, adx_period)
    # Clamp ADX from 0–60+ range into 0–1
    normalized = max(0.0, (adx_val - 15.0)) / 45.0   # 15 → 0, 60 → 1
    return min(1.0, normalized)


def trend_direction(
    closes: List[float],
    fast: int = 9,
    slow: int = 21,
) -> str:
    """'up' | 'down' | 'neutral' based on EMA alignment.

    'up'     = fast EMA > slow EMA and price above fast EMA
    'down'   = fast EMA < slow EMA and price below fast EMA
    'neutral' = mixed / insufficient data
    """
    if len(closes) < slow + 1:
        return "neutral"

    fast_ema = _ema(closes, fast)
    slow_ema = _ema(closes, slow)

    if not fast_ema or not slow_ema:
        return "neutral"

    curr_fast = fast_ema[-1]
    curr_slow = slow_ema[-1]
    price     = closes[-1]

    if curr_fast > curr_slow and price > curr_fast:
        return "up"
    if curr_fast < curr_slow and price < curr_fast:
        return "down"
    return "neutral"


def is_trending(candles: List[Candle], adx_threshold: float = 25.0) -> bool:
    """True when ADX is above ``adx_threshold``."""
    return adx(candles) >= adx_threshold
