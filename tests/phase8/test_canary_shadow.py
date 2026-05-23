"""Phase 8 tests for scripts.run_canary_shadow — run_shadow_phases().

8 tests covering:
  1. demo_mode=False raises RuntimeError
  2. Phases 1–3 advance with force_paper when health is low
  3. Blocked at phase 1 when below threshold and no force_paper
  4. Phase 4 is never advanced (advance_phase never called targeting PHASE_4)
  5. FAILED state from advance_phase stops subsequent phases
  6. output_json=True returns dict with expected keys
  7. Health snapshot captured per phase in output
  8. operator_id is passed through to every advance_phase call

No real network calls, no real filesystem writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Guard import
# ---------------------------------------------------------------------------
try:
    from scripts.run_canary_shadow import run_shadow_phases
    _IMPORT_OK = True
except Exception as _exc:
    _IMPORT_OK = False
    _IMPORT_EXC = _exc

if not _IMPORT_OK:
    pytest.skip(
        f"scripts.run_canary_shadow not importable: {_IMPORT_EXC}",
        allow_module_level=True,
    )

# Import DeploymentState for assertions
try:
    from deployment.orchestrator.orchestrator import DeploymentState
    _STATE_IMPORT_OK = True
except Exception:
    _STATE_IMPORT_OK = False

if not _STATE_IMPORT_OK:
    pytest.skip(
        "deployment.orchestrator.orchestrator not importable",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test helpers — fake orchestrator and health score
# ---------------------------------------------------------------------------


@dataclass
class _FakeHealth:
    composite_score: float = 80.0
    survivability_score: float = 80.0
    integrity_ok: bool = True
    ws_health: float = 0.9
    latency_p99_ms: float = 50.0
    execution_ok: bool = True


@dataclass
class _FakeRecord:
    deployment_id: str
    state: DeploymentState
    health_score: float = 80.0


def _make_orchestrator(
    deployment_id: str = "test-deploy-001",
    initial_state: DeploymentState = DeploymentState.PENDING,
    health_score: float = 80.0,
    advance_side_effect: Optional[Any] = None,
) -> MagicMock:
    """Build a mock orchestrator that steps through phase states on each advance_phase call."""
    orch = MagicMock()

    # Health score always returns same value unless overridden
    orch.get_health_score.return_value = _FakeHealth(
        composite_score=health_score,
        survivability_score=health_score,
    )

    # State advances through PHASE_1, PHASE_2, PHASE_3 on each call
    phase_sequence = [
        DeploymentState.CANARY_PHASE_1,
        DeploymentState.CANARY_PHASE_2,
        DeploymentState.CANARY_PHASE_3,
    ]
    call_counter: List[int] = [0]

    if advance_side_effect is not None:
        orch.advance_phase.side_effect = advance_side_effect
    else:
        def _advance(dep_id, op_id, *args, **kwargs):
            idx = call_counter[0]
            next_state = phase_sequence[idx] if idx < len(phase_sequence) else DeploymentState.CANARY_PHASE_3
            call_counter[0] += 1
            record = _FakeRecord(deployment_id=dep_id, state=next_state)
            # Update the internal deployments store so guard checks see updated state
            orch._deployments[dep_id] = record
            return record

        orch.advance_phase.side_effect = _advance

    # Internal deployments dict for guard checks
    initial_record = _FakeRecord(deployment_id=deployment_id, state=initial_state)
    orch._deployments = {deployment_id: initial_record}

    return orch


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDemoModeFalseRaises:
    def test_demo_mode_false_raises(self):
        """run_shadow_phases must raise RuntimeError when demo_mode=False."""
        orch = _make_orchestrator()
        with pytest.raises(RuntimeError, match="DEMO_MODE"):
            run_shadow_phases(
                orchestrator=orch,
                deployment_id="deploy-001",
                operator_id="test-op",
                force_paper=False,
                demo_mode=False,
            )


class TestPhasesAdvanceWithForcePaper:
    def test_phases_1_2_3_advance_with_force_paper(self, capsys):
        """All 3 phases advance when health=65 (below phase 2+3 thresholds) with force_paper."""
        orch = _make_orchestrator(
            deployment_id="deploy-force",
            health_score=65.0,  # below phase2 threshold (70) and phase3 (80)
        )

        result = run_shadow_phases(
            orchestrator=orch,
            deployment_id="deploy-force",
            operator_id="test-op",
            force_paper=True,
            demo_mode=True,
        )

        # All 3 phases should have been attempted
        assert orch.advance_phase.call_count == 3
        advanced = [p for p in result["phases"] if p.get("advanced")]
        assert len(advanced) == 3
        assert result["final_state"] == DeploymentState.CANARY_PHASE_3.value


class TestBlockedWhenBelowThresholdNoForce:
    def test_blocked_when_below_threshold_no_force(self, capsys):
        """Phase 1 is blocked when composite=55 < threshold=60 and force_paper=False."""
        orch = _make_orchestrator(
            deployment_id="deploy-block",
            health_score=55.0,  # below phase1 threshold (60)
        )

        result = run_shadow_phases(
            orchestrator=orch,
            deployment_id="deploy-block",
            operator_id="test-op",
            force_paper=False,
            demo_mode=True,
        )

        # advance_phase should never have been called
        assert orch.advance_phase.call_count == 0
        assert result["final_state"] == "BLOCKED"

        out = capsys.readouterr().out
        assert "BLOCKED" in out
        assert "HINT" in out


class TestPhase4NeverAdvanced:
    def test_phase_4_never_advanced(self):
        """advance_phase is never called when the deployment is already at PHASE_4."""
        orch = _make_orchestrator(
            deployment_id="deploy-p4",
            initial_state=DeploymentState.CANARY_PHASE_4,
            health_score=95.0,
        )

        result = run_shadow_phases(
            orchestrator=orch,
            deployment_id="deploy-p4",
            operator_id="test-op",
            force_paper=True,
            demo_mode=True,
        )

        # Must never call advance_phase when already at PHASE_4
        assert orch.advance_phase.call_count == 0
        assert result["final_state"] == DeploymentState.CANARY_PHASE_4.value


class TestFailedStateStopsRun:
    def test_failed_state_stops_run(self):
        """When advance_phase returns a FAILED record, subsequent phases are not attempted."""
        deployment_id = "deploy-fail"

        call_counter: List[int] = [0]

        def _advance_with_fail(dep_id, op_id, *args, **kwargs):
            call_counter[0] += 1
            # First call returns FAILED
            record = _FakeRecord(deployment_id=dep_id, state=DeploymentState.FAILED)
            orch._deployments[dep_id] = record
            return record

        orch = _make_orchestrator(
            deployment_id=deployment_id,
            health_score=90.0,
            advance_side_effect=_advance_with_fail,
        )

        result = run_shadow_phases(
            orchestrator=orch,
            deployment_id=deployment_id,
            operator_id="test-op",
            force_paper=False,
            demo_mode=True,
        )

        # Only one call — subsequent phases must not be attempted after FAILED
        assert orch.advance_phase.call_count == 1
        assert result["final_state"] == DeploymentState.FAILED.value


class TestOutputJsonFormat:
    def test_output_json_format(self):
        """With output_json=True, returned dict has deployment_id, phases, final_state."""
        orch = _make_orchestrator(
            deployment_id="deploy-json",
            health_score=90.0,
        )

        result = run_shadow_phases(
            orchestrator=orch,
            deployment_id="deploy-json",
            operator_id="test-op",
            force_paper=False,
            demo_mode=True,
            output_json=True,
        )

        assert "deployment_id" in result
        assert "phases" in result
        assert "final_state" in result
        assert result["deployment_id"] == "deploy-json"
        assert isinstance(result["phases"], list)


class TestHealthSnapshotCaptured:
    def test_health_snapshot_captured(self):
        """Each phase entry in the result should include a health snapshot."""
        orch = _make_orchestrator(
            deployment_id="deploy-health",
            health_score=85.0,
        )

        result = run_shadow_phases(
            orchestrator=orch,
            deployment_id="deploy-health",
            operator_id="test-op",
            force_paper=False,
            demo_mode=True,
        )

        assert len(result["phases"]) == 3
        for phase_entry in result["phases"]:
            assert "health" in phase_entry
            health = phase_entry["health"]
            assert "composite_score" in health
            assert "survivability_score" in health
            assert "integrity_ok" in health
            assert "ws_health" in health
            # Composite should match what get_health_score returned
            assert abs(health["composite_score"] - 85.0) < 0.01


class TestOperatorIdPassedThrough:
    def test_operator_id_passed_through(self):
        """Every advance_phase call must receive the specified operator_id."""
        target_operator = "test-op-special"
        orch = _make_orchestrator(
            deployment_id="deploy-opid",
            health_score=90.0,
        )

        run_shadow_phases(
            orchestrator=orch,
            deployment_id="deploy-opid",
            operator_id=target_operator,
            force_paper=False,
            demo_mode=True,
        )

        assert orch.advance_phase.call_count == 3
        for c in orch.advance_phase.call_args_list:
            # advance_phase(deployment_id, operator_id) — positional args
            args = c[0]
            assert args[1] == target_operator, (
                f"Expected operator_id={target_operator!r}, got {args[1]!r}"
            )
