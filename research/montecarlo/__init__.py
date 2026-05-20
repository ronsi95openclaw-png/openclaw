"""Monte Carlo validation engine — Phase 6."""
from __future__ import annotations

from research.montecarlo.engine import MonteCarloEngine
from research.montecarlo.simulations import MonteCarloSimulator
from research.montecarlo.ruin_probability import (
    probability_of_ruin,
    probability_of_ruin_analytic,
    capital_required_for_safety,
)
from research.montecarlo.confidence_intervals import (
    return_confidence_interval,
    drawdown_confidence_interval,
    survivability_estimate,
    compute_monte_carlo_result,
)

__all__ = [
    "MonteCarloEngine",
    "MonteCarloSimulator",
    "probability_of_ruin",
    "probability_of_ruin_analytic",
    "capital_required_for_safety",
    "return_confidence_interval",
    "drawdown_confidence_interval",
    "survivability_estimate",
    "compute_monte_carlo_result",
]
