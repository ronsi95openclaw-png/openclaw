"""Chaos Phase 6: microstructure simulator tests.

Six tests covering fill quality, slippage, deterministic replay, latency,
and batch report bounds.  All tests complete in < 30 s total.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import List

import pytest

# ── Import guard ──────────────────────────────────────────────────────────────

try:
    from runtime.microstructure_simulator import (
        MicrostructureSimulator,
        MarketMode,
        FillResult,
        ExecutionQualityReport,
    )
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


pytestmark = pytest.mark.skipif(
    not _AVAILABLE,
    reason="runtime.microstructure_simulator not available",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_sim(tmp_path: Path, seed: int = 42) -> "MicrostructureSimulator":
    """Create an isolated MicrostructureSimulator backed by tmp_path."""
    return MicrostructureSimulator(
        seed=seed,
        analytics_path=str(tmp_path / "ms_analytics.jsonl"),
    )


# ── Test 1: Normal mode fill quality ─────────────────────────────────────────

class TestNormalModeFillQuality:
    def test_normal_mode_fill_rate_gte_090(self, tmp_path: Path) -> None:
        """NORMAL mode: average fill rate across 500 orders must be ≥ 0.90."""
        sim   = _make_sim(tmp_path)
        qty   = 0.01
        price = 67_000.0
        n     = 500

        fills: List[FillResult] = [
            sim.simulate_fill("BTCUSD-PERP", "BUY", qty, price, MarketMode.NORMAL)
            for _ in range(n)
        ]
        fill_rates = [f.filled_qty / qty for f in fills]
        avg_fill_rate = sum(fill_rates) / n

        assert avg_fill_rate >= 0.90, (
            f"NORMAL mode fill rate {avg_fill_rate:.4f} < 0.90"
        )


# ── Test 2: Panic mode fill degradation ──────────────────────────────────────

class TestPanicModeFillDegradation:
    def test_panic_fill_rate_below_normal(self, tmp_path: Path) -> None:
        """PANIC mode fill rate must be strictly less than NORMAL fill rate."""
        qty   = 0.01
        price = 67_000.0
        n     = 500

        sim_normal = _make_sim(tmp_path, seed=99)
        normal_fills = [
            sim_normal.simulate_fill("BTCUSD-PERP", "BUY", qty, price, MarketMode.NORMAL)
            for _ in range(n)
        ]
        normal_avg = sum(f.filled_qty / qty for f in normal_fills) / n

        sim_panic = _make_sim(tmp_path / "panic", seed=99)
        panic_fills = [
            sim_panic.simulate_fill("BTCUSD-PERP", "BUY", qty, price, MarketMode.PANIC)
            for _ in range(n)
        ]
        panic_avg = sum(f.filled_qty / qty for f in panic_fills) / n

        assert panic_avg < normal_avg, (
            f"Expected PANIC fill rate {panic_avg:.4f} < NORMAL {normal_avg:.4f}"
        )


# ── Test 3: Deterministic replay ─────────────────────────────────────────────

class TestDeterministicReplay:
    def test_same_seed_identical_fill_result(self, tmp_path: Path) -> None:
        """Two simulators with the same seed must produce identical FillResult."""
        qty   = 0.05
        price = 3_500.0

        sim_a = _make_sim(tmp_path / "a", seed=7)
        sim_b = _make_sim(tmp_path / "b", seed=7)

        result_a = sim_a.simulate_fill(
            "ETHUSD-PERP", "SELL", qty, price, MarketMode.VOLATILE
        )
        result_b = sim_b.simulate_fill(
            "ETHUSD-PERP", "SELL", qty, price, MarketMode.VOLATILE
        )

        assert result_a.filled_qty     == pytest.approx(result_b.filled_qty,     rel=1e-9)
        assert result_a.avg_fill_price == pytest.approx(result_b.avg_fill_price, rel=1e-9)
        assert result_a.slippage_bps   == pytest.approx(result_b.slippage_bps,   rel=1e-9)
        assert result_a.latency_ms     == pytest.approx(result_b.latency_ms,     rel=1e-9)
        assert result_a.queue_position == result_b.queue_position
        assert result_a.partial_fill   == result_b.partial_fill
        assert result_a.cancel_raced   == result_b.cancel_raced
        assert result_a.ack_delayed    == result_b.ack_delayed


# ── Test 4: Liquidity crisis slippage ────────────────────────────────────────

class TestLiquidityCrisisSlippage:
    def test_liquidity_crisis_slippage_exceeds_normal(self, tmp_path: Path) -> None:
        """LIQUIDITY_CRISIS average slippage_bps must exceed NORMAL average slippage."""
        qty   = 0.01
        price = 67_000.0
        n     = 400

        sim_n = _make_sim(tmp_path / "n", seed=11)
        normal_slippages = [
            sim_n.simulate_fill("BTCUSD-PERP", "BUY", qty, price, MarketMode.NORMAL).slippage_bps
            for _ in range(n)
        ]

        sim_lc = _make_sim(tmp_path / "lc", seed=11)
        crisis_slippages = [
            sim_lc.simulate_fill("BTCUSD-PERP", "BUY", qty, price, MarketMode.LIQUIDITY_CRISIS).slippage_bps
            for _ in range(n)
        ]

        avg_normal  = sum(normal_slippages)  / n
        avg_crisis  = sum(crisis_slippages)  / n

        assert avg_crisis > avg_normal, (
            f"Expected LIQUIDITY_CRISIS slippage {avg_crisis:.4f} bps > "
            f"NORMAL {avg_normal:.4f} bps"
        )


# ── Test 5: Batch report bounds ───────────────────────────────────────────────

class TestBatchReportBounds:
    def test_execution_realism_score_in_0_100(self, tmp_path: Path) -> None:
        """execution_realism_score must be in [0, 100] for every market mode."""
        qty   = 0.01
        price = 100.0

        for mode in MarketMode:
            sim = _make_sim(tmp_path / mode.value, seed=42)
            report: ExecutionQualityReport = sim.run_batch(
                "SOLUSD-PERP", "BUY", qty, price, mode, n=200
            )
            assert 0.0 <= report.execution_realism_score <= 100.0, (
                f"Mode {mode.value}: realism_score={report.execution_realism_score} out of [0, 100]"
            )
            assert 0.0 <= report.fill_degradation_score <= 1.0, (
                f"Mode {mode.value}: fill_degradation_score out of [0, 1]"
            )
            assert 0.0 <= report.queue_priority_score <= 1.0, (
                f"Mode {mode.value}: queue_priority_score out of [0, 1]"
            )


# ── Test 6: Exchange degraded latency ─────────────────────────────────────────

class TestExchangeDegradedLatency:
    def test_exchange_degraded_p99_latency_exceeds_normal(self, tmp_path: Path) -> None:
        """EXCHANGE_DEGRADED p99 latency must exceed NORMAL p99 latency."""
        qty   = 0.01
        price = 67_000.0

        sim_n  = _make_sim(tmp_path / "n",  seed=55)
        sim_ed = _make_sim(tmp_path / "ed", seed=55)

        report_normal = sim_n.run_batch(
            "BTCUSD-PERP", "BUY", qty, price, MarketMode.NORMAL, n=500
        )
        report_degraded = sim_ed.run_batch(
            "BTCUSD-PERP", "BUY", qty, price, MarketMode.EXCHANGE_DEGRADED, n=500
        )

        assert report_degraded.p99_latency_ms > report_normal.p99_latency_ms, (
            f"Expected EXCHANGE_DEGRADED p99 {report_degraded.p99_latency_ms:.1f} ms "
            f"> NORMAL p99 {report_normal.p99_latency_ms:.1f} ms"
        )
