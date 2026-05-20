"""Walk-forward validation engine — Phase 6."""
from __future__ import annotations

from research.walkforward.rolling_windows import generate_windows, anchored_windows
from research.walkforward.engine import WalkForwardEngine
from research.walkforward.validation import (
    compute_parameter_stability,
    compute_is_oos_ratio,
    regime_breakdown,
    combined_oos_metrics,
)
from research.walkforward.overfit_detection import (
    overfit_score,
    monte_carlo_overfit_test,
    deflated_sharpe_ratio,
)

__all__ = [
    "WalkForwardEngine",
    "generate_windows",
    "anchored_windows",
    "compute_parameter_stability",
    "compute_is_oos_ratio",
    "regime_breakdown",
    "combined_oos_metrics",
    "overfit_score",
    "monte_carlo_overfit_test",
    "deflated_sharpe_ratio",
]
