"""Phase 6 chaos tests — latency profiler and execution telemetry."""
from __future__ import annotations

import time
import pytest

# ── Guard imports ─────────────────────────────────────────────────────────────

try:
    from runtime.latency_profiler import (
        LatencyProfiler,
        OperationCategory,
        get_profiler,
    )
    _PROFILER_AVAILABLE = True
except ImportError as _exc:
    _PROFILER_AVAILABLE = False
    _PROFILER_ERR = str(_exc)

try:
    from runtime.execution_telemetry import (
        ExecutionTelemetryCollector,
        get_telemetry,
    )
    _TELEMETRY_AVAILABLE = True
except ImportError as _exc:
    _TELEMETRY_AVAILABLE = False
    _TELEMETRY_ERR = str(_exc)


def _require_profiler():
    if not _PROFILER_AVAILABLE:
        pytest.skip(f"latency_profiler unavailable: {_PROFILER_ERR}")


def _require_telemetry():
    if not _TELEMETRY_AVAILABLE:
        pytest.skip(f"execution_telemetry unavailable: {_TELEMETRY_ERR}")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def profiler(tmp_path):
    """Fresh LatencyProfiler backed by a tmp analytics path."""
    _require_profiler()
    return LatencyProfiler(
        analytics_path=str(tmp_path / "latency.jsonl"),
        max_samples_per_op=10_000,
    )


@pytest.fixture()
def collector(tmp_path):
    """Fresh ExecutionTelemetryCollector backed by a tmp analytics path."""
    _require_telemetry()
    return ExecutionTelemetryCollector(
        analytics_path=str(tmp_path / "exec_telemetry.jsonl"),
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_latency_record_and_stats(profiler):
    """Record 100 samples and verify p50 < p99."""
    import random
    random.seed(42)
    for _ in range(100):
        ms = random.uniform(1.0, 500.0)
        profiler.record(OperationCategory.REST_API, "fetch_ticker", ms)

    stats = profiler.get_stats(OperationCategory.REST_API, "fetch_ticker")
    assert stats is not None, "get_stats() should return a LatencyStats object"
    assert stats.sample_count == 100
    assert stats.p50_ms < stats.p99_ms, "p50 must be less than p99 for a spread distribution"
    assert stats.min_ms <= stats.p50_ms
    assert stats.p99_ms <= stats.max_ms


def test_ewma_convergence(profiler):
    """Feed 50 identical latency values; EWMA should converge near that value."""
    target_ms = 42.0
    for _ in range(50):
        profiler.record(OperationCategory.WEBSOCKET, "ws_ping", target_ms)

    stats = profiler.get_stats(OperationCategory.WEBSOCKET, "ws_ping")
    assert stats is not None
    # After 50 identical samples EWMA should be within 2% of target
    assert abs(stats.ewma_ms - target_ms) < target_ms * 0.02, (
        f"EWMA {stats.ewma_ms:.4f} should be close to {target_ms}"
    )


def test_anomaly_detection(profiler):
    """Feed a mix of 5 ms and 100 ms samples; anomaly_detected should be True."""
    # p50 ≈ 5ms, p99 >> 5ms (because of high-latency outliers)
    for _ in range(90):
        profiler.record(OperationCategory.REST_API, "place_order", 5.0)
    for _ in range(10):
        profiler.record(OperationCategory.REST_API, "place_order", 100.0)

    stats = profiler.get_stats(OperationCategory.REST_API, "place_order")
    assert stats is not None
    # p99 ≈ 100ms, p50 ≈ 5ms → p99 > 3 * p50 → anomaly
    assert stats.anomaly_detected, (
        f"Expected anomaly_detected=True; p50={stats.p50_ms:.2f}, p99={stats.p99_ms:.2f}"
    )


def test_context_manager_measures_elapsed(profiler):
    """Use the `with profiler.measure(...)` context manager and verify a sample is recorded."""
    import time as _time

    with profiler.measure(OperationCategory.SNAPSHOT, "snap_write"):
        _time.sleep(0.01)  # ~10ms sleep

    stats = profiler.get_stats(OperationCategory.SNAPSHOT, "snap_write")
    assert stats is not None, "measure() context manager must record a sample"
    assert stats.sample_count == 1
    # Should have measured at least 5ms (accounting for timer resolution)
    assert stats.p50_ms >= 5.0, f"Expected >= 5ms but got {stats.p50_ms:.2f}ms"


def test_telemetry_snapshot_health(collector, tmp_path):
    """Record low-latency samples, then snapshot should be HEALTHY."""
    _require_profiler()
    # Inject low-latency samples into a fresh profiler backing the collector
    profiler = LatencyProfiler(
        analytics_path=str(tmp_path / "lat.jsonl"),
        max_samples_per_op=1000,
    )
    # Monkey-patch collector to use our profiler
    import runtime.latency_profiler as _lp_mod
    original_get_profiler = _lp_mod.get_profiler

    def _patched_profiler():
        return profiler

    def _patched_collector_profiler():
        return profiler, OperationCategory

    collector._profiler = _patched_collector_profiler

    # Record healthy (very low) latencies
    for _ in range(50):
        profiler.record(OperationCategory.WEBSOCKET, "ws_message", 2.0)
        profiler.record(OperationCategory.REST_API, "fetch_ticker", 20.0)

    snapshot = collector.get_telemetry_snapshot()
    # With very low latencies and default survivability=50 we expect at least not CRITICAL
    # (exchange_degradation_score will be low, survivability defaults to 50 >= 40)
    assert snapshot.telemetry_health in ("HEALTHY", "DEGRADED"), (
        f"Expected HEALTHY or DEGRADED, got {snapshot.telemetry_health}"
    )
    assert snapshot.ws_latency_p99_ms >= 0.0


def test_rollback_triggers_latency(collector, tmp_path):
    """Record ws samples at 2000ms p99, verify get_rollback_triggers includes LATENCY_EXPLOSION."""
    _require_profiler()
    profiler = LatencyProfiler(
        analytics_path=str(tmp_path / "lat.jsonl"),
        max_samples_per_op=1000,
    )

    def _patched_collector_profiler():
        return profiler, OperationCategory

    collector._profiler = _patched_collector_profiler

    # Push samples that will produce a high p99 (> 1000ms threshold)
    for _ in range(90):
        profiler.record(OperationCategory.WEBSOCKET, "ws_message", 100.0)
    for _ in range(10):
        profiler.record(OperationCategory.WEBSOCKET, "ws_message", 3000.0)

    triggers = collector.get_rollback_triggers()
    assert "LATENCY_EXPLOSION" in triggers, (
        f"Expected LATENCY_EXPLOSION in triggers, got: {triggers}"
    )
