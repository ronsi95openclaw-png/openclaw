"""Correlation analytics for strategies and instruments.

All computation uses numpy — no pandas dependency.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

import numpy as np

from research.types import BacktestResult, Candle


# ── Strategy-level correlation ────────────────────────────────────────────────

def strategy_correlation(
    results: Dict[str, BacktestResult],
) -> Dict[Tuple[str, str], float]:
    """Compute pairwise Pearson correlation between strategy equity curves.

    Equity curves are aligned by index (truncated to the shortest).

    Parameters
    ----------
    results:  mapping of strategy_name → BacktestResult

    Returns a dict of {(name_a, name_b): correlation} for all unique pairs.
    Returns an empty dict for fewer than 2 strategies.
    """
    names = list(results.keys())
    if len(names) < 2:
        return {}

    equities = {n: np.array(results[n].equity_curve, dtype=float) for n in names}
    # Convert to returns for correlation (more meaningful than raw equity)
    returns_map: Dict[str, np.ndarray] = {}
    for n, eq in equities.items():
        if len(eq) > 1:
            rets = np.diff(eq) / np.where(eq[:-1] != 0, eq[:-1], 1.0)
            returns_map[n] = rets
        else:
            returns_map[n] = np.array([0.0])

    corrs: Dict[Tuple[str, str], float] = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            ra, rb = returns_map[a], returns_map[b]
            n = min(len(ra), len(rb))
            if n < 2:
                corrs[(a, b)] = float("nan")
            else:
                c = float(np.corrcoef(ra[:n], rb[:n])[0, 1])
                corrs[(a, b)] = c
    return corrs


# ── Instrument-level rolling correlation ─────────────────────────────────────

def pair_correlation(
    candles_a: List[Candle],
    candles_b: List[Candle],
    window: int = 20,
) -> List[float]:
    """Compute rolling Pearson correlation of close prices between two instruments.

    Aligns by index (uses min length).  First (window-1) entries are 0.0.

    Parameters
    ----------
    candles_a, candles_b: price series (aligned by bar index)
    window:               rolling window size

    Returns a list of length min(len(a), len(b)).
    """
    n = min(len(candles_a), len(candles_b))
    if n < window or window < 2:
        return [0.0] * n

    closes_a = np.array([c.close for c in candles_a[:n]], dtype=float)
    closes_b = np.array([c.close for c in candles_b[:n]], dtype=float)

    result: List[float] = [0.0] * (window - 1)
    for i in range(window - 1, n):
        sa = closes_a[i - window + 1: i + 1]
        sb = closes_b[i - window + 1: i + 1]
        std_a = float(np.std(sa, ddof=1))
        std_b = float(np.std(sb, ddof=1))
        if std_a == 0 or std_b == 0:
            result.append(0.0)
        else:
            corr = float(np.corrcoef(sa, sb)[0, 1])
            result.append(corr if not math.isnan(corr) else 0.0)
    return result


# ── Correlation matrix ────────────────────────────────────────────────────────

def correlation_matrix(
    returns_dict: Dict[str, List[float]],
) -> Dict[Tuple[str, str], float]:
    """Compute the full pairwise Pearson correlation matrix for a set of return series.

    All series are truncated to the length of the shortest one.

    Parameters
    ----------
    returns_dict:  mapping of series_name → list of per-period returns

    Returns all (name_a, name_b) pairs including self-correlations (= 1.0).
    Returns an empty dict for empty input.
    """
    names = list(returns_dict.keys())
    if not names:
        return {}

    min_len = min(len(v) for v in returns_dict.values())
    if min_len < 2:
        # Not enough data — return identity matrix
        result: Dict[Tuple[str, str], float] = {}
        for a in names:
            for b in names:
                result[(a, b)] = 1.0 if a == b else 0.0
        return result

    matrix = np.array(
        [returns_dict[n][:min_len] for n in names], dtype=float
    )
    corr_mat = np.corrcoef(matrix)  # shape (n_series, n_series)

    out: Dict[Tuple[str, str], float] = {}
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            v = float(corr_mat[i, j])
            out[(a, b)] = v if not math.isnan(v) else (1.0 if a == b else 0.0)
    return out


# ── Portfolio diversification ratio ──────────────────────────────────────────

def diversification_ratio(
    weights: Dict[str, float],
    returns_dict: Dict[str, List[float]],
) -> float:
    """Compute the portfolio diversification ratio.

    DR = (weighted average of individual volatilities) / (portfolio volatility)

    A DR > 1 indicates diversification benefit.  DR = 1 means all assets are
    perfectly correlated.

    Parameters
    ----------
    weights:      {asset_name: weight}  (weights should sum to 1, but are
                  normalised internally if they don't)
    returns_dict: {asset_name: [returns]}

    Returns 1.0 for empty, single-asset, or zero-variance portfolios.
    """
    names = [n for n in weights if n in returns_dict]
    if len(names) < 2:
        return 1.0

    # Normalise weights
    total_w = sum(weights[n] for n in names)
    if total_w == 0:
        return 1.0
    w = np.array([weights[n] / total_w for n in names], dtype=float)

    min_len = min(len(returns_dict[n]) for n in names)
    if min_len < 2:
        return 1.0

    ret_matrix = np.array(
        [returns_dict[n][:min_len] for n in names], dtype=float
    )  # shape (n_assets, n_bars)

    individual_vols = np.std(ret_matrix, axis=1, ddof=1)  # shape (n_assets,)
    weighted_avg_vol = float(np.dot(w, individual_vols))

    # Portfolio returns
    port_returns = np.dot(w, ret_matrix)
    port_vol = float(np.std(port_returns, ddof=1))

    if port_vol == 0:
        return 1.0
    return weighted_avg_vol / port_vol
