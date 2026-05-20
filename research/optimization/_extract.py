"""Internal helper: extract a scalar metric from a BacktestResult."""
from __future__ import annotations

from typing import Any

from research.types import BacktestResult, PerformanceMetrics


def extract_metric(result: BacktestResult, metric: str) -> float:
    """Extract a scalar score from BacktestResult for optimization.

    Looks for the metric in:
      1. ``result.metadata["metrics"]`` (a PerformanceMetrics object).
      2. ``result.metadata`` directly (flat key → float).
      3. Falls back to 0.0 with a warning.

    Args:
        result: Completed backtest result.
        metric: Field name, e.g. ``"sharpe_ratio"``, ``"total_return_pct"``.

    Returns:
        Scalar float suitable for maximisation.
    """
    # 1. Try PerformanceMetrics object stored in metadata
    pm = result.metadata.get("metrics")
    if isinstance(pm, PerformanceMetrics) and hasattr(pm, metric):
        val = getattr(pm, metric)
        if isinstance(val, (int, float)):
            return float(val)

    # 2. Try flat metadata key
    if metric in result.metadata:
        val = result.metadata[metric]
        if isinstance(val, (int, float)):
            return float(val)

    # 3. Common fields derived directly from BacktestResult
    if metric == "total_return_pct":
        if result.initial_capital > 0:
            return (result.final_capital - result.initial_capital) / result.initial_capital * 100.0
    if metric == "final_capital":
        return float(result.final_capital)

    # 4. Auto-compute PerformanceMetrics on demand and cache in metadata
    # Covers: sharpe_ratio, sortino_ratio, calmar_ratio, win_rate, profit_factor, etc.
    _PERF_METRICS = {
        "sharpe_ratio", "sortino_ratio", "calmar_ratio", "omega_ratio",
        "win_rate", "profit_factor", "payoff_ratio", "expectancy",
        "max_drawdown_pct", "total_return_pct", "annualized_return_pct",
        "recovery_factor", "total_trades", "cagr",
    }
    if metric in _PERF_METRICS:
        try:
            from research.analytics.performance import compute_performance_metrics
            pm = compute_performance_metrics(result)
            result.metadata["metrics"] = pm          # cache for next call
            if hasattr(pm, metric):
                val = getattr(pm, metric)
                if isinstance(val, (int, float)):
                    return float(val)
        except Exception:
            pass

    import logging  # noqa: PLC0415
    logging.getLogger(__name__).warning(
        "extract_metric: metric '%s' not found in BacktestResult; returning 0.0", metric
    )
    return 0.0
