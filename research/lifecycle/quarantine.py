"""Degradation detection and quarantine logic."""
from __future__ import annotations

from research.types import PerformanceMetrics


class QuarantineManager:
    """Detects performance degradation and manages quarantine triggers.

    A strategy is flagged for degradation if **any** of the following
    conditions are met relative to its baseline metrics:

    * Sharpe ratio drops by more than 50 % from baseline.
    * Maximum drawdown exceeds 2× the baseline maximum drawdown.
    * Win-rate drops by more than 15 percentage-points from baseline.
    """

    def __init__(self, degradation_window_days: int = 7) -> None:
        self.degradation_window_days = degradation_window_days

    def check_degradation(
        self,
        strategy: str,
        rolling_metrics: PerformanceMetrics,
        baseline_metrics: PerformanceMetrics,
    ) -> bool:
        """Return *True* if *strategy* should be moved to DEGRADED state.

        Parameters
        ----------
        strategy:
            Strategy identifier (for logging purposes).
        rolling_metrics:
            Current rolling performance window.
        baseline_metrics:
            Historical baseline computed at the time of production deployment.
        """
        # Sharpe drops > 50 % from baseline
        if baseline_metrics.sharpe_ratio > 0:
            sharpe_drop = (
                (baseline_metrics.sharpe_ratio - rolling_metrics.sharpe_ratio)
                / baseline_metrics.sharpe_ratio
            )
            if sharpe_drop > 0.50:
                return True

        # Drawdown > 2× baseline max drawdown
        if rolling_metrics.max_drawdown_pct > 2.0 * baseline_metrics.max_drawdown_pct:
            return True

        # Win-rate drops > 15 percentage-points
        win_rate_drop = baseline_metrics.win_rate - rolling_metrics.win_rate
        if win_rate_drop > 0.15:
            return True

        return False

    def should_quarantine(self, strategy: str, days_degraded: int) -> bool:
        """Return *True* if a degraded strategy should now be quarantined.

        A strategy that has been in DEGRADED state for at least
        ``degradation_window_days`` days is automatically quarantined.
        """
        return days_degraded >= self.degradation_window_days
