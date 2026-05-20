"""Drawdown analytics for equity curves.

All functions accept plain Python lists and return plain Python scalars or lists.
"""
from __future__ import annotations

from typing import List, Tuple


def compute_max_drawdown(equity: List[float]) -> Tuple[float, int, int]:
    """Compute maximum drawdown from an equity curve.

    Parameters
    ----------
    equity:  list of portfolio values (one per bar)

    Returns
    -------
    Tuple of (max_drawdown_pct, start_idx, end_idx).
    max_drawdown_pct is negative (e.g. -15.0 means 15% drawdown).
    Indices mark the start (peak) and end (trough) of the worst drawdown.
    Returns (0.0, 0, 0) for empty or single-element input.
    """
    if len(equity) < 2:
        return 0.0, 0, 0

    max_dd = 0.0
    peak   = equity[0]
    peak_idx = 0
    start_idx = 0
    end_idx   = 0

    for i, v in enumerate(equity):
        if v > peak:
            peak = v
            peak_idx = i
        dd = (v - peak) / peak * 100.0 if peak > 0 else 0.0
        if dd < max_dd:
            max_dd    = dd
            start_idx = peak_idx
            end_idx   = i

    return max_dd, start_idx, end_idx


def compute_drawdown_series(equity: List[float]) -> List[float]:
    """Return per-bar drawdown from the running peak (in percent).

    Each value is <= 0 (drawdown) or 0 (at peak).
    Returns an empty list for empty input.
    """
    if not equity:
        return []
    series: List[float] = []
    peak = equity[0]
    for v in equity:
        if v > peak:
            peak = v
        dd = (v - peak) / peak * 100.0 if peak > 0 else 0.0
        series.append(dd)
    return series


def drawdown_duration(equity: List[float]) -> int:
    """Return the maximum number of consecutive bars spent in any drawdown.

    A bar is "in drawdown" when its equity is strictly below the running peak.
    Returns 0 for empty input or if never in drawdown.
    """
    if not equity:
        return 0
    peak    = equity[0]
    current = 0
    max_dur = 0
    for v in equity:
        if v > peak:
            peak    = v
            current = 0
        elif v < peak:
            current += 1
            max_dur = max(max_dur, current)
    return max_dur


def recovery_factor(total_return: float, max_drawdown: float) -> float:
    """Return total_return / |max_drawdown|.

    Both inputs are expected as percentage values (e.g. 25.0, 10.5).
    Returns 0.0 when max_drawdown is zero (no drawdown occurred).
    """
    if max_drawdown == 0:
        return 0.0
    return total_return / abs(max_drawdown)


def calmar_ratio(annualized_return: float, max_drawdown: float) -> float:
    """Return annualized_return / |max_drawdown|.

    Both inputs are expected as percentage values.
    Returns 0.0 when max_drawdown is zero.
    """
    if max_drawdown == 0:
        return 0.0
    return annualized_return / abs(max_drawdown)
