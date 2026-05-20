"""Overfit detection methods for walk-forward validation."""
from __future__ import annotations

import math
import random
from typing import List

import numpy as np

from research.types import BacktestTrade, PerformanceMetrics


def overfit_score(
    is_metrics: PerformanceMetrics,
    oos_metrics: PerformanceMetrics,
) -> float:
    """Compute an overfit score in [0, 1].

    0 = no overfit (IS and OOS agree), 1 = severe overfit.

    Combines degradation across three axes:
      - Sharpe ratio
      - Win rate
      - Profit factor

    Each axis is clipped to [0, 1] before averaging.
    """
    def _degradation(is_val: float, oos_val: float, scale: float = 1.0) -> float:
        """Normalised degradation: how much worse is OOS relative to IS?"""
        if abs(is_val) < 1e-9:
            # Both near zero → no overfit on this axis
            return 0.0 if abs(oos_val) < 1e-9 else 1.0
        raw = (is_val - oos_val) / (abs(is_val) * scale)
        return float(np.clip(raw, 0.0, 1.0))

    sharpe_deg = _degradation(is_metrics.sharpe_ratio, oos_metrics.sharpe_ratio)
    winrate_deg = _degradation(is_metrics.win_rate, oos_metrics.win_rate)
    pf_deg = _degradation(is_metrics.profit_factor, oos_metrics.profit_factor)

    score = (sharpe_deg + winrate_deg + pf_deg) / 3.0
    return float(np.clip(score, 0.0, 1.0))


def monte_carlo_overfit_test(
    is_trades: List[BacktestTrade],
    oos_trades: List[BacktestTrade],
    n_simulations: int = 1000,
    seed: int = 42,
) -> float:
    """P-value: probability that OOS performance could arise by chance.

    Uses a permutation test: randomly shuffle IS trade pnls many times and
    compute the fraction whose mean exceeds the observed OOS mean pnl.

    Returns:
        p-value in [0, 1]. Low values (< 0.05) indicate the OOS outperformance
        is unlikely to be random.
    """
    if not is_trades or not oos_trades:
        return 1.0  # no data → cannot reject null

    rng = random.Random(seed)

    is_pnls = np.array([t.net_pnl_pct for t in is_trades], dtype=float)
    oos_mean = float(np.mean([t.net_pnl_pct for t in oos_trades]))

    # Permutation distribution of IS mean pnl
    count_exceeding = 0
    n = len(is_pnls)
    for _ in range(n_simulations):
        idx = np.array([rng.randint(0, n - 1) for _ in range(n)])
        sim_mean = float(np.mean(is_pnls[idx]))
        if sim_mean >= oos_mean:
            count_exceeding += 1

    p_value = count_exceeding / n_simulations
    return float(p_value)


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_trials: int,
    n_returns: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio (DSR) per Bailey & Lopez de Prado (2014).

    Adjusts the observed Sharpe for multiple-testing bias introduced by
    searching over n_trials parameter combinations.

    Args:
        observed_sharpe: The best Sharpe ratio found across all trials.
        n_trials: Number of parameter combinations evaluated (selection bias).
        n_returns: Length of the return series used to compute the Sharpe.
        skewness: Skewness of the return distribution.
        kurtosis: Excess kurtosis of the return distribution (normal = 3).

    Returns:
        Probability in [0, 1] that the strategy has a positive true Sharpe.
        Values > 0.95 indicate the strategy survives multiple-testing adjustment.
    """
    if n_trials <= 1 or n_returns < 2:
        # Cannot deflate — return raw p-value approximation
        from scipy import stats as _stats  # type: ignore
        return float(_stats.norm.cdf(observed_sharpe * math.sqrt(n_returns)))

    try:
        from scipy import stats as _stats  # type: ignore
    except ImportError:
        # Fallback: simple normal approximation
        return float(min(1.0, max(0.0, 0.5 + 0.5 * math.tanh(observed_sharpe))))

    # Expected maximum of n_trials independent normal draws (Euler–Mascheroni approx)
    euler_mascheroni = 0.5772156649
    expected_max = (
        (1 - euler_mascheroni) * _stats.norm.ppf(1 - 1.0 / n_trials)
        + euler_mascheroni * _stats.norm.ppf(1 - 1.0 / (n_trials * math.e))
    )

    # Variance of Sharpe estimator (non-normality correction)
    sr_std = math.sqrt(
        (1.0 - skewness * observed_sharpe + (kurtosis - 1) / 4.0 * observed_sharpe ** 2)
        / (n_returns - 1)
    )

    if sr_std < 1e-12:
        return 1.0 if observed_sharpe > expected_max else 0.0

    z = (observed_sharpe - expected_max) / sr_std
    dsr = float(_stats.norm.cdf(z))
    return float(np.clip(dsr, 0.0, 1.0))
