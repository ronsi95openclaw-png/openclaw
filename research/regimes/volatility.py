"""Volatility regime indicators.

Functions:
  historical_volatility  — log-return std, optionally annualized
  atr                    — Wilder-smoothed Average True Range (scalar)
  atr_series             — ATR at every bar (same length as input)
  relative_atr           — current ATR / baseline ATR
  bollinger_width        — (upper - lower) / middle as percentage
  vol_regime             — (expanding, compressing) bool pair
"""
from __future__ import annotations

import math
from typing import List, Tuple

from research.types import Candle


# ── helpers ───────────────────────────────────────────────────────────────────

def _true_ranges(candles: List[Candle]) -> List[float]:
    """Compute true-range values for all bars except the first."""
    trs: List[float] = []
    for i in range(1, len(candles)):
        h   = candles[i].high
        lo  = candles[i].low
        pc  = candles[i - 1].close
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return trs


def _sma(values: List[float], period: int) -> List[float]:
    """Simple moving average at every position where enough data exists."""
    if len(values) < period:
        return []
    result: List[float] = []
    for i in range(period - 1, len(values)):
        result.append(sum(values[i - period + 1 : i + 1]) / period)
    return result


# ── public API ────────────────────────────────────────────────────────────────

def historical_volatility(
    closes: List[float],
    window: int = 20,
    annualized: bool = True,
    bars_per_year: float = 365 * 24 * 4,  # 15-minute bars default
) -> float:
    """Log-return standard deviation, optionally annualized (15-min bars by default).

    Parameters
    ----------
    closes:
        Closing prices (at least ``window + 1`` values required).
    window:
        Number of return observations.
    annualized:
        If True, multiply by sqrt(bars_per_year).
    bars_per_year:
        Annualization factor.  Default = 35 040 (15-min bars in a year).

    Returns
    -------
    float
        Realized volatility.  Returns 0.0 when insufficient data.
    """
    if len(closes) < window + 1:
        return 0.0

    tail = closes[-(window + 1):]
    log_rets: List[float] = []
    for i in range(1, len(tail)):
        if tail[i - 1] > 0:
            log_rets.append(math.log(tail[i] / tail[i - 1]))

    if len(log_rets) < 2:
        return 0.0

    mean = sum(log_rets) / len(log_rets)
    variance = sum((r - mean) ** 2 for r in log_rets) / (len(log_rets) - 1)
    vol = math.sqrt(variance)
    if annualized:
        vol *= math.sqrt(bars_per_year)
    return vol


def atr(candles: List[Candle], period: int = 14) -> float:
    """Wilder-smoothed Average True Range (scalar — last bar value).

    Returns 0.0 when fewer than ``period + 1`` candles are available.
    """
    if len(candles) < period + 1:
        return 0.0

    trs = _true_ranges(candles)
    if not trs:
        return 0.0

    # Seed with simple mean of first ``period`` TRs
    smoothed = sum(trs[:period]) / period
    for tr in trs[period:]:
        smoothed = (smoothed * (period - 1) + tr) / period
    return smoothed


def atr_series(candles: List[Candle], period: int = 14) -> List[float]:
    """ATR value at every bar (same length as ``candles``).

    Bars before sufficient history use the simple mean of available TRs.
    The first bar always returns 0.0 (no previous close).
    """
    n = len(candles)
    if n < 2:
        return [0.0] * n

    trs = _true_ranges(candles)   # length n-1

    result: List[float] = [0.0]   # index 0: no ATR possible
    smoothed: float = 0.0

    for idx, tr in enumerate(trs):
        bar_idx = idx + 1          # position in the candle series
        if idx < period - 1:
            # Accumulation phase — use progressive mean
            window_trs = trs[: idx + 1]
            smoothed   = sum(window_trs) / len(window_trs)
        elif idx == period - 1:
            # First full window — seed
            smoothed = sum(trs[:period]) / period
        else:
            smoothed = (smoothed * (period - 1) + tr) / period
        result.append(smoothed)

    return result


def relative_atr(
    candles: List[Candle],
    short_period: int = 5,
    long_period: int = 20,
) -> float:
    """current ATR / baseline ATR.

    > 1.3 → volatility expansion
    < 0.7 → volatility compression

    Returns 1.0 (neutral) when insufficient data.
    """
    if len(candles) < long_period + 1:
        return 1.0

    current_atr  = atr(candles, short_period)
    baseline_atr = atr(candles, long_period)

    if baseline_atr <= 0:
        return 1.0
    return current_atr / baseline_atr


def bollinger_width(
    closes: List[float],
    period: int = 20,
    k: float = 2.0,
) -> float:
    """(upper_band - lower_band) / middle_band as a percentage.

    Returns 0.0 when insufficient data.
    """
    if len(closes) < period:
        return 0.0

    window = closes[-period:]
    mean   = sum(window) / period
    if mean <= 0:
        return 0.0

    variance = sum((x - mean) ** 2 for x in window) / period
    std_dev  = math.sqrt(variance)

    upper = mean + k * std_dev
    lower = mean - k * std_dev
    return (upper - lower) / mean * 100.0


def vol_regime(
    candles: List[Candle],
    atr_expansion_threshold: float = 1.3,
    atr_compression_threshold: float = 0.7,
) -> Tuple[bool, bool]:
    """Classify current volatility state.

    Parameters
    ----------
    candles:
        Price candles (requires ≥ 21 bars for meaningful signal).
    atr_expansion_threshold:
        relative_atr ratio above which the regime is classified as expanding.
    atr_compression_threshold:
        relative_atr ratio below which the regime is classified as compressing.

    Returns
    -------
    (expanding, compressing)
        Mutually exclusive booleans.  Both False = neutral vol.
    """
    ratio = relative_atr(candles)
    expanding   = ratio > atr_expansion_threshold
    compressing = ratio < atr_compression_threshold
    return expanding, compressing
