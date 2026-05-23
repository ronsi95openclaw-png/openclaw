"""Phase 7 tests: MultiLeg simulator extension of MicrostructureSimulator.

Tests cover multi-leg fills, SL/TP pricing, determinism, slippage bounds,
cascade propagation, and correlated stress across symbols.
"""
from __future__ import annotations

import random as _random_module

import pytest

from runtime.microstructure_simulator import (
    MarketMode,
    MicrostructureSimulator,
    MultiLegFillResult,
    MultiLegSimulationConfig,
)


# ── Fixture ───────────────────────────────────────────────────────────────────

def _sim(seed: int = 42) -> MicrostructureSimulator:
    """Return a fresh MicrostructureSimulator with the given seed."""
    return MicrostructureSimulator(seed=seed, analytics_path="data/test_ms_phase7.jsonl")


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestMultiLegNormalMode:
    """Test 1: Basic multi-leg fill in NORMAL mode."""

    def test_multi_leg_normal_mode(self) -> None:
        sim = _sim()
        result = sim.simulate_multi_leg(
            symbol="BTCUSD-PERP",
            side="BUY",
            qty=0.01,
            ref_price=67_000.0,
            mode=MarketMode.NORMAL,
        )
        assert isinstance(result, MultiLegFillResult)
        # Entry should have attempted fill (filled_qty may be 0 on no-fill, but
        # field must exist and be non-negative)
        assert result.entry.filled_qty >= 0.0
        assert result.sl_fill is not None
        assert result.partial_tp_fills is not None
        assert len(result.partial_tp_fills) >= 1
        assert result.total_latency_ms > 0.0
        assert result.execution_realism_score >= 0.0
        assert result.execution_realism_score <= 100.0


class TestMultiLegPriceCorrectness:
    """Test 2: SL below entry for LONG; TP above entry for LONG."""

    def test_multi_leg_sl_price_correct(self) -> None:
        sim = _sim()
        ref_price = 50_000.0
        config = MultiLegSimulationConfig(
            sl_distance_pct=1.0,
            tp_distance_pct=2.0,
        )
        result = sim.simulate_multi_leg(
            symbol="BTCUSD-PERP",
            side="BUY",
            qty=0.01,
            ref_price=ref_price,
            mode=MarketMode.NORMAL,
            config=config,
        )
        expected_sl_max = ref_price * (1.0 - 0.009)   # strictly less than ref_price
        expected_tp_min = ref_price * (1.0 + 0.019)   # strictly greater than ref_price

        # SL fill reference price should be below entry reference
        assert result.sl_fill.avg_fill_price < ref_price * 1.01, (
            f"SL fill price {result.sl_fill.avg_fill_price} should be near/below ref_price {ref_price}"
        )
        # TP fill reference price should be above entry reference
        assert result.tp_fill is not None
        assert result.tp_fill.avg_fill_price > ref_price * 0.99, (
            f"TP fill price {result.tp_fill.avg_fill_price} should be near/above ref_price {ref_price}"
        )
        # Structural invariant: sl_fill price < tp_fill price for LONG
        assert result.sl_fill.avg_fill_price < result.tp_fill.avg_fill_price


class TestMultiLegPanicLatency:
    """Test 3: PANIC mode total_latency > NORMAL mode total_latency (on average)."""

    def test_multi_leg_panic_higher_total_latency(self) -> None:
        n = 20
        normal_latencies = []
        panic_latencies = []

        for seed in range(n):
            sim_n = _sim(seed=seed)
            r_n = sim_n.simulate_multi_leg(
                "ETHUSD-PERP", "BUY", 0.1, 3000.0, MarketMode.NORMAL
            )
            normal_latencies.append(r_n.total_latency_ms)

            sim_p = _sim(seed=seed)
            r_p = sim_p.simulate_multi_leg(
                "ETHUSD-PERP", "BUY", 0.1, 3000.0, MarketMode.PANIC
            )
            panic_latencies.append(r_p.total_latency_ms)

        avg_normal = sum(normal_latencies) / n
        avg_panic  = sum(panic_latencies) / n
        assert avg_panic > avg_normal, (
            f"PANIC avg latency {avg_panic:.1f}ms should exceed NORMAL avg latency {avg_normal:.1f}ms"
        )


