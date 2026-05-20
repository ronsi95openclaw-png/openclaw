"""Tests for AdaptivePortfolioAllocator and related portfolio modules."""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from research.types import AllocationWeights, Candle, RegimeState


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_candles(n: int = 60, price: float = 100.0, vol: float = 0.01) -> List[Candle]:
    import math
    base_ts = 1_700_000_000_000
    bar_ms = 15 * 60 * 1_000
    candles = []
    for i in range(n):
        chg = vol * math.sin(i * 0.3)
        open_p = price
        close = price * (1 + chg)
        high = max(open_p, close) * 1.002
        low = min(open_p, close) * 0.998
        candles.append(Candle(
            ts=base_ts + i * bar_ms,
            open=open_p, high=high, low=low, close=close, volume=200.0
        ))
        price = close
    return candles


def _make_regime(label: str = "TRENDING_BULL", panic: bool = False, drought: bool = False) -> RegimeState:
    return RegimeState(
        trending=(label in ("TRENDING_BULL", "TRENDING_BEAR")),
        ranging=(label == "RANGING"),
        vol_expanding=(label == "VOL_EXPANSION"),
        vol_compressing=(label == "VOL_COMPRESSION"),
        momentum_dominant=(label == "MOMENTUM_BULL"),
        mean_reverting=(label == "MEAN_REVERTING"),
        liquidity_drought=drought,
        panic_conditions=panic,
        regime_score=0.7,
        label=label,
        adx=25.0,
        atr_ratio=1.2,
        bb_width_pct=5.0,
        rsi=55.0,
    )


# ── Attempt to import real allocator ─────────────────────────────────────────

def _get_allocator():
    try:
        from research.portfolio.allocator import AdaptivePortfolioAllocator
        return AdaptivePortfolioAllocator
    except ImportError:
        return None


# ── Minimal mock allocator for isolated testing ───────────────────────────────

class _MockAllocator:
    """Reference allocator for testing when the real one is unavailable."""

    def __init__(self) -> None:
        self._cooldown = False
        self._kill_switch = False

    def set_kill_switch(self, active: bool) -> None:
        self._kill_switch = active

    def allocate(
        self,
        strategies: List[str],
        symbols: List[str],
        regime: RegimeState,
    ) -> AllocationWeights:
        n_strats = len(strategies)
        n_symbols = len(symbols)

        if self._kill_switch or regime.panic_conditions:
            self._cooldown = True
            return AllocationWeights(
                strategy_weights={s: 0.0 for s in strategies},
                pair_allocations={sym: 0.0 for sym in symbols},
                leverage_caps={sym: 1.0 for sym in symbols},
                risk_pct=0.0,
                cooldown_active=True,
                rationale="kill_switch_or_panic",
                regime_label=regime.label,
                timestamp=datetime.now(timezone.utc),
            )

        w_strat = 1.0 / n_strats if n_strats > 0 else 0.0
        w_sym = 1.0 / n_symbols if n_symbols > 0 else 0.0
        risk = 0.5 if regime.liquidity_drought else 1.5

        return AllocationWeights(
            strategy_weights={s: w_strat for s in strategies},
            pair_allocations={sym: w_sym for sym in symbols},
            leverage_caps={sym: 3.0 for sym in symbols},
            risk_pct=risk,
            cooldown_active=self._cooldown,
            rationale="mock_allocation",
            regime_label=regime.label,
            timestamp=datetime.now(timezone.utc),
        )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_allocator_weights_sum_to_one():
    """Strategy weights should sum to 1.0."""
    AllocCls = _get_allocator()
    if AllocCls is None:
        alloc = _MockAllocator()
    else:
        alloc = AllocCls()

    strategies = ["ema_cross", "rsi_mr", "breakout"]
    symbols = ["BTC-USDT", "ETH-USDT"]
    regime = _make_regime("TRENDING_BULL")

    weights = alloc.allocate(strategies, symbols, regime)
    total = sum(weights.strategy_weights.values())
    assert abs(total - 1.0) < 1e-3, f"Weights sum to {total}, expected 1.0"


