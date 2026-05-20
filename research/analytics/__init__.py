"""Analytics sub-package for the OpenClaw research engine.

Exports the primary functions used by callers:

Performance
    compute_performance_metrics, compute_equity_curve,
    rolling_returns, rolling_sharpe, rolling_volatility

Expectancy
    compute_expectancy, compute_profit_factor, compute_payoff_ratio,
    streak_analysis, edge_ratio

Drawdown
    compute_max_drawdown, compute_drawdown_series, drawdown_duration,
    recovery_factor, calmar_ratio

Risk-adjusted
    sharpe_ratio, sortino_ratio, omega_ratio, information_ratio,
    value_at_risk, conditional_value_at_risk

Exposure
    time_in_market, avg_leverage_used, compute_mae_mfe_analysis,
    adverse_excursion_efficiency

Correlation
    strategy_correlation, pair_correlation, correlation_matrix,
    diversification_ratio
"""
from __future__ import annotations

from research.analytics.performance import (
    compute_equity_curve,
    compute_performance_metrics,
    rolling_returns,
    rolling_sharpe,
    rolling_volatility,
)
from research.analytics.expectancy import (
    compute_expectancy,
    compute_payoff_ratio,
    compute_profit_factor,
    edge_ratio,
    streak_analysis,
)
from research.analytics.drawdown import (
    calmar_ratio,
    compute_drawdown_series,
    compute_max_drawdown,
    drawdown_duration,
    recovery_factor,
)
from research.analytics.risk_adjusted import (
    conditional_value_at_risk,
    information_ratio,
    omega_ratio,
    sharpe_ratio,
    sortino_ratio,
    value_at_risk,
)
from research.analytics.exposure_analysis import (
    adverse_excursion_efficiency,
    avg_leverage_used,
    compute_mae_mfe_analysis,
    time_in_market,
)
from research.analytics.correlation import (
    correlation_matrix,
    diversification_ratio,
    pair_correlation,
    strategy_correlation,
)

__all__ = [
    # performance
    "compute_performance_metrics",
    "compute_equity_curve",
    "rolling_returns",
    "rolling_sharpe",
    "rolling_volatility",
    # expectancy
    "compute_expectancy",
    "compute_profit_factor",
    "compute_payoff_ratio",
    "streak_analysis",
    "edge_ratio",
    # drawdown
    "compute_max_drawdown",
    "compute_drawdown_series",
    "drawdown_duration",
    "recovery_factor",
    "calmar_ratio",
    # risk-adjusted
    "sharpe_ratio",
    "sortino_ratio",
    "omega_ratio",
    "information_ratio",
    "value_at_risk",
    "conditional_value_at_risk",
    # exposure
    "time_in_market",
    "avg_leverage_used",
    "compute_mae_mfe_analysis",
    "adverse_excursion_efficiency",
    # correlation
    "strategy_correlation",
    "pair_correlation",
    "correlation_matrix",
    "diversification_ratio",
]
