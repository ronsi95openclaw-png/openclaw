"""Phase 7 unit tests for runtime.replay_verifier.

8 tests — all complete in < 30s total.
All imports are guarded with try/except + pytest.skip for graceful degradation.

Tests exercise:
  1. run_verification() completes without crash on empty store
  2. Empty store → both replay paths agree → equivalent=True
  3. Manually divergent state → divergence_detected in at least one field
  4. checksum_tree has "raw" key
  5. Consecutive runs → different report_ids
  6. Audit file is created after run_verification()
  7. replay_duration_ms is non-negative
  8. _emit_divergence_event() does not crash
"""
from __future__ import annotations

import pytest

# ── Guard imports ─────────────────────────────────────────────────────────────

try:
    from runtime.replay_verifier import (
        ReplayVerifier,
        ReplayCheckField,
        ReplayDivergence,
        ReplayEquivalenceReport,
        get_verifier,
    )
    _AVAILABLE = True
except ImportError as _exc:
    _AVAILABLE = False
    _IMPORT_ERR = str(_exc)


def _require():
    if not _AVAILABLE:
        pytest.skip(f"replay_verifier unavailable: {_IMPORT_ERR}")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def verifier(tmp_path):
    """Fresh ReplayVerifier with isolated tmp audit path."""
    _require()
    return ReplayVerifier(
        verification_window          = 50,
        tolerance_pct                = 0.01,
        trigger_rollback_on_mismatch = False,
        audit_path                   = str(tmp_path / "replay_audit_test.jsonl"),
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_run_verification_no_crash(verifier):
    """run_verification() on an empty store must return a non-None report."""
    report = verifier.run_verification()
    assert report is not None, "run_verification() must return a ReplayEquivalenceReport"
    assert isinstance(report, ReplayEquivalenceReport)


def test_equivalent_on_empty_store(verifier):
    """With an empty store both replay paths return the same (empty) state.

    equivalent must be True because there is no divergence to detect.
    """
    report = verifier.run_verification()
    assert report.equivalent is True, (
        f"Empty store must produce equivalent=True; divergences={report.divergences}"
    )


def test_divergence_detected(tmp_path):
    """Manually create a divergent state and verify divergence_detected=True.

    We subclass ReplayVerifier and override the raw/snapshot paths to return
    different capital_state values to force a divergence.
    """
    _require()

    class _DivergentVerifier(ReplayVerifier):
        def _replay_raw(self) -> dict:
            return {
                "capital_state":        "SAFE",
                "open_positions_count": 0,
                "realized_pnl":         100.0,
                "event_count":          5,
            }

        def _replay_snapshot_tail(self) -> dict:
            return {
                "capital_state":        "HALT",   # deliberately different
                "open_positions_count": 0,
                "realized_pnl":         200.0,    # also different (> tolerance)
                "event_count":          3,
            }

        def _read_live_state(self) -> dict:
            return {}

    verifier = _DivergentVerifier(
        tolerance_pct = 0.01,
        audit_path    = str(tmp_path / "divergent_audit.jsonl"),
    )
    report = verifier.run_verification()

    divergent_fields = [d for d in report.divergences if d.divergence_detected]
    assert len(divergent_fields) >= 1, (
        "Expected at least one divergent field; got 0.  "
        f"All divergences: {report.divergences}"
    )


def test_checksum_tree_present(verifier):
    """report.checksum_tree must contain the 'raw' key."""
    report = verifier.run_verification()
    assert "raw" in report.checksum_tree, (
        f"checksum_tree must have 'raw' key; got keys={list(report.checksum_tree.keys())}"
    )


def test_report_id_unique(verifier):
    """Two consecutive run_verification() calls must produce different report_ids."""
    report1 = verifier.run_verification()
    report2 = verifier.run_verification()
    assert report1.report_id != report2.report_id, (
        "Each report must have a unique UUID report_id"
    )


def test_audit_file_created(tmp_path):
    """run_verification() must create the audit JSONL file."""
    _require()
    audit_path = tmp_path / "audit_created.jsonl"
    verifier   = ReplayVerifier(
        verification_window = 10,
        audit_path          = str(audit_path),
    )
    verifier.run_verification()
    assert audit_path.exists(), (
        f"Audit JSONL file must be created at {audit_path}"
    )
    content = audit_path.read_text().strip()
    assert content, "Audit file must not be empty"


def test_replay_duration_recorded(verifier):
    """report.replay_duration_ms must be a non-negative float."""
    report = verifier.run_verification()
    assert isinstance(report.replay_duration_ms, float), (
        "replay_duration_ms must be a float"
    )
    assert report.replay_duration_ms >= 0.0, (
        f"replay_duration_ms must be >= 0; got {report.replay_duration_ms}"
    )


def test_emit_divergence_event_does_not_crash(verifier):
    """Calling _emit_divergence_event() directly must not raise any exception."""
    _require()
    try:
        verifier._emit_divergence_event()
    except Exception as exc:
        pytest.fail(
            f"_emit_divergence_event() must not raise; got {type(exc).__name__}: {exc}"
        )
