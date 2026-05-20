"""Probability-of-ruin calculations: simulation-based and analytic."""
from __future__ import annotations

import math
from typing import List

import numpy as np

from research.types import BacktestTrade


def probability_of_ruin(
    equity_paths: np.ndarray,
    initial_capital: float,
    ruin_threshold: float = 0.5,
) -> float:
    """Probability that equity ever drops below ``initial * (1 - ruin_threshold)``.

    Args:
        equity_paths:    Array of shape ``(n_simulations, n_steps)`` with equity
                         values (absolute currency units).
        initial_capital: Starting equity used to define the ruin level.
        ruin_threshold:  Fraction of initial capital that constitutes ruin
                         (default 0.5 → 50 % drawdown = ruin).

    Returns:
        Probability in [0, 1].
    """
    if equity_paths.size == 0 or initial_capital <= 0:
        return 0.0

    ruin_level = initial_capital * (1.0 - ruin_threshold)
    # Each simulation is ruined if its minimum equity ever goes below ruin_level
    min_equity = np.min(equity_paths, axis=1)  # (n_simulations,)
    ruined = np.mean(min_equity < ruin_level)
    return float(ruined)


def probability_of_ruin_analytic(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    max_drawdown_threshold: float = 0.5,
    n_trades: int = 1000,
) -> float:
    """Gambler's ruin approximation for rapid probability-of-ruin estimation.

    Uses the classical result for a biased random walk:
        P(ruin) ≈ ((loss / win) ^ (capital / avg_loss)) ^ fraction
    adapted to a fractional drawdown threshold via the edge ratio.

    Args:
        win_rate:               Fraction of winning trades (0–1).
        avg_win:                Average profit per winning trade (absolute $).
        avg_loss:               Average loss per losing trade (absolute $, positive).
        max_drawdown_threshold: Drawdown fraction considered ruin (default 0.5).
        n_trades:               Number of trades to project (controls time horizon).

    Returns:
        Estimated ruin probability in [0, 1].
    """
    if avg_win <= 0 or avg_loss <= 0:
        return 1.0 if win_rate < 0.5 else 0.0

    loss_rate = 1.0 - win_rate
    expectancy_per_trade = win_rate * avg_win - loss_rate * avg_loss

    # If expected value is negative, ruin probability approaches 1.0
    if expectancy_per_trade <= 0:
        return min(1.0, 1.0 - expectancy_per_trade / (avg_win + avg_loss))

    # Kelly-based ruin estimate
    # Edge = (win_rate * avg_win - loss_rate * avg_loss) / avg_loss
    edge = expectancy_per_trade / avg_loss
    # Odds = avg_win / avg_loss
    odds = avg_win / avg_loss

    # Probability of ruin = ((1 - edge/odds) / (1 + edge))^(threshold_steps)
    # where threshold_steps ≈ max_drawdown_threshold / (avg_loss / initial_capital)
    # We normalise to a unit capital problem.
    # Classical gambler's ruin: p_ruin = ((q/p)^N - (q/p)^M) / (1 - (q/p)^M)
    # Approximate with the asymptotic formula for large thresholds.

    # Simplified: use the ratio r = (loss_rate / win_rate) * (1/odds)
    r = (loss_rate / win_rate) * (1.0 / odds) if win_rate > 0 else 1.0

    if abs(r - 1.0) < 1e-9:
        # Symmetric random walk: ruin probability = threshold / 1 ≈ threshold
        return float(np.clip(max_drawdown_threshold, 0.0, 1.0))

    if r >= 1.0:
        return 1.0

    # The number of "units" to ruin is scaled by threshold and trade count
    units_to_ruin = max_drawdown_threshold * n_trades
    p_ruin = r ** units_to_ruin
    return float(np.clip(p_ruin, 0.0, 1.0))


def capital_required_for_safety(
    trades: List[BacktestTrade],
    target_ruin_probability: float = 0.01,
    n_simulations: int = 10_000,
    seed: int = 42,
) -> float:
    """Estimate how much starting capital is needed to keep P(ruin) < target.

    Uses binary search over initial capital multipliers, running Monte Carlo
    simulations at each candidate value.

    Args:
        trades:                  Historical trade list.
        target_ruin_probability: Desired maximum P(ruin) (default 1 %).
        n_simulations:           Number of MC paths per evaluation.
        seed:                    Random seed.

    Returns:
        Capital multiplier (e.g. 2.0 means twice the original capital is
        needed).  Returns 1.0 if the base capital is already safe.
    """
    if not trades:
        return 1.0

    from research.montecarlo.simulations import MonteCarloSimulator  # noqa: PLC0415

    sim = MonteCarloSimulator(n_simulations=n_simulations, seed=seed)

    # Base capital is the sum of all entry notional values; proxy via sum of gross pnl
    # We work with unit capital (1.0) and scale later.
    base_capital = 1.0

    paths = sim.simulate_equity_paths(trades, initial_capital=base_capital)

    p_ruin = probability_of_ruin(paths, base_capital, ruin_threshold=0.5)
    if p_ruin <= target_ruin_probability:
        return 1.0

    # Binary search for the multiplier M such that if we start with M * base_capital
    # the drawdown threshold (0.5 * M * base_capital) keeps P(ruin) below target.
    # Equivalently, we scale down the returns — more capital means smaller fractional
    # drawdowns.  We scale pnl magnitudes down by 1/M which reduces ruin probability.

    lo, hi = 1.0, 100.0
    for _ in range(20):  # 20 iterations gives ~1e-6 precision
        mid = (lo + hi) / 2.0
        # Simulate with scaled pnls
        scaled_trades = _scale_trades(trades, 1.0 / mid)
        paths_scaled = sim.simulate_equity_paths(
            scaled_trades, initial_capital=base_capital * mid
        )
        p = probability_of_ruin(
            paths_scaled, base_capital * mid, ruin_threshold=0.5
        )
        if p <= target_ruin_probability:
            hi = mid
        else:
            lo = mid

    return float((lo + hi) / 2.0)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _scale_trades(
    trades: List[BacktestTrade], factor: float
) -> List[BacktestTrade]:
    """Return shallow copies of trades with pnl fields scaled by factor.

    Only ``net_pnl_pct`` matters for equity path simulation.
    """
    from dataclasses import replace  # noqa: PLC0415

    return [
        replace(t, net_pnl_pct=t.net_pnl_pct * factor) for t in trades
    ]
