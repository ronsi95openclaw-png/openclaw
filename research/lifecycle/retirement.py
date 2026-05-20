"""Retirement condition checks for strategies."""
from __future__ import annotations

from typing import Tuple

from research.types import PerformanceMetrics


class RetirementChecker:
    """Evaluates strategies for retirement suitability.

    All recommendations are **advisory only** — a human operator must
    confirm before a strategy can actually be retired.
    """

    def __init__(
        self,
        neg_sharpe_days: int = 60,
        low_pf_days: int = 30,
        low_profit_factor: float = 0.5,
    ) -> None:
        self.neg_sharpe_days = neg_sharpe_days
        self.low_pf_days = low_pf_days
        self.low_profit_factor = low_profit_factor

    def check_retirement_conditions(
        self,
        strategy: str,
        metrics: PerformanceMetrics,
        days_neg_sharpe: int = 0,
        days_low_pf: int = 0,
        equity_curve: list[float] | None = None,
    ) -> Tuple[bool, str]:
        """Evaluate whether *strategy* should be recommended for retirement.

        Parameters
        ----------
        strategy:
            Strategy identifier (informational).
        metrics:
            Current performance metrics.
        days_neg_sharpe:
            Number of consecutive days with negative Sharpe ratio.
        days_low_pf:
            Number of consecutive days with profit_factor below threshold.
        equity_curve:
            Optional sequence of equity values.  If provided, all-negative
            bar-over-bar changes indicate terminal decline.

        Returns
        -------
        (should_retire, reason)
            Advisory signal — human confirmation is still required.
        """
        # Negative Sharpe for ≥ 60 days
        if days_neg_sharpe >= self.neg_sharpe_days:
            return True, (
                f"negative Sharpe ratio for {days_neg_sharpe} days "
                f"(threshold: {self.neg_sharpe_days})"
            )

        # Profit factor below 0.5 for ≥ 30 days
        if days_low_pf >= self.low_pf_days and metrics.profit_factor < self.low_profit_factor:
            return True, (
                f"profit_factor {metrics.profit_factor:.3f} below "
                f"{self.low_profit_factor} for {days_low_pf} days "
                f"(threshold: {self.low_pf_days})"
            )

        # Terminal decline: all bar-over-bar equity changes are negative
        if equity_curve and len(equity_curve) >= 2:
            changes = [
                equity_curve[i] - equity_curve[i - 1]
                for i in range(1, len(equity_curve))
            ]
            if all(c <= 0.0 for c in changes):
                return True, "equity curve in terminal decline (all bars non-positive)"

        return False, "no retirement conditions met"
