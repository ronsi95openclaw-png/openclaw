"""Phase 6 chaos tests — automated rollback triggers in RollbackManager."""
from __future__ import annotations

import pytest

# ── Guard imports ─────────────────────────────────────────────────────────────

try:
    from runtime.rollback_manager import RollbackManager, RollbackRecord
    _RM_AVAILABLE = True
except ImportError as _exc:
    _RM_AVAILABLE = False
    _RM_ERR = str(_exc)


def _require_rm():
    if not _RM_AVAILABLE:
        pytest.skip(f"rollback_manager unavailable: {_RM_ERR}")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def manager(tmp_path):
    """Fresh RollbackManager with a tmp audit path."""
    _require_rm()
    audit = tmp_path / "rollback_audit.jsonl"
    return RollbackManager(audit_path=str(audit))


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_survivability_rollback_fires(manager):
    """trigger_survivability_rollback(score=30, threshold=40) must return a record."""
    result = manager.trigger_survivability_rollback(
        score=30.0, threshold=40.0, cooldown_s=0.0
    )
    assert result is not None, "Expected a RollbackRecord, got None"
    assert isinstance(result, RollbackRecord)
    assert result.rollback_id, "rollback_id must not be empty"


def test_survivability_rollback_skips_healthy(manager):
    """trigger_survivability_rollback(score=85, threshold=40) must return None."""
    result = manager.trigger_survivability_rollback(
        score=85.0, threshold=40.0, cooldown_s=0.0
    )
    assert result is None, (
        f"Expected None for healthy survivability score, got {result}"
    )


def test_cooldown_prevents_double_trigger(manager):
    """Triggering twice rapidly, second call must return None due to cooldown."""
    # First trigger (cooldown=0 for the first, then a large cooldown for the second)
    first = manager.trigger_survivability_rollback(
        score=20.0, threshold=40.0, cooldown_s=0.0
    )
    assert first is not None, "First trigger should fire"

    # Second trigger immediately — use a long cooldown so it blocks
    second = manager.trigger_survivability_rollback(
        score=20.0, threshold=40.0, cooldown_s=9999.0
    )
    assert second is None, (
        "Second trigger should be blocked by cooldown"
    )


def test_latency_rollback_fires(manager):
    """trigger_latency_rollback(p99_ms=3000, threshold_ms=2000) must return a record."""
    result = manager.trigger_latency_rollback(
        p99_ms=3000.0, threshold_ms=2000.0, cooldown_s=0.0
    )
    assert result is not None, "Expected a RollbackRecord, got None"
    assert isinstance(result, RollbackRecord)


def test_escalation_ladder_ordered(manager):
    """get_rollback_escalation_ladder() must have RECONCILIATION_INSTABILITY first."""
    ladder = manager.get_rollback_escalation_ladder()
    assert isinstance(ladder, list), "Escalation ladder must be a list"
    assert len(ladder) >= 4, "Ladder must have at least 4 entries"
    first = ladder[0]
    assert first["trigger"] == "RECONCILIATION_INSTABILITY", (
        f"Expected RECONCILIATION_INSTABILITY first, got {first['trigger']}"
    )
    # MANUAL should be last (no cooldown)
    last = ladder[-1]
    assert last["trigger"] == "MANUAL"
    assert last["cooldown_s"] == 0


def test_automation_status_tracks_triggers(manager):
    """After triggering, get_automation_status() must show total_automated_rollbacks > 0."""
    # Fire at least one automated trigger
    manager.trigger_latency_rollback(
        p99_ms=5000.0, threshold_ms=2000.0, cooldown_s=0.0
    )
    manager.trigger_survivability_rollback(
        score=10.0, threshold=40.0, cooldown_s=0.0
    )

    status = manager.get_automation_status()
    assert isinstance(status, dict), "get_automation_status() must return a dict"
    assert "total_automated_rollbacks" in status
    assert status["total_automated_rollbacks"] >= 2, (
        f"Expected >= 2 automated rollbacks, got {status['total_automated_rollbacks']}"
    )
    assert "cooldown_remaining_s" in status
    assert "last_trigger" in status
