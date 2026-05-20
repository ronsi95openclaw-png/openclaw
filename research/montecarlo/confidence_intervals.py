"""Confidence intervals and survivability estimates from Monte Carlo paths."""
from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np

from research.types import BacktestTrade, MonteCarloResult


# ── Public functions ──────────────────────────────────────────────────────────

def return_confidence_interval(
    equity_paths: np.ndarray,
    initial_capital: float,
    confidence: float = 0.95,
) -> Tuple[float, float, float]:
    """Compute the confidence interval for annualised return.

    Args:
        equity_paths:    ``(n_simulations, n_steps)`` equity curves.
        initial_capital: Starting equity value.
        confidence:      Confidence level, e.g. 0.95 for 95 %.

    Returns:
        ``(median, lower, upper)`` annualised return as fractions (not percent).
    """
    if equity_paths.size == 0 or initial_capital <= 0:
        return (0.0, 0.0, 0.0)

    final_equity = equity_paths[:, -1]  # (n_simulations,)
    total_returns = final_equity / initial_capital - 1.0  # fractional total return

    # Annualise using n_steps as proxy for one year of data
    n_steps = equity_paths.shape[1] - 1
    if n_steps <= 0:
        return (0.0, 0.0, 0.0)

    # Compound annual return: (1 + R_total) ^ (252/n_steps) - 1
    # We use 252 as the default year length
    annual_factor = 252.0 / max(n_steps, 1)
    annual_returns = (1.0 + total_returns) ** annual_factor - 1.0

    alpha = 1.0 - confidence
    lower = float(np.percentile(annual_returns, alpha / 2.0 * 100.0))
    upper = float(np.percentile(annual_returns, (1.0 - alpha / 2.0) * 100.0))
    median = float(np.median(annual_returns))

    return (median, lower, upper)


def drawdown_confidence_interval(
    equity_paths: np.ndarray,
    confidence: float = 0.95,
) -> Tuple[float, float, float]:
    """Compute the confidence interval for maximum drawdown.

    Args:
        equity_paths: ``(n_simulations, n_steps)`` equity curves.
        confidence:   Confidence level, e.g. 0.95.

    Returns:
        ``(median, worst_case, best_case)`` max-drawdown as positive fractions.
        ``worst_case`` is the high-drawdown (bad) percentile;
        ``best_case`` is the low-drawdown (good) percentile.
    """
    if equity_paths.size == 0:
        return (0.0, 0.0, 0.0)

    # Compute max drawdown for each simulation path
    running_max = np.maximum.accumulate(equity_paths, axis=1)
    # Avoid division by zero
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdowns = np.where(
            running_max > 0,
            (running_max - equity_paths) / running_max,
            0.0,
        )
    max_drawdowns = np.max(drawdowns, axis=1)  # (n_simulations,)

    alpha = 1.0 - confidence
    median = float(np.median(max_drawdowns))
    # worst_case = upper percentile (big drawdown = bad)
    worst_case = float(np.percentile(max_drawdowns, (1.0 - alpha / 2.0) * 100.0))
    # best_case  = lower percentile (small drawdown = good)
    best_case = float(np.percentile(max_drawdowns, alpha / 2.0 * 100.0))

    return (median, worst_case, best_case)


def survivability_estimate(
    equity_paths: np.ndarray,
    years: float = 1.0,
) -> float:
    """Probability that equity is still positive after ``years``.

    Args:
        equity_paths: ``(n_simulations, n_steps)`` equity curves.
        years:        Number of years to project (scales the path).

    Returns:
        Probability in [0, 1].
    """
    if equity_paths.size == 0:
        return 0.0

    # If years != 1.0, we pick the column index nearest to the fraction of
    # the total path that corresponds to ``years``.  For simplicity, we use
    # the last column when years >= 1.0 and interpolate for fractional years.
    n_steps = equity_paths.shape[1] - 1
    if n_steps <= 0:
        return float(np.mean(equity_paths[:, 0] > 0))

    col = min(int(round(n_steps * min(years, 1.0))), n_steps)
    survived = np.mean(equity_paths[:, col] > 0)
    return float(survived)


def compute_monte_carlo_result(
    trades: List[BacktestTrade],
    initial_capital: float,
    n_simulations: int = 10_000,
    confidence: float = 0.95,
    ruin_threshold: float = 0.5,
    seed: int = 42,
) -> MonteCarloResult:
    """Run full Monte Carlo analysis and return a ``MonteCarloResult``.

    Args:
        trades:          Historical trades from a backtest.
        initial_capital: Starting portfolio value.
        n_simulations:   Number of simulation paths.
        confidence:      Confidence level for intervals.
        ruin_threshold:  Drawdown fraction that constitutes ruin.
        seed:            Random seed.

    Returns:
        Populated ``MonteCarloResult`` dataclass.
    """
    from research.montecarlo.simulations import MonteCarloSimulator  # noqa: PLC0415
    from research.montecarlo.ruin_probability import (  # noqa: PLC0415
        probability_of_ruin,
        capital_required_for_safety,
    )

    sim = MonteCarloSimulator(n_simulations=n_simulations, seed=seed)
    equity_paths = sim.simulate_equity_paths(trades, initial_capital=initial_capital)

    median_ret, ret_lower, ret_upper = return_confidence_interval(
        equity_paths, initial_capital, confidence
    )
    dd_median, dd_worst, _dd_best = drawdown_confidence_interval(
        equity_paths, confidence
    )
    p_ruin = probability_of_ruin(equity_paths, initial_capital, ruin_threshold)
    survivability = survivability_estimate(equity_paths, years=1.0)

    # Capital adequacy: multiplier needed to bring P(ruin) to 1 %
    cap_multiplier = capital_required_for_safety(
        trades,
        target_ruin_probability=0.01,
        n_simulations=min(n_simulations, 2000),  # use fewer sims for speed
        seed=seed,
    )

    return MonteCarloResult(
        n_simulations=n_simulations,
        confidence_level=confidence,
        max_drawdown_median=dd_median,
        max_drawdown_p5=dd_worst,    # 5th percentile worst drawdown (p95 path)
        max_drawdown_p95=_dd_best,   # 95th percentile best drawdown (p5 path)
        ruin_probability=p_ruin,
        expected_annual_return=median_ret,
        return_ci_lower=ret_lower,
        return_ci_upper=ret_upper,
        survivability=survivability,
        capital_adequacy_multiplier=cap_multiplier,
    )