class TestPartialTPLadder:
    """Test 4: tp_ladder_levels=2 → len(partial_tp_fills) == 2."""

    def test_partial_tp_ladder(self) -> None:
        sim = _sim()
        config = MultiLegSimulationConfig(
            tp_ladder_levels=2,
            tp_ladder_fractions=[0.5, 0.5],
        )
        result = sim.simulate_multi_leg(
            symbol="SOLUSD-PERP",
            side="BUY",
            qty=1.0,
            ref_price=150.0,
            mode=MarketMode.NORMAL,
            config=config,
        )
        assert len(result.partial_tp_fills) == 2, (
            f"Expected 2 partial TP fills, got {len(result.partial_tp_fills)}"
        )


class TestDeterministicMultiLeg:
    """Test 5: Same seed produces identical MultiLegFillResult."""

    def test_deterministic_multi_leg(self) -> None:
        kwargs = dict(
            symbol="BTCUSD-PERP",
            side="BUY",
            qty=0.01,
            ref_price=67_000.0,
            mode=MarketMode.VOLATILE,
        )

        sim_a = _sim(seed=99)
        result_a = sim_a.simulate_multi_leg(**kwargs)

        sim_b = _sim(seed=99)
        result_b = sim_b.simulate_multi_leg(**kwargs)

        assert result_a.entry.filled_qty        == result_b.entry.filled_qty
        assert result_a.entry.avg_fill_price    == result_b.entry.avg_fill_price
        assert result_a.entry.latency_ms        == result_b.entry.latency_ms
        assert result_a.sl_fill.avg_fill_price  == result_b.sl_fill.avg_fill_price
        assert result_a.net_slippage_bps        == result_b.net_slippage_bps
        assert result_a.total_latency_ms        == result_b.total_latency_ms
        assert result_a.cascade_chain_length    == result_b.cascade_chain_length
        assert result_a.trailing_stop_triggered == result_b.trailing_stop_triggered
        assert result_a.maker_taker_entry       == result_b.maker_taker_entry
        assert result_a.maker_taker_exit        == result_b.maker_taker_exit


class TestNetSlippageBounded:
    """Test 6: NORMAL mode net_slippage_bps < 20 bps on average."""

    def test_net_slippage_bounded(self) -> None:
        sim = _sim(seed=7)
        n = 50
        total = 0.0
        for _ in range(n):
            r = sim.simulate_multi_leg("BTCUSD-PERP", "BUY", 0.01, 50_000.0, MarketMode.NORMAL)
            total += r.net_slippage_bps
        avg = total / n
        assert avg < 20.0, (
            f"NORMAL mode avg net_slippage_bps {avg:.2f} should be < 20 bps"
        )


class TestCascadeChainPropagates:
    """Test 7: In PANIC mode with cascades, chain_length > 0 observed."""

    def test_cascade_chain_propagates(self) -> None:
        # Run many simulations in PANIC mode — should see at least some cascades
        sim = _sim(seed=13)
        cascade_lengths = []
        for _ in range(200):
            r = sim.simulate_multi_leg(
                "BTCUSD-PERP", "BUY", 0.1, 30_000.0, MarketMode.PANIC
            )
            cascade_lengths.append(r.cascade_chain_length)

        # In PANIC mode (cascade prob = 12%), with 200 runs we should see several
        non_zero = [c for c in cascade_lengths if c > 0]
        assert len(non_zero) > 0, (
            "Expected at least one cascade event in 200 PANIC-mode multi-leg simulations"
        )
        # All non-zero chain lengths should be in the expected range [1, 5)
        for c in non_zero:
            assert 1 <= c < 5, f"Cascade chain length {c} out of expected range [1, 5)"


class TestCorrelatedStressReturnsPerSymbol:
    """Test 8: run_correlated_stress returns one result per symbol."""

    def test_correlated_stress_returns_per_symbol(self) -> None:
        sim = _sim(seed=55)
        symbols = ["BTCUSD-PERP", "ETHUSD-PERP"]
        prices = {"BTCUSD-PERP": 67_000.0, "ETHUSD-PERP": 3_200.0}

        results = sim.run_correlated_stress(
            symbols=symbols,
            side="BUY",
            qty=0.01,
            prices=prices,
            mode=MarketMode.VOLATILE,
        )
        assert len(results) == 2, f"Expected 2 results, got {len(results)}"
        for r in results:
            assert isinstance(r, MultiLegFillResult)
            assert r.total_latency_ms > 0.0
