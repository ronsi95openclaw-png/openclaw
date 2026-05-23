"""Phase 7 chaos tests covering the 6 new ChaosEventType values.

All tests use tmp_path so they never touch real project data directories.
All tests complete in < 30 s total.
"""
from __future__ import annotations

import json
import os
import time

import pytest

# ---------------------------------------------------------------------------
# Guard import — skip entire module if ChaosRuntime cannot be imported
# ---------------------------------------------------------------------------
try:
    from runtime.chaos_runtime import (
        ChaosRuntime,
        ChaosRuntimeConfig,
        ChaosEventType,
    )
    _IMPORT_OK = True
except Exception as _exc:
    _IMPORT_OK = False
    _IMPORT_EXC = _exc

if not _IMPORT_OK:
    pytest.skip(
        f"runtime.chaos_runtime not importable: {_IMPORT_EXC}",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runtime(tmp_path, *, cooldown_s: float = 0.0) -> ChaosRuntime:
    """Create a fresh ChaosRuntime whose audit file lands in tmp_path."""
    cfg = ChaosRuntimeConfig(
        seed=42,
        max_concurrent_chaos=10,
        event_cooldown_s=cooldown_s,
        audit_path=str(tmp_path / "chaos_audit.jsonl"),
    )
    return ChaosRuntime(cfg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBalanceCorruptionSimulation:
    def test_balance_corruption_simulation(self, tmp_path):
        """BALANCE_CORRUPTION_SIMULATION should produce RECOVERED or SKIPPED."""
        rt = _make_runtime(tmp_path)
        ev = rt.run_event(
            ChaosEventType.BALANCE_CORRUPTION_SIMULATION,
            parameters={"tmp_dir": str(tmp_path)},
        )
        assert ev.outcome in ("RECOVERED", "SKIPPED"), (
            f"Unexpected outcome: {ev.outcome}"
        )
        assert ev.event_type == ChaosEventType.BALANCE_CORRUPTION_SIMULATION


class TestReplayDivergenceInjection:
    def test_replay_divergence_injection(self, tmp_path):
        """REPLAY_DIVERGENCE_INJECTION should produce RECOVERED or SKIPPED."""
        rt = _make_runtime(tmp_path)
        ev = rt.run_event(
            ChaosEventType.REPLAY_DIVERGENCE_INJECTION,
            parameters={"tmp_dir": str(tmp_path)},
        )
        assert ev.outcome in ("RECOVERED", "SKIPPED"), (
            f"Unexpected outcome: {ev.outcome}"
        )
        assert ev.event_type == ChaosEventType.REPLAY_DIVERGENCE_INJECTION


class TestApprovalSignatureTampering:
    def test_approval_tampering_detected(self, tmp_path):
        """APPROVAL_SIGNATURE_TAMPERING: tampered record must be rejected (RECOVERED)."""
        rt = _make_runtime(tmp_path)
        ev = rt.run_event(
            ChaosEventType.APPROVAL_SIGNATURE_TAMPERING,
            parameters={},
        )
        # The tampered record must either be correctly rejected (RECOVERED)
        # or the module is unavailable (also RECOVERED by design).
        # It must never return DEGRADED (which would mean tamper was accepted).
        assert ev.outcome == "RECOVERED", (
            f"Tamper was not correctly rejected — outcome: {ev.outcome}"
        )


class TestSnapshotPartialTruncation:
    def test_snapshot_truncation_detected(self, tmp_path):
        """SNAPSHOT_PARTIAL_TRUNCATION: truncated file should be detected and rejected."""
        rt = _make_runtime(tmp_path)
        ev = rt.run_event(
            ChaosEventType.SNAPSHOT_PARTIAL_TRUNCATION,
            parameters={"tmp_dir": str(tmp_path)},
        )
        # Should detect corruption → RECOVERED (i.e. the system correctly rejected it)
        assert ev.outcome == "RECOVERED", (
            f"Truncated snapshot was not detected — outcome: {ev.outcome}"
        )


class TestLockContentionStorm:
    def test_lock_contention_bounded(self, tmp_path):
        """LOCK_CONTENTION_STORM: outcome must be RECOVERED or DEGRADED, never FATAL."""
        lock_dir = tmp_path / "chaos_locks"
        rt = _make_runtime(tmp_path)
        ev = rt.run_event(
            ChaosEventType.LOCK_CONTENTION_STORM,
            parameters={"tmp_dir": str(lock_dir), "n_threads": 5},
        )
        assert ev.outcome in ("RECOVERED", "DEGRADED", "SKIPPED"), (
            f"Lock contention produced unexpected outcome: {ev.outcome}"
        )
        assert ev.outcome != "FATAL", (
            "Lock contention storm must never produce FATAL"
        )


class TestRollbackCascadeBounded:
    def test_rollback_cascade_bounded(self, tmp_path):
        """DEPLOYMENT_ROLLBACK_CASCADE: outcome must be RECOVERED or DEGRADED, never FATAL."""
        rt = _make_runtime(tmp_path)
        ev = rt.run_event(
            ChaosEventType.DEPLOYMENT_ROLLBACK_CASCADE,
            parameters={},
        )
        assert ev.outcome in ("RECOVERED", "DEGRADED", "SKIPPED"), (
            f"Rollback cascade produced unexpected outcome: {ev.outcome}"
        )
        assert ev.outcome != "FATAL", (
            "Rollback cascade must never produce FATAL"
        )


class TestAuditLogging:
    def test_phase7_events_audit_logged(self, tmp_path):
        """After running 3 Phase 7 events, the audit JSONL must have 3 entries."""
        audit_path = tmp_path / "chaos_audit.jsonl"
        cfg = ChaosRuntimeConfig(
            seed=99,
            max_concurrent_chaos=10,
            event_cooldown_s=0.0,
            audit_path=str(audit_path),
        )
        rt = ChaosRuntime(cfg)

        event_types = [
            ChaosEventType.BALANCE_CORRUPTION_SIMULATION,
            ChaosEventType.REPLAY_DIVERGENCE_INJECTION,
            ChaosEventType.SNAPSHOT_PARTIAL_TRUNCATION,
        ]
        params_map = {
            ChaosEventType.BALANCE_CORRUPTION_SIMULATION: {},
            ChaosEventType.REPLAY_DIVERGENCE_INJECTION: {},
            ChaosEventType.SNAPSHOT_PARTIAL_TRUNCATION: {"tmp_dir": str(tmp_path)},
        }

        for et in event_types:
            rt.run_event(et, parameters=params_map[et])

        assert audit_path.exists(), "Audit JSONL was not created"
        lines = [
            ln.strip()
            for ln in audit_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        # Parse all lines to verify they are valid JSON
        records = []
        for ln in lines:
            records.append(json.loads(ln))

        assert len(records) == 3, (
            f"Expected 3 audit entries, got {len(records)}: {records}"
        )
        # Verify each record has required fields
        for rec in records:
            assert "event_id" in rec
            assert "event_type" in rec
            assert "outcome" in rec
            assert rec["outcome"] in ("RECOVERED", "DEGRADED", "FATAL", "SKIPPED")


class TestCooldownEnforcement:
    def test_cooldown_still_enforced_phase7(self, tmp_path):
        """Running the same Phase 7 event type twice rapidly → second must be SKIPPED."""
        rt = _make_runtime(tmp_path, cooldown_s=60.0)  # 60s cooldown

        # First run — should execute
        ev1 = rt.run_event(
            ChaosEventType.BALANCE_CORRUPTION_SIMULATION,
            parameters={},
        )
        # Second run immediately — should be SKIPPED due to cooldown
        ev2 = rt.run_event(
            ChaosEventType.BALANCE_CORRUPTION_SIMULATION,
            parameters={},
        )

        assert ev1.outcome != "SKIPPED", (
            "First run should not be SKIPPED"
        )
        assert ev2.outcome == "SKIPPED", (
            f"Second run within cooldown should be SKIPPED, got: {ev2.outcome}"
        )
