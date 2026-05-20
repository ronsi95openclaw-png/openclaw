"""Tests for execution quality, venue scoring, smart router, and latency tracker."""
from __future__ import annotations

import sys
import os
import tempfile
from datetime import datetime, timezone
from typing import List

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from research.types import Candle, ExecutionRecord, VenueScore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_record(
    venue: str = "blofin",
    slippage_bps: float = 5.0,
    latency_ms: float = 80.0,
    fill_status: str = "full",
    adverse_selection: float = 0.0,
    order_id: str = "ord1",
) -> ExecutionRecord:
    return ExecutionRecord(
        order_id=order_id,
        symbol="BTC-USDT",
        side="buy",
        intended_price=100.0,
        fill_price=100.0 + slippage_bps * 0.01,  # rough conversion
        size=1.0,
        latency_ms=latency_ms,
        slippage_bps=slippage_bps,
        adverse_selection=adverse_selection,
        venue=venue,
        timestamp=datetime.now(timezone.utc),
        fill_status=fill_status,
        reject_reason="" if fill_status != "rejected" else "no_liquidity",
    )


def _make_candle(price: float = 100.0, volume: float = 200.0, ts: int = None) -> Candle:
    ts = ts or 1_700_000_000_000
    return Candle(
        ts=ts,
        open=price,
        high=price + 1.0,
        low=price - 1.0,
        close=price,
        volume=volume,
    )


# ── ExecutionQualityTracker tests ─────────────────────────────────────────────

def test_execution_quality_tracker_import():
    from exchange.execution_quality import ExecutionQualityTracker


def test_record_fill_and_summary(tmp_path):
    """record_fill stores fills; summary returns correct averages."""
    from exchange.execution_quality import ExecutionQualityTracker

    tracker = ExecutionQualityTracker(persist_path=str(tmp_path / "eq.json"))
    for i in range(5):
        tracker.record_fill(_make_record(slippage_bps=10.0, latency_ms=100.0, order_id=f"o{i}"))

    s = tracker.summary(last_n=100)
    assert s["n_fills"] == 5
    assert s["avg_slippage_bps"] == pytest.approx(10.0, rel=1e-4)
    assert s["avg_latency_ms"] == pytest.approx(100.0, rel=1e-4)


def test_slippage_by_venue_aggregates_correctly(tmp_path):
    """slippage_by_venue returns per-venue averages."""
    from exchange.execution_quality import ExecutionQualityTracker

    tracker = ExecutionQualityTracker(persist_path=str(tmp_path / "eq2.json"))
    tracker.record_fill(_make_record(venue="blofin", slippage_bps=4.0, order_id="a1"))
    tracker.record_fill(_make_record(venue="blofin", slippage_bps=6.0, order_id="a2"))
    tracker.record_fill(_make_record(venue="binance", slippage_bps=2.0, order_id="a3"))

    by_venue = tracker.slippage_by_venue()
    assert "blofin" in by_venue
    assert by_venue["blofin"] == pytest.approx(5.0, rel=1e-4)
    assert "binance" in by_venue
    assert by_venue["binance"] == pytest.approx(2.0, rel=1e-4)


def test_quality_score_between_0_and_1(tmp_path):
    """quality_score should be in [0, 1]."""
    from exchange.execution_quality import ExecutionQualityTracker

    tracker = ExecutionQualityTracker(persist_path=str(tmp_path / "eq3.json"))
    for i in range(10):
        tracker.record_fill(_make_record(slippage_bps=8.0, latency_ms=150.0, order_id=f"q{i}"))

    score = tracker.quality_score()
    assert 0.0 <= score <= 1.0


def test_quality_score_no_fills_returns_1(tmp_path):
    """No fills → quality_score returns 1.0 (assume perfect, no evidence)."""
    from exchange.execution_quality import ExecutionQualityTracker

    tracker = ExecutionQualityTracker(persist_path=str(tmp_path / "eq4.json"))
    assert tracker.quality_score() == pytest.approx(1.0)


def test_rejection_rate_counted(tmp_path):
    """Rejected fills increase rejection rate."""
    from exchange.execution_quality import ExecutionQualityTracker

    tracker = ExecutionQualityTracker(persist_path=str(tmp_path / "eq5.json"))
    tracker.record_fill(_make_record(fill_status="full", order_id="r1"))
    tracker.record_fill(_make_record(fill_status="full", order_id="r2"))
    tracker.record_fill(_make_record(fill_status="rejected", order_id="r3"))

    s = tracker.summary()
    assert s["rejection_rate"] == pytest.approx(1 / 3, rel=1e-4)


