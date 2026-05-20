"""Position exposure analytics.

Analyses how much time the strategy spends in-market, leverage utilisation,
and the quality of entry/exit timing through MAE/MFE statistics.
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from research.types import BacktestTrade


def time_in_market(trades: List[BacktestTrade], total_bars: int) -> float:
    """Fraction of total bars with an open position.

    Parameters
    ----------
    trades:      completed trade records (each has holding_bars)
    total_bars:  total number of bars in the backtest period

    Returns a float in [0.0, 1.0].  Returns 0.0 when total_bars <= 0.
    """
    if total_bars <= 0 or not trades:
        return 0.0
    bars_in_market = sum(max(1, t.holding_bars) for t in trades)
    return min(1.0, bars_in_market / total_bars)


def avg_leverage_used(trades: List[BacktestTrade]) -> float:
    """Compute the average notional leverage used across all trades.

    For each trade, leverage proxy = |gross_pnl| / (|net_pnl| + fees) when
    fees and slippage are small.  Since the BacktestTrade does not carry the
    leverage multiplier directly, we infer it from:

        leverage = gross_pnl / ((exit_price - entry_price) * size)    [long]
        leverage = gross_pnl / ((entry_price - exit_price) * size)    [short]

    Falls back to 1.0 when the price move is zero.
    Returns 1.0 for an empty trade list.
    """
    if not trades:
        return 1.0
    leverages: List[float] = []
    for t in trades:
        if t.side == "long":
            price_move = (t.exit_price - t.entry_price) * t.size
        else:
            price_move = (t.entry_price - t.exit_price) * t.size
        if abs(price_move) > 1e-12:
            lev = abs(t.gross_pnl / price_move)
            leverages.append(lev)
        else:
            leverages.append(1.0)
    return float(np.mean(leverages)) if leverages else 1.0


def compute_mae_mfe_analysis(trades: List[BacktestTrade]) -> Dict[str, Any]:
    """Statistical analysis of MAE and MFE distributions.

    Returns a dictionary with summary statistics for both MAE and MFE:
        mae_mean, mae_std, mae_median, mae_p90, mae_p95
        mfe_mean, mfe_std, mfe_median, mfe_p90, mfe_p95
        entry_efficiency_mean  – MFE / (MFE + MAE) per trade
        n_trades               – number of trades analysed
    """
    if not trades:
        return {
            "mae_mean":              0.0,
            "mae_std":               0.0,
            "mae_median":            0.0,
            "mae_p90":               0.0,
            "mae_p95":               0.0,
            "mfe_mean":              0.0,
            "mfe_std":               0.0,
            "mfe_median":            0.0,
            "mfe_p90":               0.0,
            "mfe_p95":               0.0,
            "entry_efficiency_mean": 0.0,
            "n_trades":              0,
        }

    maes = np.array([t.max_adverse_excursion for t in trades], dtype=float)
    mfes = np.array([t.max_favorable_excursion for t in trades], dtype=float)

    # Entry efficiency: how much of the total excursion range was favorable
    # efficiency = MFE / (MFE + MAE), clamped to [0, 1]
    denom = mfes + maes
    with np.errstate(divide="ignore", invalid="ignore"):
        efficiencies = np.where(denom > 0, mfes / denom, 0.5)

    return {
        "mae_mean":              float(np.mean(maes)),
        "mae_std":               float(np.std(maes, ddof=1)) if len(maes) > 1 else 0.0,
        "mae_median":            float(np.median(maes)),
        "mae_p90":               float(np.percentile(maes, 90)),
        "mae_p95":               float(np.percentile(maes, 95)),
        "mfe_mean":              float(np.mean(mfes)),
        "mfe_std":               float(np.std(mfes, ddof=1)) if len(mfes) > 1 else 0.0,
        "mfe_median":            float(np.median(mfes)),
        "mfe_p90":               float(np.percentile(mfes, 90)),
        "mfe_p95":               float(np.percentile(mfes, 95)),
        "entry_efficiency_mean": float(np.mean(efficiencies)),
        "n_trades":              len(trades),
    }


def adverse_excursion_efficiency(trades: List[BacktestTrade]) -> float:
    """Measure how well the strategy converts MFE into actual profit.

    Computes: mean(net_pnl / mfe) for winning trades.

    A ratio of 1.0 means all favorable excursion was captured.
    A ratio of 0.5 means half the MFE was given back before exit.
    Returns 0.0 for an empty trade list or if no trade had positive MFE.
    """
    if not trades:
        return 0.0
    eligible = [t for t in trades if t.max_favorable_excursion > 0]
    if not eligible:
        return 0.0
    efficiencies = [
        min(1.0, t.net_pnl / t.max_favorable_excursion)
        for t in eligible
    ]
    return float(np.mean(efficiencies))
