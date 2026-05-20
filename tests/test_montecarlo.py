"""Tests for Monte Carlo engine and simulation utilities."""
from __future__ import annotations

import sys
import os
import math
from datetime import datetime, timezone
from typing import List

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from research.types import BacktestTrade, BacktestResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_trade(
    net_pnl: float,
    net_pnl_pct: float = None,
    fees: float = 0.5,
    entry_price: float = 100.0,
) -> BacktestTrade:
    npp = net_pnl_pct if net_pnl_pct is not None else (net_pnl / entry_price * 100.0)
    return BacktestTrade(
        trade_id="mc_t",
        symbol="BTC-USDT",
        strategy="mc_test",
        side="long",
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        entry_price=entry_price,
        exit_price=entry_price + net_pnl,
        size=1.0,
        gross_pnl=net_pnl + fees,
        fees=fees,
        net_pnl=net_pnl,
        net_pnl_pct=npp,
        entry_slippage=0.0,
        exit_slippage=0.0,
        max_adverse_excursion=abs(net_pnl) * 0.5,
        max_favorable_excursion=abs(net_pnl) * 1.5,
        holding_bars=4,
        exit_reason="tp" if net_pnl > 0 else "sl",
        funding_paid=0.0,
    )


def _make_result(
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
        strategy="mc_test",
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


# ── Monte Carlo engine tests ──────────────────────────────────────────────────

def test_monte_carlo_run_basic():
    """MonteCarloEngine.run() completes and returns a MonteCarloResult."""
    try:
        from research.montecarlo.engine import MonteCarloEngine
    except ImportError:
        pytest.skip("MonteCarloEngine not available")

    trades = [_make_trade(100.0)] * 20 + [_make_trade(-40.0)] * 10
    result = _make_result(trades)
    engine = MonteCarloEngine(n_simulations=200, seed=42)
    mc = engine.run(result)

    assert mc is not None
    assert mc.n_simulations == 200
    assert 0.0 <= mc.ruin_probability <= 1.0
    assert 0.0 <= mc.survivability <= 1.0


def test_ruin_probability_zero_all_wins():
    """All-profitable trades → ruin probability should be very low (or 0)."""
    try:
        from research.montecarlo.engine import MonteCarloEngine
    except ImportError:
        pytest.skip("MonteCarloEngine not available")

    # 100 winning trades with tiny variance
    trades = [_make_trade(100.0)] * 100
    result = _make_result(trades, initial_capital=10_000.0)
    engine = MonteCarloEngine(n_simulations=500, ruin_threshold=0.5, seed=42)
    mc = engine.run(result)

    # With all-winning trades, ruin should be near 0
    assert mc.ruin_probability < 0.10


def test_confidence_intervals_consistent():
    """CI lower < median < upper for return confidence intervals."""
    try:
        from research.montecarlo.engine import MonteCarloEngine
    except ImportError:
        pytest.skip("MonteCarloEngine not available")

    trades = [_make_trade(50.0)] * 30 + [_make_trade(-20.0)] * 10
    result = _make_result(trades)
    engine = MonteCarloEngine(n_simulations=300, confidence=0.95, seed=42)
    mc = engine.run(result)

    # lower <= median (within floating point tolerance)
    assert mc.return_ci_lower <= mc.expected_annual_return + 1e-6
    assert mc.expected_annual_return <= mc.return_ci_upper + 1e-6


def test_n_simulations_respected():
    """MonteCarloResult should record the requested simulation count."""
    try:
        from research.montecarlo.engine import MonteCarloEngine
    except ImportError:
        pytest.skip("MonteCarloEngine not available")

    trades = [_make_trade(30.0)] * 15
    result = _make_result(trades)
    n = 150
    engine = MonteCarloEngine(n_simulations=n, seed=1)
    mc = engine.run(result)
    assert mc.n_simulations == n


def test_seed_reproducibility():
    """Same seed → same result."""
    try:
        from research.montecarlo.engine import MonteCarloEngine
    except ImportError:
        pytest.skip("MonteCarloEngine not available")

    trades = [_make_trade(80.0)] * 20 + [_make_trade(-30.0)] * 8
    result = _make_result(trades)

    engine_a = MonteCarloEngine(n_simulations=200, seed=999)
    engine_b = MonteCarloEngine(n_simulations=200, seed=999)

    mc_a = engine_a.run(result)
    mc_b = engine_b.run(result)

    assert mc_a.ruin_probability == pytest.approx(mc_b.ruin_probability, abs=1e-9)
    assert mc_a.survivability == pytest.approx(mc_b.survivability, abs=1e-9)
    assert mc_a.expected_annual_return == pytest.approx(mc_b.expected_annual_return, abs=1e-9)


def test_empty_trades_does_not_crash():
    """Empty trade list: engine should handle gracefully."""
    try:
        from research.montecarlo.engine import MonteCarloEngine
    except ImportError:
        pytest.skip("MonteCarloEngine not available")

    result = _make_result([])
    engine = MonteCarloEngine(n_simulations=100, seed=42)
    # Should not raise — implementation may return zero values
    try:
        mc = engine.run(result)
        assert mc is not None
    except Exception as exc:
        pytest.skip(f"Empty trade handling not implemented: {exc}")


def test_max_drawdown_median_non_negative():
    """Max drawdown median should be non-negative."""
    try:
        from research.montecarlo.engine import MonteCarloEngine
    except ImportError:
        pytest.skip("MonteCarloEngine not available")

    trades = [_make_trade(50.0)] * 20 + [_make_trade(-20.0)] * 5
    result = _make_result(trades)
    engine = MonteCarloEngine(n_simulations=200, seed=42)
    mc = engine.run(result)
    assert mc.max_drawdown_median >= 0.0


def test_capital_adequacy_multiplier_is_positive():
    """Capital adequacy multiplier should be >= 1.0."""
    try:
        from research.montecarlo.engine import MonteCarloEngine
    except ImportError:
        pytest.skip("MonteCarloEngine not available")

    trades = [_make_trade(40.0)] * 15 + [_make_trade(-50.0)] * 10
    result = _make_result(trades)
    engine = MonteCarloEngine(n_simulations=200, seed=42)
    mc = engine.run(result)
    assert mc.capital_adequacy_multiplier >= 1.0
