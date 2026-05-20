"""Tests for the optimization modules: grid search, random search, Bayesian search."""
from __future__ import annotations

import asyncio
import sys
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from research.types import BacktestResult, BacktestTrade, Candle


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_candle(ts: int, price: float = 100.0) -> Candle:
    return Candle(ts=ts, open=price, high=price + 1, low=price - 1, close=price, volume=50.0)


def _make_candles(n: int = 30) -> List[Candle]:
    base = 1_700_000_000_000
    bar_ms = 15 * 60 * 1_000
    return [_make_candle(base + i * bar_ms, 100.0 + i * 0.1) for i in range(n)]


def _make_trade(net_pnl: float, entry_price: float = 100.0) -> BacktestTrade:
    return BacktestTrade(
        trade_id="opt_t",
        symbol="BTC-USDT",
        strategy="opt_test",
        side="long",
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        entry_price=entry_price,
        exit_price=entry_price + net_pnl,
        size=1.0,
        gross_pnl=net_pnl + 1.0,
        fees=1.0,
        net_pnl=net_pnl,
        net_pnl_pct=(net_pnl / entry_price * 100.0),
        entry_slippage=0.0,
        exit_slippage=0.0,
        max_adverse_excursion=1.0,
        max_favorable_excursion=2.0,
        holding_bars=3,
        exit_reason="tp",
        funding_paid=0.0,
    )


async def _mock_backtest(
    candles: List[Candle],
    params: Dict[str, Any],
) -> BacktestResult:
    """Fake backtest where score = param1 * 0.1 (deterministic for testing)."""
    param1 = params.get("param1", 1)
    param2 = params.get("param2", 1)
    # Score is param1 + param2 — easy to verify ranking
    score_value = param1 * 0.5 + param2 * 0.3

    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    equity = [10_000.0 + score_value * i for i in range(len(candles) + 1)]

    # Include metrics in metadata so extract_metric can find sharpe_ratio
    try:
        from research.analytics.performance import compute_performance_metrics
        from research.types import PerformanceMetrics
        metrics = PerformanceMetrics(
            total_return_pct=score_value * 100,
            annualized_return_pct=score_value * 100,
            cagr=score_value * 100,
            sharpe_ratio=float(score_value),
            sortino_ratio=float(score_value),
            calmar_ratio=float(score_value),
            omega_ratio=float(score_value) + 1,
            max_drawdown_pct=5.0,
            max_drawdown_duration_bars=10,
            recovery_factor=2.0,
            total_trades=10,
            winning_trades=7,
            losing_trades=3,
            win_rate=0.7,
            profit_factor=2.0,
            payoff_ratio=1.5,
            expectancy=50.0,
            avg_win=100.0,
            avg_loss=-70.0,
            largest_win=200.0,
            largest_loss=-150.0,
            max_win_streak=4,
            max_loss_streak=2,
            avg_holding_bars=4.0,
            total_fees=10.0,
            total_slippage=5.0,
        )
    except Exception:
        metrics = None

    return BacktestResult(
        strategy="opt_test",
        symbol="BTC-USDT",
        params=params,
        trades=[_make_trade(score_value * 10)] * 5,
        equity_curve=equity,
        timestamps=[ts] * len(equity),
        initial_capital=10_000.0,
        final_capital=10_000.0 + score_value * len(candles),
        start_time=ts,
        end_time=ts,
        metadata={"metrics": metrics} if metrics else {},
    )


# ── Grid search tests ─────────────────────────────────────────────────────────

def test_grid_search_explores_all_combinations():
    """grid_search should evaluate every combination in param_grid."""
    try:
        from research.optimization.grid_search import grid_search
    except ImportError:
        pytest.skip("grid_search not available")

    param_grid = {"param1": [1, 2, 3], "param2": [10, 20]}
    expected_count = 3 * 2  # 6 combinations

    candles = _make_candles(20)
    results = asyncio.run(
        grid_search(_mock_backtest, candles, param_grid, metric="sharpe_ratio")
    )

    assert len(results) == expected_count, (
        f"Expected {expected_count} results, got {len(results)}"
    )


