"""Tests for analytics modules: performance metrics, drawdown, risk-adjusted ratios."""
from __future__ import annotations

import math
import sys
import os
from datetime import datetime, timezone
from typing import List

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from research.types import BacktestTrade, BacktestResult, Candle


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_trade(
    net_pnl: float,
    fees: float = 1.0,
    gross_pnl: float = None,
    entry_price: float = 100.0,
    size: float = 1.0,
    holding_bars: int = 5,
    mae: float = 2.0,
    mfe: float = 4.0,
    exit_reason: str = "tp",
) -> BacktestTrade:
    gp = gross_pnl if gross_pnl is not None else net_pnl + fees
    notional = entry_price * size
    return BacktestTrade(
        trade_id="t1",
        symbol="BTC-USDT",
        strategy="test",
        side="long",
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        entry_price=entry_price,
        exit_price=entry_price + net_pnl / size,
        size=size,
        gross_pnl=gp,
        fees=fees,
        net_pnl=net_pnl,
        net_pnl_pct=(net_pnl / notional * 100.0) if notional > 0 else 0.0,
        entry_slippage=0.0,
        exit_slippage=0.0,
        max_adverse_excursion=mae,
        max_favorable_excursion=mfe,
        holding_bars=holding_bars,
        exit_reason=exit_reason,
        funding_paid=0.0,
    )


def _make_backtest_result(
    trades: List[BacktestTrade],
    initial_capital: float = 10_000.0,
) -> BacktestResult:
    equity = [initial_capital]
    cap = initial_capital
    for t in trades:
        cap += t.net_pnl
        equity.append(cap)
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return BacktestResult(
        strategy="test",
        symbol="BTC-USDT",
        params={},
        trades=trades,
        equity_curve=equity,
        timestamps=[ts] * len(equity),
        initial_capital=initial_capital,
        final_capital=cap,
        start_time=ts,
        end_time=ts,
    )


# ── Drawdown tests ────────────────────────────────────────────────────────────

def test_max_drawdown_known_sequence():
    """Max drawdown of [100, 110, 90, 80, 100] should be about -27%."""
    try:
        from research.analytics.drawdown import compute_max_drawdown
    except ImportError:
        pytest.skip("drawdown module not available")

    equity = [100.0, 110.0, 90.0, 80.0, 100.0]
    dd, start, end = compute_max_drawdown(equity)
    # Peak = 110, trough = 80 → drawdown = (80-110)/110 = -27.27%
    assert dd == pytest.approx(-27.27, rel=0.01)
    assert start == 1   # peak index
    assert end == 3     # trough index


def test_max_drawdown_all_rising():
    """No drawdown in a monotonically rising curve."""
    try:
        from research.analytics.drawdown import compute_max_drawdown
    except ImportError:
        pytest.skip("drawdown module not available")

    equity = [100.0, 101.0, 102.0, 103.0]
    dd, start, end = compute_max_drawdown(equity)
    assert dd == pytest.approx(0.0, abs=1e-9)


def test_max_drawdown_single_element():
    """Single-element equity curve returns (0, 0, 0)."""
    try:
        from research.analytics.drawdown import compute_max_drawdown
    except ImportError:
        pytest.skip("drawdown module not available")

    dd, s, e = compute_max_drawdown([100.0])
    assert dd == 0.0
    assert s == 0
    assert e == 0


def test_max_drawdown_empty():
    """Empty equity curve returns (0, 0, 0)."""
    try:
        from research.analytics.drawdown import compute_max_drawdown
    except ImportError:
        pytest.skip("drawdown module not available")

    dd, s, e = compute_max_drawdown([])
    assert dd == 0.0


# ── Sharpe ratio tests ────────────────────────────────────────────────────────

def test_sharpe_ratio_positive_returns():
    """Positive, consistent returns should give a positive Sharpe."""
    try:
        from research.analytics.risk_adjusted import sharpe_ratio
    except ImportError:
        pytest.skip("risk_adjusted module not available")

    # Constant 1% return per bar → high Sharpe
    equity = [100.0 * (1.01 ** i) for i in range(200)]
    sr = sharpe_ratio(equity)
    assert sr > 0.0


def test_sharpe_zero_for_flat_equity():
    """Flat equity → zero excess return → Sharpe = 0."""
    try:
        from research.analytics.risk_adjusted import sharpe_ratio
    except ImportError:
        pytest.skip("risk_adjusted module not available")

    equity = [100.0] * 100
    sr = sharpe_ratio(equity)
    assert sr == pytest.approx(0.0, abs=1e-6)


# ── Sortino ratio tests ───────────────────────────────────────────────────────

