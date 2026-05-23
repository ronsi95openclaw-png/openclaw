"""Phase 7 tests for runtime.live_balance_guardian.BalanceGuardian.

All tests use tmp_path so they never touch real project data directories.
All tests complete in < 15 s total.
"""
from __future__ import annotations

import json
import os
import time

import pytest

# ---------------------------------------------------------------------------
# Guard import — skip entire module if the guardian cannot be imported
# ---------------------------------------------------------------------------
try:
    from runtime.live_balance_guardian import (
        BalanceGuardian,
        BalanceGuardianConfig,
        BalanceSeverity,
        BalanceCheckResult,
        get_guardian,
    )
    _IMPORT_OK = True
except Exception as _exc:
    _IMPORT_OK = False
    _IMPORT_EXC = _exc

if not _IMPORT_OK:
    pytest.skip(
        f"runtime.live_balance_guardian not importable: {_IMPORT_EXC}",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_guardian(tmp_path, *, demo_mode: bool = True) -> BalanceGuardian:
    """Create a fresh BalanceGuardian whose files land in tmp_path."""
    cfg = BalanceGuardianConfig(
        demo_mode=demo_mode,
        audit_path=str(tmp_path / "balance_audit.jsonl"),
        cache_path=str(tmp_path / "balance_guardian_cache.json"),
        stale_threshold_s=300.0,
    )
    return BalanceGuardian(cfg)


# ---------------------------------------------------------------------------
# Test 1 — no data at all → INFO
# ---------------------------------------------------------------------------

def test_no_data_returns_info(tmp_path):
    """run_check() with no exchange_balance and no capital data → INFO severity."""
    guardian = _make_guardian(tmp_path)

    # Patch out the capital/replay reads so they return None
    guardian._read_capital_equity = lambda: None
    guardian._read_replay_equity = lambda: None

    result = guardian.run_check()  # no exchange_balance argument

    assert isinstance(result, BalanceCheckResult)
    assert result.severity == BalanceSeverity.INFO
    assert result.exchange_balance is None
    assert result.capital_engine_equity is None
    assert result.divergence_abs == 0.0
    assert result.divergence_pct == 0.0
    assert not result.stale_data
    assert not result.negative_collateral


# ---------------------------------------------------------------------------
# Test 2 — healthy balance → INFO
# ---------------------------------------------------------------------------

def test_healthy_balance_info(tmp_path):
    """exchange_balance ≈ capital_engine_equity (< 2% divergence) → INFO."""
    guardian = _make_guardian(tmp_path)
    guardian._read_capital_equity = lambda: 10000.0
    guardian._read_replay_equity = lambda: None  # no replay data available

    result = guardian.run_check(exchange_balance=10050.0)  # 0.5% divergence

    assert result.severity == BalanceSeverity.INFO
    assert result.exchange_balance == pytest.approx(10050.0)
    assert result.capital_engine_equity == pytest.approx(10000.0)
    assert result.divergence_pct == pytest.approx(0.5, abs=1e-6)
    assert not result.stale_data
    assert not result.negative_collateral
    assert not result.replay_mismatch


# ---------------------------------------------------------------------------
# Test 3 — 3% divergence → WARNING
# ---------------------------------------------------------------------------

def test_small_divergence_warning(tmp_path):
    """3% divergence between exchange and engine → WARNING severity."""
    guardian = _make_guardian(tmp_path)
    guardian._read_capital_equity = lambda: 10000.0
    guardian._read_replay_equity = lambda: None  # no replay data available

    result = guardian.run_check(exchange_balance=10300.0)  # 3% divergence

    assert result.severity == BalanceSeverity.WARNING
    assert result.divergence_pct == pytest.approx(3.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Test 4 — 6% divergence → CRITICAL (not HALT because demo_mode=True cap)
# ---------------------------------------------------------------------------

def test_large_divergence_critical(tmp_path):
    """6% divergence with demo_mode=True → CRITICAL (raw HALT not enforced)."""
    guardian = _make_guardian(tmp_path, demo_mode=True)
    guardian._read_capital_equity = lambda: 10000.0
    guardian._read_replay_equity = lambda: None  # no replay data available

    result = guardian.run_check(exchange_balance=10600.0)  # 6% divergence

    # 6% > divergence_critical_pct (5%) → raw would be CRITICAL; HALT threshold is 10%
    assert result.severity == BalanceSeverity.CRITICAL
    assert result.advisory_mode is True


# ---------------------------------------------------------------------------
# Test 5 — demo_mode never enforces HALT
# ---------------------------------------------------------------------------

def test_demo_mode_never_halts(tmp_path):
    """50% divergence with demo_mode=True → advisory_mode=True, no halt marker written."""
    guardian = _make_guardian(tmp_path, demo_mode=True)
    guardian._read_capital_equity = lambda: 10000.0
    guardian._read_replay_equity = lambda: None  # no replay data available

    result = guardian.run_check(exchange_balance=15000.0)  # 50% divergence

    # demo mode downgrades HALT to CRITICAL
    assert result.advisory_mode is True
    assert result.severity != BalanceSeverity.HALT

    # Halt marker must NOT exist
    from runtime.live_balance_guardian import _BALANCE_HALT_MARKER
    assert not os.path.exists(_BALANCE_HALT_MARKER), (
        "Halt marker must never be written in demo/advisory mode"
    )


# ---------------------------------------------------------------------------
# Test 6 — negative collateral → CRITICAL+ (demo_mode=True keeps it CRITICAL)
# ---------------------------------------------------------------------------

def test_negative_collateral_critical(tmp_path):
    """exchange_balance=-100 → negative_collateral=True, severity >= CRITICAL."""
    guardian = _make_guardian(tmp_path, demo_mode=True)
    guardian._read_capital_equity = lambda: 10000.0
    guardian._read_replay_equity = lambda: None  # no replay data available

    result = guardian.run_check(exchange_balance=-100.0)

    assert result.negative_collateral is True
    assert result.severity >= BalanceSeverity.CRITICAL


# ---------------------------------------------------------------------------
# Test 7 — stale detection
# ---------------------------------------------------------------------------

def test_stale_detection(tmp_path):
    """Setting last_exchange_ts to 600 s ago → stale_data=True in next check."""
    guardian = _make_guardian(tmp_path)
    guardian._read_capital_equity = lambda: 10000.0
    guardian._read_replay_equity = lambda: None  # no replay data available

    # Simulate a first check 600 s ago
    with guardian._lock:
        guardian._last_exchange_ts = time.monotonic() - 600.0

    result = guardian.run_check()  # no new exchange_balance, so ts stays old

    assert result.stale_data is True


# ---------------------------------------------------------------------------
# Test 8 — EWMA increases monotonically with growing divergence
# ---------------------------------------------------------------------------

def test_ewma_updates_monotonically(tmp_path):
    """Five checks with increasing divergence → ewma_divergence increases."""
    guardian = _make_guardian(tmp_path)
    guardian._read_capital_equity = lambda: 10000.0
    guardian._read_replay_equity = lambda: None  # no replay data available

    ewma_values = []
    base_divergence_amounts = [10050.0, 10100.0, 10150.0, 10200.0, 10250.0]

    for balance in base_divergence_amounts:
        result = guardian.run_check(exchange_balance=balance)
        ewma_values.append(result.ewma_divergence)

    # Each subsequent EWMA should be >= the previous one (divergence always grows)
    for i in range(1, len(ewma_values)):
        assert ewma_values[i] >= ewma_values[i - 1], (
            f"EWMA not monotonic at index {i}: "
            f"{ewma_values[i-1]:.6f} → {ewma_values[i]:.6f}"
        )
    # Final EWMA must be > 0
    assert ewma_values[-1] > 0.0


# ---------------------------------------------------------------------------
# Test 9 — audit file is created and contains valid JSON
# ---------------------------------------------------------------------------

def test_audit_file_created(tmp_path):
    """run_check() creates audit JSONL; each line is valid JSON with required keys."""
    audit_path = tmp_path / "balance_audit.jsonl"
    cfg = BalanceGuardianConfig(
        demo_mode=True,
        audit_path=str(audit_path),
        cache_path=str(tmp_path / "cache.json"),
    )
    guardian = BalanceGuardian(cfg)
    guardian._read_capital_equity = lambda: 10000.0
    guardian._read_replay_equity = lambda: None  # no replay data available

    guardian.run_check(exchange_balance=10000.0)
    guardian.run_check(exchange_balance=10100.0)

    assert audit_path.exists(), "Audit JSONL file must be created after run_check()"

    lines = [l for l in audit_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 2, f"Expected 2 audit lines, got {len(lines)}"

    required_keys = {
        "check_id", "checked_at", "severity", "divergence_pct",
        "ewma_divergence", "stale_data", "negative_collateral",
        "advisory_mode", "detail",
    }
    for idx, line in enumerate(lines):
        record = json.loads(line)
        missing = required_keys - set(record.keys())
        assert not missing, f"Audit line {idx} missing keys: {missing}"


# ---------------------------------------------------------------------------
# Test 10 — last_known_good is populated after an INFO check
# ---------------------------------------------------------------------------

def test_last_known_good_cached(tmp_path):
    """run_check() with INFO result → last_known_good is populated with correct values."""
    guardian = _make_guardian(tmp_path)
    guardian._read_capital_equity = lambda: 10000.0
    guardian._read_replay_equity = lambda: None  # no replay data available

    assert guardian.get_last_known_good() is None, (
        "last_known_good should be None before any check"
    )

    result = guardian.run_check(exchange_balance=10000.0)
    assert result.severity == BalanceSeverity.INFO

    lkg = guardian.get_last_known_good()
    assert lkg is not None, "last_known_good must be set after a successful INFO check"
    assert "check_id" in lkg
    assert lkg["exchange_balance"] == pytest.approx(10000.0)
    assert lkg["capital_engine_equity"] == pytest.approx(10000.0)

    # Verify cache file was written
    cache_path = tmp_path / "balance_guardian_cache.json"
    assert cache_path.exists(), "Cache file must be written after updating last_known_good"
    cached = json.loads(cache_path.read_text())
    assert cached["exchange_balance"] == pytest.approx(10000.0)