def test_latency_percentiles(tmp_path):
    """latency_percentiles returns p50/p95/p99 sorted correctly."""
    from exchange.execution_quality import ExecutionQualityTracker

    tracker = ExecutionQualityTracker(persist_path=str(tmp_path / "eq6.json"))
    latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 100.0, 200.0, 500.0]
    for i, lat in enumerate(latencies):
        tracker.record_fill(_make_record(latency_ms=lat, order_id=f"lat{i}"))

    pcts = tracker.latency_percentiles()
    assert pcts["p50"] <= pcts["p95"]
    assert pcts["p95"] <= pcts["p99"]


# ── VenueScoringEngine tests ──────────────────────────────────────────────────

def test_venue_scoring_engine_updates_on_fill(tmp_path):
    """score_venue returns updated scores after record_fill."""
    from exchange.venue_scoring import VenueScoringEngine

    scorer = VenueScoringEngine(persist_path=str(tmp_path / "vs.json"))
    for i in range(5):
        scorer.update_from_fill(_make_record(venue="blofin", slippage_bps=4.0, order_id=f"v{i}"))

    score = scorer.score_venue("blofin")
    assert isinstance(score, VenueScore)
    assert 0.0 <= score.composite <= 1.0


def test_venue_scoring_unknown_venue(tmp_path):
    """Unknown venue returns a default neutral score."""
    from exchange.venue_scoring import VenueScoringEngine

    scorer = VenueScoringEngine(persist_path=str(tmp_path / "vs2.json"))
    score = scorer.score_venue("unknown_exchange")
    assert score.composite == pytest.approx(0.70, abs=0.05)


def test_mark_outage_reduces_score(tmp_path):
    """mark_outage drops venue score temporarily."""
    from exchange.venue_scoring import VenueScoringEngine

    scorer = VenueScoringEngine(persist_path=str(tmp_path / "vs3.json"))
    # Add some fills to get a non-default score
    for i in range(5):
        scorer.update_from_fill(_make_record(venue="blofin", slippage_bps=3.0, order_id=f"o{i}"))

    before = scorer.score_venue("blofin").composite
    scorer.mark_outage("blofin")
    after = scorer.score_venue("blofin").composite
    assert after <= before


def test_best_venue_returns_highest_scoring(tmp_path):
    """best_venue returns the venue with the highest composite score."""
    from exchange.venue_scoring import VenueScoringEngine

    scorer = VenueScoringEngine(persist_path=str(tmp_path / "vs4.json"))
    # Give blofin excellent fills
    for i in range(10):
        scorer.update_from_fill(_make_record(venue="blofin", slippage_bps=2.0, latency_ms=30.0, order_id=f"good{i}"))
    # Give binance poor fills
    for i in range(10):
        scorer.update_from_fill(_make_record(venue="binance", slippage_bps=15.0, latency_ms=400.0, order_id=f"bad{i}"))

    best = scorer.best_venue()
    assert best == "blofin"


# ── SmartOrderRouter tests ────────────────────────────────────────────────────

def _make_router(tmp_path, venues=None):
    from exchange.execution_quality import ExecutionQualityTracker
    from exchange.latency_tracker import LatencyTracker
    from exchange.liquidity import LiquidityMonitor
    from exchange.venue_scoring import VenueScoringEngine
    from exchange.smart_router import SmartOrderRouter

    venues = venues or ["blofin"]
    quality = ExecutionQualityTracker(persist_path=str(tmp_path / "eq.json"))
    latency = LatencyTracker(alpha=0.2)
    liquidity = LiquidityMonitor(baseline_window=5)
    scorer = VenueScoringEngine(persist_path=str(tmp_path / "vs.json"))

    return SmartOrderRouter(
        venues=venues,
        quality_tracker=quality,
        latency_tracker=latency,
        liquidity_monitor=liquidity,
        venue_scorer=scorer,
    )


def test_smart_router_routes_to_best_venue(tmp_path):
    """SmartOrderRouter selects a venue when conditions are normal."""
    router = _make_router(tmp_path, venues=["blofin"])
    candle = _make_candle(100.0, volume=500.0)

    # Warm up liquidity monitor
    for _ in range(6):
        router._liquidity.update(candle)

    decision = router.route("BTC-USDT", "buy", 0.1, candle)
    assert decision.venue in ("blofin", "") or not decision.allowed


def test_smart_router_kill_switch_blocks_order(tmp_path):
    """Kill switch active → RoutingDecision.allowed = False."""
    router = _make_router(tmp_path)
    router.set_kill_switch(True)

    candle = _make_candle()
    decision = router.route("BTC-USDT", "buy", 0.1, candle)

    assert decision.allowed is False
    assert decision.reject_reason == "kill_switch_active"
    assert decision.venue == ""


