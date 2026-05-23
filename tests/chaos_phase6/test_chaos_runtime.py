"""ChaosRuntime unit tests — Phase 6.

Tests validate individual chaos event behaviors, cooldown enforcement,
health snapshot structure, and bounded concurrency.

All tests complete in < 30s total.
"""
from __future__ import annotations

import threading
import time

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_runtime(tmp_path, **kwargs):
    try:
        from runtime.chaos_runtime import ChaosRuntime, ChaosRuntimeConfig  # type: ignore[import]
        defaults = dict(
            seed             = 42,
            event_cooldown_s = 0.0,
            latency_spike_ms = 30.0,
            audit_path       = str(tmp_path / "chaos_audit.jsonl"),
        )
        defaults.update(kwargs)
        return ChaosRuntime(config=ChaosRuntimeConfig(**defaults))
    except ImportError as exc:
        pytest.skip(f"chaos_runtime unavailable: {exc}")


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestChaosRuntime:
    """ChaosRuntime behavioral tests — all in < 30s."""

    def test_ws_reconnect_storm_recovers(self, tmp_path):
        """WS_RECONNECT_STORM must return RECOVERED or DEGRADED, never FATAL."""
        from runtime.chaos_runtime import ChaosEventType  # type: ignore[import]

        runtime = _make_runtime(tmp_path, ws_storm_reconnects=5)
        event = runtime.run_event(
            event_type = ChaosEventType.WS_RECONNECT_STORM,
            parameters = {"reconnects": 5},
        )

        assert event.outcome in ("RECOVERED", "DEGRADED"), (
            f"WS_RECONNECT_STORM returned unexpected outcome: {event.outcome}"
        )
        assert event.event_id, "Event must have an event_id"
        assert event.duration_ms >= 0.0, "Duration must be non-negative"

    def test_memory_pressure_bounded(self, tmp_path):
        """MEMORY_PRESSURE event must not cause unbounded memory growth."""
        from runtime.chaos_runtime import ChaosEventType  # type: ignore[import]

        runtime = _make_runtime(tmp_path, memory_pressure_mb=10)

        snap_before = runtime.take_health_snapshot()
        event = runtime.run_event(
            event_type = ChaosEventType.MEMORY_PRESSURE,
            parameters = {"memory_pressure_mb": 10},
        )
        snap_after = runtime.take_health_snapshot()

        assert event.outcome in ("RECOVERED", "DEGRADED", "FATAL", "SKIPPED")

        # Memory growth must be < 200MB
        growth_mb = snap_after.rss_mb - snap_before.rss_mb
        assert growth_mb < 200.0, (
            f"Memory grew by {growth_mb:.1f}MB during MEMORY_PRESSURE "
            f"(expected < 200MB)"
        )

    def test_snapshot_corruption_rejected(self, tmp_path):
        """SNAPSHOT_CORRUPTION_INJECTION must return RECOVERED (corruption detected)."""
        from runtime.chaos_runtime import ChaosEventType  # type: ignore[import]

        runtime = _make_runtime(tmp_path)
        event = runtime.run_event(
            event_type = ChaosEventType.SNAPSHOT_CORRUPTION_INJECTION,
            parameters = {"snapshot_dir": str(tmp_path / "chaos_snapshots")},
        )

        assert event.outcome == "RECOVERED", (
            f"SNAPSHOT_CORRUPTION_INJECTION expected RECOVERED (corruption detected) "
            f"but got: {event.outcome}"
        )

    def test_cooldown_enforced(self, tmp_path):
        """Running the same event type twice rapidly — second must be SKIPPED."""
        from runtime.chaos_runtime import ChaosRuntime, ChaosRuntimeConfig, ChaosEventType  # type: ignore[import]

        # Use a real cooldown (5 seconds)
        config = ChaosRuntimeConfig(
            seed             = 42,
            event_cooldown_s = 5.0,
            latency_spike_ms = 10.0,
            audit_path       = str(tmp_path / "chaos_audit.jsonl"),
        )
        runtime = ChaosRuntime(config=config)

        # First event should run
        event1 = runtime.run_event(ChaosEventType.LATENCY_SPIKE)
        assert event1.outcome in ("RECOVERED", "DEGRADED", "FATAL"), \
            f"First event should not be SKIPPED, got: {event1.outcome}"

        # Second event immediately after — must be SKIPPED due to cooldown
        event2 = runtime.run_event(ChaosEventType.LATENCY_SPIKE)
        assert event2.outcome == "SKIPPED", (
            f"Second event within cooldown should be SKIPPED, got: {event2.outcome}"
        )

    def test_health_snapshot_structure(self, tmp_path):
        """take_health_snapshot() must return RuntimeHealthSnapshot with all fields."""
        from runtime.chaos_runtime import RuntimeHealthSnapshot  # type: ignore[import]

        runtime = _make_runtime(tmp_path)
        snap = runtime.take_health_snapshot()

        assert isinstance(snap, RuntimeHealthSnapshot), \
            f"Expected RuntimeHealthSnapshot, got: {type(snap)}"
        assert isinstance(snap.snapshot_at, str) and snap.snapshot_at, \
            "snapshot_at must be a non-empty string"
        assert isinstance(snap.thread_count, int) and snap.thread_count >= 0, \
            "thread_count must be a non-negative int"
        assert isinstance(snap.open_fd_count, int) and snap.open_fd_count >= 0, \
            "open_fd_count must be a non-negative int"
        assert isinstance(snap.rss_mb, float) and snap.rss_mb >= 0.0, \
            "rss_mb must be a non-negative float"
        assert isinstance(snap.survivability_score, float), \
            "survivability_score must be a float"
        assert isinstance(snap.active_chaos_events, int), \
            "active_chaos_events must be an int"
        assert isinstance(snap.total_chaos_events, int), \
            "total_chaos_events must be an int"
        assert isinstance(snap.incident_count, int), \
            "incident_count must be an int"

    def test_concurrent_chaos_bounded(self, tmp_path):
        """max_concurrent_chaos=1: second concurrent event must be SKIPPED."""
        from runtime.chaos_runtime import ChaosRuntime, ChaosRuntimeConfig, ChaosEventType  # type: ignore[import]

        config = ChaosRuntimeConfig(
            seed                 = 42,
            max_concurrent_chaos = 1,
            event_cooldown_s     = 0.0,
            latency_spike_ms     = 500.0,   # long enough to block
            audit_path           = str(tmp_path / "chaos_audit.jsonl"),
        )
        runtime = ChaosRuntime(config=config)

        results: list = []
        barrier = threading.Barrier(2)

        def _run_event(event_type):
            barrier.wait()
            ev = runtime.run_event(event_type)
            results.append(ev)

        t1 = threading.Thread(
            target=_run_event,
            args=(ChaosEventType.LATENCY_SPIKE,),
            daemon=True,
        )
        t2 = threading.Thread(
            target=_run_event,
            args=(ChaosEventType.MEMORY_PRESSURE,),
            daemon=True,
        )

        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert len(results) == 2, f"Expected 2 results, got {len(results)}"

        outcomes = {r.outcome for r in results}
        assert "SKIPPED" in outcomes, (
            f"Expected at least one SKIPPED result with max_concurrent_chaos=1, "
            f"got outcomes: {outcomes}"
        )