def test_sortino_uses_only_negative_returns():
    """Sortino with only upside moves should be high (no downside deviation)."""
    try:
        from research.analytics.risk_adjusted import sortino_ratio
    except ImportError:
        pytest.skip("risk_adjusted module not available")

    # Only upward moves
    equity = [100.0 + i * 2 for i in range(50)]
    sortino = sortino_ratio(equity)
    # With all positive returns and no downside, sortino should be very high or inf
    assert sortino > 0.0


# ── Omega ratio tests ─────────────────────────────────────────────────────────

def test_omega_ratio_all_winning():
    """All-winning returns → omega should be very high (no losses)."""
    try:
        from research.analytics.risk_adjusted import omega_ratio
    except ImportError:
        pytest.skip("risk_adjusted module not available")

    equity = [100.0 + i * 5 for i in range(20)]
    omega = omega_ratio(equity, threshold=0.0)
    assert omega > 1.0  # more gains than losses


def test_omega_ratio_all_losing():
    """All-losing returns → omega should be 0."""
    try:
        from research.analytics.risk_adjusted import omega_ratio
    except ImportError:
        pytest.skip("risk_adjusted module not available")

    returns = [-0.05] * 20  # 5% loss every period
    omega = omega_ratio(returns, threshold=0.0)
    assert omega == pytest.approx(0.0, abs=1e-6)


# ── Expectancy tests ──────────────────────────────────────────────────────────

def test_expectancy_avg_net_pnl():
    """Expectancy == average net_pnl per trade."""
    try:
        from research.analytics.expectancy import compute_expectancy
    except ImportError:
        pytest.skip("expectancy module not available")

    trades = [
        _make_trade(net_pnl=100.0),
        _make_trade(net_pnl=-50.0),
        _make_trade(net_pnl=200.0),
    ]
    exp = compute_expectancy(trades)
    expected = (100.0 - 50.0 + 200.0) / 3.0
    assert exp == pytest.approx(expected, rel=1e-6)


def test_expectancy_empty():
    """Empty trade list → expectancy = 0."""
    try:
        from research.analytics.expectancy import compute_expectancy
    except ImportError:
        pytest.skip("expectancy module not available")

    assert compute_expectancy([]) == pytest.approx(0.0)


def test_expectancy_single_winning_trade():
    """Single winning trade → expectancy == that trade's net_pnl."""
    try:
        from research.analytics.expectancy import compute_expectancy
    except ImportError:
        pytest.skip("expectancy module not available")

    trade = _make_trade(net_pnl=300.0)
    assert compute_expectancy([trade]) == pytest.approx(300.0, rel=1e-6)


# ── Profit factor tests ───────────────────────────────────────────────────────

def test_profit_factor_known_values():
    """Profit factor = gross_win / gross_loss."""
    try:
        from research.analytics.expectancy import compute_profit_factor
    except ImportError:
        pytest.skip("expectancy module not available")

    trades = [
        _make_trade(net_pnl=200.0),  # winner
        _make_trade(net_pnl=-100.0), # loser
    ]
    pf = compute_profit_factor(trades)
    assert pf == pytest.approx(2.0, rel=1e-6)


def test_profit_factor_all_wins():
    """All-winning trades → profit factor is infinite (no losses)."""
    try:
        from research.analytics.expectancy import compute_profit_factor
    except ImportError:
        pytest.skip("expectancy module not available")

    trades = [_make_trade(net_pnl=100.0), _make_trade(net_pnl=200.0)]
    pf = compute_profit_factor(trades)
    assert math.isinf(pf)


def test_profit_factor_empty():
    """Empty trade list → profit factor = 0."""
    try:
        from research.analytics.expectancy import compute_profit_factor
    except ImportError:
        pytest.skip("expectancy module not available")

    assert compute_profit_factor([]) == pytest.approx(0.0)


# ── compute_performance_metrics integration test ──────────────────────────────

def test_compute_performance_metrics_basic():
    """compute_performance_metrics runs on a known trade set."""
    try:
        from research.analytics.performance import compute_performance_metrics
    except ImportError:
        pytest.skip("performance module not available")

    trades = [
        _make_trade(net_pnl=100.0, fees=2.0),
        _make_trade(net_pnl=-30.0, fees=2.0),
        _make_trade(net_pnl=80.0,  fees=2.0),
    ]
    result = _make_backtest_result(trades)
    metrics = compute_performance_metrics(result)
    assert metrics.total_trades == 3
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 1
    assert metrics.win_rate == pytest.approx(2 / 3, rel=1e-6)
    assert metrics.profit_factor > 0


def test_compute_performance_metrics_empty_trades():
    """Works on an empty trade list without raising."""
    try:
        from research.analytics.performance import compute_performance_metrics
    except ImportError:
        pytest.skip("performance module not available")

    result = _make_backtest_result([])
    metrics = compute_performance_metrics(result)
    assert metrics.total_trades == 0
    assert metrics.win_rate == 0.0