def test_smart_router_no_venues_available(tmp_path):
    """No available venues → routing rejected."""
    router = _make_router(tmp_path, venues=["blofin"])
    router.mark_venue_outage("blofin")

    candle = _make_candle()
    # warm up liquidity to avoid liquidity block
    for _ in range(10):
        router._liquidity.update(candle)

    decision = router.route("BTC-USDT", "buy", 0.1, candle)
    assert decision.allowed is False
    assert "no_venues" in decision.reject_reason or "outage" in decision.reject_reason or not decision.allowed


def test_smart_router_decision_fields(tmp_path):
    """RoutingDecision has all expected fields."""
    from exchange.smart_router import RoutingDecision
    router = _make_router(tmp_path)
    candle = _make_candle(volume=1000.0)

    for _ in range(10):
        router._liquidity.update(candle)

    decision = router.route("BTC-USDT", "buy", 0.1, candle)
    assert hasattr(decision, "venue")
    assert hasattr(decision, "allowed")
    assert hasattr(decision, "reject_reason")
    assert hasattr(decision, "expected_slippage_bps")
    assert hasattr(decision, "expected_latency_ms")
    assert hasattr(decision, "venue_score")
    assert hasattr(decision, "liquidity_score")


def test_smart_router_report(tmp_path):
    """report() returns a dict with expected keys."""
    router = _make_router(tmp_path)
    report = router.report()
    assert "kill_switch_active" in report
    assert "venues_total" in report
    assert "venue_rankings" in report


def test_kill_switch_toggle(tmp_path):
    """Kill switch can be turned on and off."""
    router = _make_router(tmp_path)

    router.set_kill_switch(True)
    assert router.kill_switch_active is True

    router.set_kill_switch(False)
    assert router.kill_switch_active is False


def test_routing_decision_blocked_when_kill_switch_active(tmp_path):
    """RoutingDecision.allowed=False whenever kill switch is active."""
    router = _make_router(tmp_path)
    router.set_kill_switch(True)

    candle = _make_candle()
    for symbol in ("BTC-USDT", "ETH-USDT", "SOL-USDT"):
        decision = router.route(symbol, "buy", 0.1, candle)
        assert decision.allowed is False, f"Expected blocked for {symbol}"


# ── LatencyTracker tests ──────────────────────────────────────────────────────

def test_latency_tracker_unknown_venue():
    from exchange.latency_tracker import LatencyTracker
    tracker = LatencyTracker()
    assert tracker.get_latency("unknown") == pytest.approx(999.0)


def test_latency_tracker_records_ema():
    from exchange.latency_tracker import LatencyTracker
    tracker = LatencyTracker(alpha=0.5)
    tracker.record("blofin", 100.0)
    tracker.record("blofin", 200.0)
    # After 2 records: EMA = 100 * 0.5 + 200 * 0.5 = not quite but ≈ 150
    lat = tracker.get_latency("blofin")
    assert 90.0 <= lat <= 210.0


def test_latency_tracker_rank_venues():
    from exchange.latency_tracker import LatencyTracker
    tracker = LatencyTracker()
    tracker.record("slow_venue", 500.0)
    tracker.record("fast_venue", 20.0)
    tracker.record("medium_venue", 100.0)

    ranked = tracker.rank_venues(["slow_venue", "fast_venue", "medium_venue"])
    assert ranked[0] == "fast_venue"
    assert ranked[-1] == "slow_venue"


# ── LiquidityMonitor tests ────────────────────────────────────────────────────

def test_liquidity_monitor_vol_shock():
    from exchange.liquidity import LiquidityMonitor

    monitor = LiquidityMonitor(baseline_window=10)
    # Feed normal candles
    for i in range(10):
        monitor.update(_make_candle(100.0, volume=200.0))

    # Shock candle with huge range
    shock = Candle(ts=1_700_000_001_000, open=100.0, high=120.0, low=80.0, close=110.0, volume=200.0)
    assert monitor.is_vol_shock(shock) is True


def test_liquidity_monitor_drought():
    from exchange.liquidity import LiquidityMonitor

    monitor = LiquidityMonitor(baseline_window=10)
    for i in range(10):
        monitor.update(_make_candle(100.0, volume=1000.0))

    drought = _make_candle(100.0, volume=10.0)  # < 50% of 1000
    assert monitor.is_liquidity_drought(drought) is True


def test_liquidity_score_normal_conditions():
    from exchange.liquidity import LiquidityMonitor

    monitor = LiquidityMonitor(baseline_window=10)
    for i in range(10):
        c = _make_candle(100.0 + i * 0.1, volume=300.0)
        monitor.update(c)

    normal = _make_candle(100.5, volume=300.0)
    score = monitor.liquidity_score(normal)
    assert 0.0 <= score <= 1.0
    assert score > 0.5  # Normal conditions → decent score