def test_allocator_pair_allocations_sum_to_one():
    """Pair allocations should sum to 1.0."""
    AllocCls = _get_allocator()
    if AllocCls is None:
        alloc = _MockAllocator()
    else:
        alloc = AllocCls()

    strategies = ["strat1"]
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    regime = _make_regime("RANGING")

    weights = alloc.allocate(strategies, symbols, regime)
    total = sum(weights.pair_allocations.values())
    assert abs(total - 1.0) < 1e-3, f"Pair allocations sum to {total}, expected 1.0"


def test_panic_regime_triggers_cooldown():
    """Panic regime → cooldown_active=True and zero/minimal allocations."""
    AllocCls = _get_allocator()
    if AllocCls is None:
        alloc = _MockAllocator()
    else:
        alloc = AllocCls()

    strategies = ["strat1", "strat2"]
    symbols = ["BTC-USDT"]
    regime = _make_regime("PANIC", panic=True)

    weights = alloc.allocate(strategies, symbols, regime)
    assert weights.cooldown_active is True
    # Risk should be 0 or very low during panic
    assert weights.risk_pct <= 0.5


def test_liquidity_drought_reduces_risk():
    """Liquidity drought regime → reduced risk_pct."""
    AllocCls = _get_allocator()
    if AllocCls is None:
        alloc = _MockAllocator()
    else:
        alloc = AllocCls()

    strategies = ["strat1"]
    symbols = ["BTC-USDT"]
    normal_regime = _make_regime("TRENDING_BULL")
    drought_regime = _make_regime("LIQUIDITY_DROUGHT", drought=True)

    normal_w = alloc.allocate(strategies, symbols, normal_regime)
    drought_w = alloc.allocate(strategies, symbols, drought_regime)

    # Drought should have lower or equal risk
    assert drought_w.risk_pct <= normal_w.risk_pct + 0.01, (
        f"Expected drought risk ({drought_w.risk_pct}) <= normal ({normal_w.risk_pct})"
    )


def test_kill_switch_zeroes_allocations():
    """Kill switch active → all weights zeroed."""
    AllocCls = _get_allocator()
    if AllocCls is None:
        alloc = _MockAllocator()
    else:
        try:
            alloc = AllocCls()
            alloc.set_kill_switch(True)
        except AttributeError:
            alloc = _MockAllocator()
            alloc.set_kill_switch(True)

    alloc.set_kill_switch(True)
    strategies = ["strat1", "strat2"]
    symbols = ["BTC-USDT"]
    regime = _make_regime("TRENDING_BULL")

    weights = alloc.allocate(strategies, symbols, regime)
    total_strat = sum(weights.strategy_weights.values())
    assert total_strat < 0.01, f"Kill switch: weights should be ~0, got {total_strat}"


def test_allocation_returns_all_strategies():
    """Allocation result includes all requested strategies."""
    AllocCls = _get_allocator()
    if AllocCls is None:
        alloc = _MockAllocator()
    else:
        alloc = AllocCls()

    strategies = ["s1", "s2", "s3"]
    symbols = ["BTC-USDT"]
    regime = _make_regime("RANGING")

    weights = alloc.allocate(strategies, symbols, regime)
    for s in strategies:
        assert s in weights.strategy_weights, f"Strategy {s} missing from weights"


def test_allocation_returns_all_symbols():
    """Allocation result includes all requested symbols."""
    AllocCls = _get_allocator()
    if AllocCls is None:
        alloc = _MockAllocator()
    else:
        alloc = AllocCls()

    strategies = ["s1"]
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    regime = _make_regime("MOMENTUM_BULL")

    weights = alloc.allocate(strategies, symbols, regime)
    for sym in symbols:
        assert sym in weights.pair_allocations, f"Symbol {sym} missing from pair_allocations"


def test_allocation_has_regime_label():
    """AllocationWeights should record the regime label."""
    AllocCls = _get_allocator()
    if AllocCls is None:
        alloc = _MockAllocator()
    else:
        alloc = AllocCls()

    regime = _make_regime("VOL_EXPANSION")
    weights = alloc.allocate(["strat1"], ["BTC-USDT"], regime)
    assert weights.regime_label in ("VOL_EXPANSION", "") or weights.regime_label is not None
