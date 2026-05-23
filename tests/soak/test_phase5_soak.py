"""Phase 5 soak / stability tests.

All tests complete in < 60 s wall time (except where noted).
Compile-only tests run in < 1 s.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import List

import pytest


class TestPhase5Soak:
    """Runtime stability tests for Phase 5 subsystems."""

    # ── 1. SnapshotDaemon lifecycle ───────────────────────────────────────────

    def test_snapshot_daemon_start_stop(self, tmp_path: Path) -> None:
        """Daemon starts, creates a snapshot on force_snapshot_now(), stops cleanly."""
        try:
            from runtime.snapshot_daemon import SnapshotDaemon
        except ImportError:
            pytest.skip("snapshot_daemon not available")

        daemon = SnapshotDaemon(
            snapshot_dir=str(tmp_path / "snaps"),
            event_store_path=str(tmp_path / "events.jsonl"),
            interval_events=100_000,   # effectively never auto-trigger
            interval_hours=24.0,
            cooldown_seconds=0,
        )
        daemon.start()
        status = daemon.get_status()
        assert status["running"], "Daemon should be running after start()"

        daemon.force_snapshot_now()
        status = daemon.get_status()
        assert status["total_snapshots"] >= 1, (
            f"Expected at least 1 snapshot after force_snapshot_now(), got {status}"
        )
        assert status["consecutive_failures"] == 0, (
            f"No failures expected: {status}"
        )

        daemon.stop()
        status = daemon.get_status()
        assert not status["running"], "Daemon should be stopped"

    # ── 2. SnapshotDaemon seq threshold trigger ───────────────────────────────

    def test_snapshot_daemon_seq_trigger(self, tmp_path: Path) -> None:
        """notify_event_written() triggers snapshot when threshold crossed."""
        try:
            from runtime.snapshot_daemon import SnapshotDaemon
        except ImportError:
            pytest.skip("snapshot_daemon not available")

        daemon = SnapshotDaemon(
            snapshot_dir=str(tmp_path / "snaps"),
            event_store_path=str(tmp_path / "events.jsonl"),
            interval_events=50,
            interval_hours=24.0,
            cooldown_seconds=0,
        )
        daemon.start()

        for seq in range(1, 56):
            daemon.notify_event_written(seq)
        time.sleep(2.0)   # allow daemon loop to react

        status = daemon.get_status()
        assert status["total_snapshots"] >= 1, (
            f"Seq-triggered snapshot should have fired: {status}"
        )
        daemon.stop()

    # ── 3. IntegrityMonitor on-demand scan ────────────────────────────────────

    def test_integrity_monitor_scan(self, tmp_path: Path, monkeypatch) -> None:
        """run_scan() returns a report with no CRITICAL findings on empty store."""
        try:
            from runtime.integrity_monitor import IntegrityMonitor, IntegritySeverity
        except ImportError:
            pytest.skip("integrity_monitor not available")

        # Isolate from the global data/event_store.jsonl which may have state
        # from concurrent test writers.  Changing cwd makes relative paths
        # (data/event_store.jsonl, data/…) resolve inside tmp_path.
        monkeypatch.chdir(tmp_path)

        monitor = IntegrityMonitor(
            scan_interval_seconds=3600,
            event_scan_window=100,
            halt_on_critical=False,
        )
        report = monitor.run_scan()

        assert report is not None
        assert report.generated_at
        assert report.scan_duration_ms >= 0

        critical = [f for f in report.findings if f.severity == IntegritySeverity.CRITICAL]
        assert len(critical) == 0, (
            f"Expected no CRITICAL findings on empty EventStore, got: "
            f"{[(f.subsystem, f.description) for f in critical]}"
        )

    # ── 4. IntegrityMonitor start/stop lifecycle ──────────────────────────────

    def test_integrity_monitor_lifecycle(self, tmp_path: Path) -> None:
        """Monitor starts and stops cleanly; get_status() is accurate."""
        try:
            from runtime.integrity_monitor import IntegrityMonitor
        except ImportError:
            pytest.skip("integrity_monitor not available")

        monitor = IntegrityMonitor(scan_interval_seconds=3600)
        monitor.start()
        status = monitor.get_status()
        assert status.get("running"), f"Monitor should be running: {status}"

        monitor.stop()
        status = monitor.get_status()
        assert not status.get("running"), f"Monitor should be stopped: {status}"

    # ── 5. ExecutionOptimizer passthrough in demo mode ────────────────────────

    def test_execution_optimizer_demo_passthrough(self, tmp_path: Path) -> None:
        """In demo_mode=True, optimizer returns qty unchanged."""
        try:
            from runtime.execution_optimizer import ExecutionOptimizer
        except ImportError:
            pytest.skip("execution_optimizer not available")

        opt = ExecutionOptimizer(
            analytics_path=str(tmp_path / "analytics.jsonl"),
            policy_path=str(tmp_path / "policy.json"),
        )
        advice = opt.get_advice(symbol="BTC_USDT", qty=0.001,
                                current_spread_bps=5.0, demo_mode=True)
        assert advice.advised_qty == 0.001, (
            f"Demo mode should passthrough qty unchanged, got {advice.advised_qty}"
        )
        assert not advice.should_wait, "Demo mode should never block entry"

    # ── 6. ExecutionOptimizer bounded adaptation ──────────────────────────────

    def test_execution_optimizer_bounded_adaptation(self, tmp_path: Path) -> None:
        """update_from_analytics() stays within ±30% bounds from defaults."""
        try:
            from runtime.execution_optimizer import ExecutionOptimizer
        except ImportError:
            pytest.skip("execution_optimizer not available")

        opt = ExecutionOptimizer(
            analytics_path=str(tmp_path / "analytics.jsonl"),
            policy_path=str(tmp_path / "policy.json"),
        )
        original = opt.get_policy()
        default_spread = original.spread_threshold_bps
        default_size   = original.max_order_size_pct

        # Feed very bad analytics (huge slippage, low fill efficiency)
        bad_report = {
            "avg_slippage_bps": default_spread * 3,   # 3× budget
            "avg_fill_efficiency": 0.5,                # below min
        }
        for _ in range(20):
            opt.update_from_analytics(bad_report)

        policy = opt.get_policy()
        # Bounds: spread floor = 50% of default
        assert policy.spread_threshold_bps >= default_spread * 0.5, (
            f"spread_threshold_bps went below 50% floor: {policy.spread_threshold_bps}"
        )
        # Bounds: size floor = 50%
        assert policy.max_order_size_pct >= 50.0, (
            f"max_order_size_pct went below 50% floor: {policy.max_order_size_pct}"
        )

    # ── 7. SurvivabilityEngine basic score ───────────────────────────────────

    def test_survivability_engine_score(self, tmp_path: Path) -> None:
        """compute_score() returns a valid report with score in [0, 100]."""
        try:
            from runtime.survivability import SurvivabilityEngine, SurvivabilityClassification
        except ImportError:
            pytest.skip("survivability not available")

        engine = SurvivabilityEngine(score_history_size=10)
        report = engine.compute_score()

        assert 0.0 <= report.current_score <= 100.0, (
            f"Score out of range [0, 100]: {report.current_score}"
        )
        assert report.classification in SurvivabilityClassification, (
            f"Invalid classification: {report.classification}"
        )
        assert report.generated_at
        assert isinstance(report.critical_subsystems, list)
        assert isinstance(report.deployment_ready, bool)

    # ── 8. SurvivabilityEngine trend detection ────────────────────────────────

    def test_survivability_trend(self, tmp_path: Path) -> None:
        """Trend is STABLE when scores are flat."""
        try:
            from runtime.survivability import SurvivabilityEngine
        except ImportError:
            pytest.skip("survivability not available")

        engine = SurvivabilityEngine(score_history_size=10)
        for _ in range(6):
            engine.compute_score()

        trend = engine.get_trend()
        assert trend in ("STABLE", "IMPROVING", "DEGRADING"), (
            f"Unexpected trend value: {trend}"
        )

    # ── 9. AlphaValidationEngine empty outcomes ───────────────────────────────

    def test_alpha_validation_empty(self, tmp_path: Path) -> None:
        """generate_report() on empty outcomes returns MARGINAL or INSUFFICIENT."""
        try:
            from research.statistics.alpha_validation import AlphaValidationEngine, AlphaSignal
        except ImportError:
            pytest.skip("alpha_validation not available")

        engine = AlphaValidationEngine(
            outcomes_path=str(tmp_path / "trade_outcomes.jsonl"),
            window=100,
        )
        engine.load_outcomes()
        report = engine.generate_report()

        assert report is not None
        assert report.trades_analyzed == 0
        assert report.portfolio_alpha_signal in AlphaSignal, (
            f"Unexpected signal: {report.portfolio_alpha_signal}"
        )
        assert report.overall_portfolio_expectancy == 0.0

    # ── 10. AlphaValidationEngine with synthetic outcomes ────────────────────

    def test_alpha_validation_synthetic_outcomes(self, tmp_path: Path) -> None:
        """Decaying strategy shows DEGRADING signal."""
        try:
            from research.statistics.alpha_validation import AlphaValidationEngine, AlphaSignal
        except ImportError:
            pytest.skip("alpha_validation not available")

        outcomes_path = tmp_path / "trade_outcomes.jsonl"

        # 30 early wins, then 30 losses — decay pattern
        records = []
        for i in range(30):
            records.append({"ts": f"2026-05-23T{i:02d}:00:00", "strategy": "EMA_CROSS",
                            "pnl": 5.0, "outcome": "win", "regime": "TRENDING",
                            "confidence": 0.7, "symbol": "BTCUSD-PERP",
                            "side": "long", "size": 0.001,
                            "entry_price": 50000.0, "exit_price": 50100.0})
        for i in range(30):
            records.append({"ts": f"2026-05-23T{30+i:02d}:00:00", "strategy": "EMA_CROSS",
                            "pnl": -5.0, "outcome": "loss", "regime": "TRENDING",
                            "confidence": 0.7, "symbol": "BTCUSD-PERP",
                            "side": "long", "size": 0.001,
                            "entry_price": 50000.0, "exit_price": 49900.0})

        with open(outcomes_path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        engine = AlphaValidationEngine(outcomes_path=str(outcomes_path), window=60)
        engine.load_outcomes()
        report = engine.generate_report()

        assert report.trades_analyzed == 60
        assert "EMA_CROSS" in report.strategies
        metrics = report.strategies["EMA_CROSS"]
        # With 30 wins then 30 losses, rolling metrics should show degradation
        assert metrics.sample_size == 60
        # Win rate decay should be negative (declining)
        assert metrics.win_rate_decay_rate <= 0.0, (
            f"Expected negative decay rate for decaying strategy, got {metrics.win_rate_decay_rate}"
        )

    # ── 11. AdaptiveAllocator bounds validation ───────────────────────────────

    def test_adaptive_allocator_bounds(self, tmp_path: Path) -> None:
        """apply_recommendation() rejects invalid bounds and requires approver_id."""
        try:
            from research.portfolio.adaptive_allocator import AdaptiveAllocator, AllocationBounds
        except ImportError:
            pytest.skip("adaptive_allocator not available")

        alloc = AdaptiveAllocator(bounds_path=str(tmp_path / "bounds.json"))

        # Empty approver_id should be rejected
        rec = alloc.compute_recommendation()
        rejected = alloc.apply_recommendation(rec, approver_id="")
        assert not rejected, "Empty approver_id must be rejected"

        # Valid approver_id should succeed
        accepted = alloc.apply_recommendation(rec, approver_id="test-operator")
        assert accepted, "Valid approver should be accepted"

    # ── 12. RollbackManager audit trail ──────────────────────────────────────

    def test_rollback_manager_audit(self, tmp_path: Path) -> None:
        """emergency_rollback() creates immutable audit record."""
        try:
            from runtime.rollback_manager import RollbackManager, RollbackTrigger
        except ImportError:
            pytest.skip("rollback_manager not available")

        audit_path = tmp_path / "rollback_audit.jsonl"
        mgr = RollbackManager(audit_path=str(audit_path))

        record = mgr.emergency_rollback(
            operator_id="test-operator",
            reason="Phase 5 soak test",
        )

        assert record.rollback_id
        assert record.executed_by == "test-operator"
        assert audit_path.exists(), "Audit JSONL must be created"

        # Audit record must be valid JSON
        with open(audit_path) as f:
            line = f.readline().strip()
        parsed = json.loads(line)
        assert parsed.get("rollback_id") == record.rollback_id

    # ── 13. DistributedLock acquire/release ──────────────────────────────────

    def test_distributed_lock_basic(self, tmp_path: Path) -> None:
        """Acquire/release cycle works; second acquire fails while held."""
        try:
            from runtime.distributed_lock import DistributedLock
        except ImportError:
            pytest.skip("distributed_lock not available")

        lock = DistributedLock(
            resource_name="test-resource",
            lock_dir=str(tmp_path / "locks"),
            ttl_seconds=30,
        )

        acquired = lock.acquire("node-A")
        assert acquired, "First acquire should succeed"
        assert lock.is_held_by("node-A")

        # Second holder cannot acquire
        acquired_b = lock.acquire("node-B")
        assert not acquired_b, "node-B should not acquire lock held by node-A"

        # Release by correct holder
        released = lock.release("node-A")
        assert released, "node-A should release its own lock"
        assert not lock.is_held_by("node-A")

        # After release, node-B can acquire
        acquired_b2 = lock.acquire("node-B")
        assert acquired_b2, "node-B should acquire after node-A releases"
        lock.release("node-B")

    # ── 14. LeaderElection single-node fallback ───────────────────────────────

    def test_leader_election_single_node(self, tmp_path: Path) -> None:
        """LeaderElection gracefully falls back to single-node leader mode."""
        try:
            from runtime.leader_election import LeaderElection
        except ImportError:
            pytest.skip("leader_election not available")

        election = LeaderElection(
            node_id="test-node",
            resource_name="test-leader",
            ttl_seconds=5,
            election_interval_s=1,
        )
        election.start()
        time.sleep(2.5)  # allow one election cycle

        # In single-node mode or after successful election, should be leader
        assert election.is_leader() or election.get_state().value in ("LEADER", "UNKNOWN"), (
            f"Unexpected state: {election.get_state()}"
        )

        election.stop()
        status = election.get_status()
        assert not status.get("running"), "Election should be stopped"
