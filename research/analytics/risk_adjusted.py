"""Risk-adjusted return metrics.

All functions accept plain Python lists of return values (per-bar or per-trade).
numpy/scipy are used for numerical stability and quantile computation.
"""
from __future__ import annotations

import math
from typing import List

import numpy as np
import scipy.stats


def sharpe_ratio(
    returns: List[float],
    risk_free: float = 0.0,
    periods_per_year: float = 252,
) -> float:
    """Annualised Sharpe ratio.

    Parameters
    ----------
    returns:          list of per-period returns (not percentages)
    risk_free:        per-period risk-free rate (default 0)
    periods_per_year: scaling factor for annualisation (default 252 for daily)

    Returns 0.0 for empty or zero-std inputs.
    """
    if not returns:
        return 0.0
    arr    = np.array(returns, dtype=float)
    excess = arr - risk_free
    mean   = float(np.mean(excess))
    std    = float(np.std(excess, ddof=1)) if len(arr) > 1 else 0.0
    if std == 0:
        return 0.0
    return float(mean / std * math.sqrt(periods_per_year))


def sortino_ratio(
    returns: List[float],
    risk_free: float = 0.0,
    periods_per_year: float = 252,
) -> float:
    """Annualised Sortino ratio (downside-only deviation denominator).

    Returns inf if there are no negative returns and mean excess > 0.
    Returns 0.0 for empty input.
    """
    if not returns:
        return 0.0
    arr    = np.array(returns, dtype=float)
    excess = arr - risk_free
    mean   = float(np.mean(excess))
    down   = excess[excess < 0]
    if len(down) == 0:
        return float("inf") if mean > 0 else 0.0
    downside_std = float(np.sqrt(np.mean(down ** 2)))
    if downside_std == 0:
        return 0.0
    return float(mean / downside_std * math.sqrt(periods_per_year))


def omega_ratio(
    returns: List[float],
    threshold: float = 0.0,
) -> float:
    """Omega ratio: weighted sum of gains above threshold over losses below.

    Omega > 1 indicates more probability-weighted gain than loss above the
    threshold.  Returns 1.0 when both gains and losses are zero.
    """
    if not returns:
        return 1.0
    gains  = sum(max(r - threshold, 0.0) for r in returns)
    losses = sum(max(threshold - r, 0.0) for r in returns)
    if losses == 0:
        return float("inf") if gains > 0 else 1.0
    return gains / losses


def information_ratio(
    returns: List[float],
    benchmark_returns: List[float],
) -> float:
    """Information ratio: mean active return / tracking error.

    Active return = returns - benchmark_returns (must be same length).
    Returns 0.0 for empty, mismatched, or zero-tracking-error inputs.
    """
    if not returns or not benchmark_returns:
        return 0.0
    n = min(len(returns), len(benchmark_returns))
    if n < 2:
        return 0.0
    arr  = np.array(returns[:n], dtype=float)
    bm   = np.array(benchmark_returns[:n], dtype=float)
    diff = arr - bm
    mean = float(np.mean(diff))
    std  = float(np.std(diff, ddof=1))
    if std == 0:
        return 0.0
    return float(mean / std)


def value_at_risk(returns: List[float], confidence: float = 0.95) -> float:
    """Historical Value-at-Risk (VaR) at the given confidence level.

    Returns the loss (positive number) that is not exceeded with probability
    ``confidence``.  For example, a 95% VaR of 0.02 means there is only a 5%
    chance of losing more than 2% in a single period.

    Parameters
    ----------
    returns:    per-period returns (negatives are losses)
    confidence: probability level, e.g. 0.95

    Returns 0.0 for empty input.
    """
    if not returns:
        return 0.0
    arr = np.array(returns, dtype=float)
    # VaR is the negative of the (1-confidence) quantile
    q = float(np.percentile(arr, (1.0 - confidence) * 100.0))
    return float(-q)  # positive loss


def conditional_value_at_risk(
    returns: List[float], confidence: float = 0.95
) -> float:
    """Expected Shortfall (CVaR / ES) at the given confidence level.

    Average of returns that fall below the VaR threshold — i.e. the expected
    loss *given* that we are in the worst (1-confidence) of outcomes.

    Returns 0.0 for empty input or when there are no tail losses.
    """
    if not returns:
        return 0.0
    arr = np.array(returns, dtype=float)
    threshold = float(np.percentile(arr, (1.0 - confidence) * 100.0))
    tail = arr[arr <= threshold]
    if len(tail) == 0:
        return 0.0
    return float(-np.mean(tail))  # positive loss
