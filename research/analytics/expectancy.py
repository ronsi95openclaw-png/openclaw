"""Trade expectancy and distribution analytics.

All functions operate purely on lists of BacktestTrade — no pandas required.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

import numpy as np

from research.types import BacktestTrade


def compute_expectancy(trades: List[BacktestTrade]) -> float:
    """Average expected profit/loss per trade (in USD).

    Expectancy = (Win Rate × Avg Win) + (Loss Rate × Avg Loss)

    Returns 0.0 for an empty trade list.
    """
    if not trades:
        return 0.0
    return float(np.mean([t.net_pnl for t in trades]))


def compute_profit_factor(trades: List[BacktestTrade]) -> float:
    """Gross profit divided by gross loss.

    Returns inf if there are no losing trades (and at least one winner).
    Returns 0.0 if there are no winning trades.
    Returns 0.0 for an empty trade list.
    """
    if not trades:
        return 0.0
    gross_profit = sum(t.net_pnl for t in trades if t.net_pnl > 0)
    gross_loss   = sum(abs(t.net_pnl) for t in trades if t.net_pnl < 0)
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def compute_payoff_ratio(trades: List[BacktestTrade]) -> float:
    """Average win divided by average loss magnitude.

    Returns inf if there are no losing trades (and at least one winner).
    Returns 0.0 if there are no winning trades or no trades.
    """
    if not trades:
        return 0.0
    wins   = [t.net_pnl for t in trades if t.net_pnl > 0]
    losses = [abs(t.net_pnl) for t in trades if t.net_pnl < 0]
    if not wins:
        return 0.0
    avg_win  = float(np.mean(wins))
    if not losses:
        return float("inf")
    avg_loss = float(np.mean(losses))
    if avg_loss == 0:
        return float("inf")
    return avg_win / avg_loss


def streak_analysis(trades: List[BacktestTrade]) -> Dict[str, Any]:
    """Analyse consecutive win/loss streaks.

    Returns a dictionary with:
        max_win_streak    – longest consecutive winning run
        max_loss_streak   – longest consecutive losing run
        current_streak    – positive = wins in a row, negative = losses in a row
        avg_streak_length – mean absolute streak length (across all streaks)
    """
    if not trades:
        return {
            "max_win_streak": 0,
            "max_loss_streak": 0,
            "current_streak": 0,
            "avg_streak_length": 0.0,
        }

    max_win  = 0
    max_loss = 0
    cur_win  = 0
    cur_loss = 0
    streak_lengths: List[int] = []

    for t in trades:
        if t.net_pnl >= 0:
            if cur_loss > 0:
                streak_lengths.append(cur_loss)
                cur_loss = 0
            cur_win += 1
            max_win = max(max_win, cur_win)
        else:
            if cur_win > 0:
                streak_lengths.append(cur_win)
                cur_win = 0
            cur_loss += 1
            max_loss = max(max_loss, cur_loss)

    # Flush final streak
    if cur_win > 0:
        streak_lengths.append(cur_win)
    elif cur_loss > 0:
        streak_lengths.append(cur_loss)

    current_streak = cur_win if cur_win > 0 else -cur_loss

    avg_len = float(np.mean(streak_lengths)) if streak_lengths else 0.0

    return {
        "max_win_streak":    max_win,
        "max_loss_streak":   max_loss,
        "current_streak":    current_streak,
        "avg_streak_length": avg_len,
    }


def edge_ratio(trades: List[BacktestTrade]) -> float:
    """Compute the edge ratio: average MFE / average MAE (price-normalised).

    Edge ratio measures how well the strategy captures favourable moves
    relative to adverse moves.

    Returns 1.0 when MAE is zero (perfect edge — no adverse excursion).
    Returns 0.0 for empty or single-trade lists.
    """
    if not trades:
        return 0.0

    # Filter trades that have valid entry prices
    valid = [t for t in trades if t.entry_price > 0]
    if not valid:
        return 0.0

    avg_mfe = float(np.mean([
        t.max_favorable_excursion / t.entry_price for t in valid
    ]))
    avg_mae = float(np.mean([
        t.max_adverse_excursion / t.entry_price for t in valid
    ]))

    if avg_mae == 0:
        return float("inf") if avg_mfe > 0 else 1.0
    return avg_mfe / avg_mae
