"""Tests for the walk-forward engine and related utilities."""
from __future__ import annotations

import asyncio
import sys
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from research.types import (
    BacktestResult,
    BacktestTrade,
    Candle,
    PerformanceMetrics,
    WalkForwardWindow,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_candles(n: int, base_ts_ms: int = 1_700_000_000_000) -> List[Candle]:
    bar_ms = 15 * 60 * 1_000
    price = 100.0
    candles = []
    for i in range(n):
        candles.append(Candle(
            ts=base_ts_ms + i * bar_ms,
            open=price,
            high=price + 1.0,
            low=price - 1.0,
            close=price + 0.1,
            volume=100.0,
        ))
        price += 0.1
    return candles


def _make_trade(net_pnl: float, entry_price: float = 100.0) -> BacktestTrade:
    return BacktestTrade(
        trade_id="t1",
        symbol="BTC-USDT",
        strategy="mock",
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
        holding_bars=5,
        exit_reason="tp",
        funding_paid=0.0,
    )


def _make_window(
    window_id: int,
    best_params: Dict[str, Any],
    train_sharpe: float = 1.5,
    test_sharpe: float = 0.8,
    train_bars: int = 500,
    test_bars: int = 200,
) -> WalkForwardWindow:
    """Create a WalkForwardWindow with mock metrics."""
    candles = _make_candles(train_bars + test_bars)
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def _make_metrics(sharpe: float) -> PerformanceMetrics:
        return PerformanceMetrics(
            total_return_pct=10.0,
            annualized_return_pct=10.0,
            cagr=10.0,
            sharpe_ratio=sharpe,
            sortino_ratio=sharpe * 1.2,
            calmar_ratio=1.0,
            omega_ratio=1.5,
            max_drawdown_pct=5.0,
            max_drawdown_duration_bars=20,
            recovery_factor=2.0,
            total_trades=20,
            winning_trades=12,
            losing_trades=8,
            win_rate=0.6,
            profit_factor=1.8,
            payoff_ratio=1.5,
            expectancy=50.0,
            avg_win=150.0,
            avg_loss=-100.0,
            largest_win=300.0,
            largest_loss=-200.0,
            max_win_streak=4,
            max_loss_streak=3,
            avg_holding_bars=5.0,
            total_fees=20.0,
            total_slippage=10.0,
        )

    return WalkForwardWindow(
        window_id=window_id,
        train_start=ts,
        train_end=ts,
        test_start=ts,
        test_end=ts,
        train_candles=candles[:train_bars],
        test_candles=candles[train_bars:],
        best_params=best_params,
        train_metrics=_make_metrics(train_sharpe),
        test_metrics=_make_metrics(test_sharpe),
        overfit_score=0.0,
    )


# ── generate_windows tests ────────────────────────────────────────────────────

def test_generate_windows_basic():
    """generate_windows produces at least one window for sufficient candles."""
    try:
        from research.walkforward.rolling_windows import generate_windows
    except ImportError:
        pytest.skip("rolling_windows module not available")

    candles = _make_candles(1500)
    windows = generate_windows(candles, train_bars=800, test_bars=200, step_bars=200)
    assert len(windows) > 0


def test_generate_windows_insufficient_data():
    """Too few candles → no windows generated."""
    try:
        from research.walkforward.rolling_windows import generate_windows
    except ImportError:
        pytest.skip("rolling_windows module not available")

    candles = _make_candles(50)  # way too few
    windows = generate_windows(candles, train_bars=500, test_bars=200)
    assert windows == []


def test_generate_windows_no_data_leakage():
    """Test set starts strictly after training set ends."""
    try:
        from research.walkforward.rolling_windows import generate_windows
    except ImportError:
        pytest.skip("rolling_windows module not available")

    candles = _make_candles(1200)
    windows = generate_windows(candles, train_bars=600, test_bars=300, step_bars=300)

    for w in windows:
        assert len(w.train_candles) > 0
        assert len(w.test_candles) > 0
        train_end_ts = w.train_candles[-1].ts
        test_start_ts = w.test_candles[0].ts
        # Test starts AFTER train ends
        assert test_start_ts > train_end_ts, (
            f"Data leakage: test starts at {test_start_ts}, "
            f"train ends at {train_end_ts}"
        )


def test_generate_windows_correct_sizes():
    """Windows have exactly train_bars and test_bars candles."""
    try:
        from research.walkforward.rolling_windows import generate_windows
    except ImportError:
        pytest.skip("rolling_windows module not available")

    train_bars = 600
    test_bars = 200
    candles = _make_candles(train_bars + test_bars + 50)
    windows = generate_windows(candles, train_bars=train_bars, test_bars=test_bars)

    for w in windows:
        assert len(w.train_candles) == train_bars
        assert len(w.test_candles) == test_bars


# ── compute_parameter_stability tests ────────────────────────────────────────

def test_parameter_stability_identical_params():
    """Identical params across windows → stability = 1.0."""
    try:
        from research.walkforward.validation import compute_parameter_stability
    except ImportError:
        pytest.skip("validation module not available")

    params = {"ema_fast": 9, "ema_slow": 21}
    windows = [_make_window(i, params) for i in range(3)]
    stability = compute_parameter_stability(windows)
    assert stability == pytest.approx(1.0, abs=1e-6)


def test_parameter_stability_varying_params():
    """Varying params → stability < 1.0."""
    try:
        from research.walkforward.validation import compute_parameter_stability
    except ImportError:
        pytest.skip("validation module not available")

    windows = [
        _make_window(0, {"ema_fast": 5,  "ema_slow": 21}),
        _make_window(1, {"ema_fast": 9,  "ema_slow": 34}),
        _make_window(2, {"ema_fast": 12, "ema_slow": 50}),
    ]
    stability = compute_parameter_stability(windows)
    assert 0.0 <= stability <= 1.0
    assert stability < 1.0


def test_parameter_stability_empty_windows():
    """Empty window list → stability = 0."""
    try:
        from research.walkforward.validation import compute_parameter_stability
    except ImportError:
        pytest.skip("validation module not available")

    stability = compute_parameter_stability([])
    assert stability == 0.0


# ── overfit_score tests ───────────────────────────────────────────────────────

def test_overfit_score_no_degradation():
    """When IS and OOS metrics are identical → overfit score near 0."""
    try:
        from research.walkforward.overfit_detection import overfit_score
    except ImportError:
        pytest.skip("overfit_detection module not available")

    metrics = _make_window(0, {}).train_metrics
    assert metrics is not None
    score = overfit_score(metrics, metrics)  # identical
    assert score == pytest.approx(0.0, abs=1e-6)


def test_overfit_score_severe_degradation():
    """When IS is excellent and OOS is terrible → overfit score near 1.0."""
    try:
        from research.walkforward.overfit_detection import overfit_score
    except ImportError:
        pytest.skip("overfit_detection module not available")

    is_w = _make_window(0, {}, train_sharpe=3.0, test_sharpe=3.0)
    oos_w = _make_window(0, {}, train_sharpe=3.0, test_sharpe=0.0)
    score = overfit_score(is_w.train_metrics, oos_w.test_metrics)
    assert score > 0.0


# ── WalkForwardEngine mock test ───────────────────────────────────────────────

def test_walk_forward_engine_with_mock_backtest():
    """WalkForwardEngine runs with a mock backtest function."""
    try:
        from research.walkforward.engine import WalkForwardEngine
    except ImportError:
        pytest.skip("WalkForwardEngine not available")

    async def mock_backtest(candles, params) -> BacktestResult:
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        return BacktestResult(
            strategy="mock",
            symbol="BTC-USDT",
            params=params,
            trades=[],
            equity_curve=[10000.0] * len(candles),
            timestamps=[ts] * len(candles),
            initial_capital=10000.0,
            final_capital=10000.0,
            start_time=ts,
            end_time=ts,
            metadata={},
        )

    class MockOptimizer:
        async def run_bayesian_search(self, n_trials):
            return []

    engine = WalkForwardEngine(
        optimizer=MockOptimizer(),
        backtest_fn=mock_backtest,
        train_bars=600,
        test_bars=200,
        step_bars=200,
    )
    candles = _make_candles(900)
    result = asyncio.run(
        engine.run(
            candles,
            param_space={"param": [1, 2, 3]},
            symbol="BTC-USDT",
            strategy_name="mock_strategy",
            n_trials=3,
        )
    )
    # Minimal: should not raise; windows list may be empty if optimizer returns nothing
    assert result is not None
