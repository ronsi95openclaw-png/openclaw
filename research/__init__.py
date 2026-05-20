"""OpenClaw Research Engine — quantitative backtesting and analytics.

Phases:
  5  — Backtesting engine + analytics
  6  — Walk-forward + Monte Carlo validation
  7  — Optimization + parameter persistence
  8  — Market regime + adaptive portfolio
"""
from research.types import (
    Candle,
    Signal,
    BacktestTrade,
    BacktestResult,
    PerformanceMetrics,
    WalkForwardWindow,
    WalkForwardResult,
    MonteCarloResult,
    RegimeState,
    AllocationWeights,
    ExecutionRecord,
    VenueScore,
    OptimizationResult,
)

__all__ = [
    "Candle",
    "Signal",
    "BacktestTrade",
    "BacktestResult",
    "PerformanceMetrics",
    "WalkForwardWindow",
    "WalkForwardResult",
    "MonteCarloResult",
    "RegimeState",
    "AllocationWeights",
    "ExecutionRecord",
    "VenueScore",
    "OptimizationResult",
]