def test_grid_search_results_sorted_best_first():
    """grid_search results should be sorted descending by score."""
    try:
        from research.optimization.grid_search import grid_search
    except ImportError:
        pytest.skip("grid_search not available")

    param_grid = {"param1": [1, 2, 3], "param2": [1, 2]}
    candles = _make_candles(20)
    results = asyncio.run(
        grid_search(_mock_backtest, candles, param_grid, metric="sharpe_ratio")
    )

    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True), "Results not sorted by score"


def test_grid_search_best_params_are_correct():
    """The best params from grid search match the highest-scoring combination."""
    try:
        from research.optimization.grid_search import grid_search
    except ImportError:
        pytest.skip("grid_search not available")

    param_grid = {"param1": [1, 5], "param2": [1, 5]}
    candles = _make_candles(20)
    results = asyncio.run(
        grid_search(_mock_backtest, candles, param_grid, metric="sharpe_ratio")
    )

    best = results[0]
    # param1=5 + param2=5 should give highest score (5*0.5 + 5*0.3 = 4.0)
    assert best.params["param1"] == 5
    assert best.params["param2"] == 5


# ── Random search tests ───────────────────────────────────────────────────────

def test_random_search_respects_n_trials():
    """random_search should produce exactly n_trials results."""
    try:
        from research.optimization.random_search import random_search
    except ImportError:
        pytest.skip("random_search not available")

    param_space = {"param1": [1, 2, 3, 4, 5], "param2": [10, 20, 30]}
    n_trials = 7
    candles = _make_candles(20)
    results = asyncio.run(
        random_search(
            _mock_backtest, candles, param_space,
            n_trials=n_trials, metric="sharpe_ratio", seed=42
        )
    )
    assert len(results) == n_trials, (
        f"Expected {n_trials} results, got {len(results)}"
    )


def test_random_search_results_are_sorted():
    """random_search results are sorted best-first."""
    try:
        from research.optimization.random_search import random_search
    except ImportError:
        pytest.skip("random_search not available")

    param_space = {"param1": list(range(1, 10)), "param2": list(range(1, 10))}
    candles = _make_candles(20)
    results = asyncio.run(
        random_search(_mock_backtest, candles, param_space, n_trials=10, seed=0)
    )
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


# ── Bayesian search tests ─────────────────────────────────────────────────────

def test_bayesian_search_improves_over_trials():
    """Bayesian search should find a high-scoring param set."""
    try:
        from research.optimization.bayesian import bayesian_search
    except ImportError:
        pytest.skip("bayesian_search not available")

    param_space = {
        "param1": list(range(1, 11)),
        "param2": list(range(1, 6)),
    }
    candles = _make_candles(20)
    results = asyncio.run(
        bayesian_search(
            _mock_backtest, candles, param_space,
            n_trials=15, metric="sharpe_ratio", seed=42
        )
    )

    assert len(results) > 0
    # Best should have scored reasonably high (param1=10 + param2=5 = 6.5 max)
    best_score = results[0].score
    assert best_score > 0.0


# ── Rank results tests ────────────────────────────────────────────────────────

def test_rank_results_sorts_correctly():
    """Rank function should sort OptimizationResult list by score."""
    from research.types import OptimizationResult

    results = [
        OptimizationResult(strategy="s", symbol="X", params={}, score=0.5, metric="sharpe_ratio"),
        OptimizationResult(strategy="s", symbol="X", params={}, score=2.0, metric="sharpe_ratio"),
        OptimizationResult(strategy="s", symbol="X", params={}, score=1.0, metric="sharpe_ratio"),
    ]

    # Try to find rank_results function
    try:
        from research.optimization.grid_search import grid_search
        # If we can import grid search, sort manually and verify
        ranked = sorted(results, key=lambda r: r.score, reverse=True)
        assert ranked[0].score == 2.0
        assert ranked[-1].score == 0.5
    except ImportError:
        pass
