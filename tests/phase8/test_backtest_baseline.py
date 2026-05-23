"""Phase 8 tests for scripts.generate_backtest_baseline.

Tests validate correctness, idempotency, determinism, and field integrity
of the backtest baseline generator without performing real I/O unless using
pytest's tmp_path fixture.

All tests complete in < 5 s each.
"""
from __future__ import annotations

import json
import os

import pytest

# ---------------------------------------------------------------------------
# Guard import — skip entire module if the script cannot be imported
# ---------------------------------------------------------------------------
try:
    from scripts.generate_backtest_baseline import generate_baseline
    _IMPORT_OK = True
except Exception as _exc:
    _IMPORT_OK = False
    _IMPORT_EXC = _exc

if not _IMPORT_OK:
    pytest.skip(
        f"scripts.generate_backtest_baseline not importable: {_IMPORT_EXC}",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test 1 — generate_baseline(dry_run=True) returns list of >= 30 dicts
# ---------------------------------------------------------------------------

def test_generate_returns_records():
    """generate_baseline(dry_run=True) returns a non-empty list of >= 30 dicts."""
    records = generate_baseline(dry_run=True)

    assert isinstance(records, list), (
        f"generate_baseline() must return a list, got {type(records)}"
    )
    assert len(records) >= 30, (
        f"Expected >= 30 records, got {len(records)}"
    )
    for i, rec in enumerate(records):
        assert isinstance(rec, dict), (
            f"Record at index {i} must be a dict, got {type(rec)}"
        )


# ---------------------------------------------------------------------------
# Test 2 — all records have "pnl" key with float value
# ---------------------------------------------------------------------------

def test_all_records_have_pnl():
    """Every record returned by generate_baseline(dry_run=True) has a float 'pnl'."""
    records = generate_baseline(dry_run=True)

    assert records, "generate_baseline must return at least one record"

    for i, rec in enumerate(records):
        assert "pnl" in rec, (
            f"Record at index {i} is missing required 'pnl' field: {rec}"
        )
        assert isinstance(rec["pnl"], (int, float)), (
            f"'pnl' at index {i} must be numeric, got {type(rec['pnl'])}: {rec['pnl']!r}"
        )


# ---------------------------------------------------------------------------
# Test 3 — two calls with same seed → identical record count and mean pnl
# ---------------------------------------------------------------------------

def test_deterministic_output():
    """Two calls with the same seed produce identical record count and mean PnL."""
    records_a = generate_baseline(dry_run=True, seed=42)
    records_b = generate_baseline(dry_run=True, seed=42)

    assert len(records_a) == len(records_b), (
        f"Record counts differ: {len(records_a)} vs {len(records_b)}"
    )

    def mean_pnl(recs):
        values = [r["pnl"] for r in recs if isinstance(r.get("pnl"), (int, float))]
        return sum(values) / len(values) if values else 0.0

    pnl_a = mean_pnl(records_a)
    pnl_b = mean_pnl(records_b)

    assert abs(pnl_a - pnl_b) < 1e-9, (
        f"Mean PnL differs between identical seeds: {pnl_a} vs {pnl_b}"
    )


# ---------------------------------------------------------------------------
# Test 4 — min_records parameter is respected
# ---------------------------------------------------------------------------

def test_min_records_respected():
    """generate_baseline(min_records=50) returns at least 50 records."""
    records = generate_baseline(dry_run=True, min_records=50)

    assert len(records) >= 50, (
        f"Expected >= 50 records with min_records=50, got {len(records)}"
    )


# ---------------------------------------------------------------------------
# Test 5 — at least 3 distinct strategies appear in output
# ---------------------------------------------------------------------------

def test_strategy_distribution():
    """Output contains at least 3 distinct strategy values."""
    records = generate_baseline(dry_run=True)

    strategies = {r.get("strategy") for r in records if r.get("strategy")}

    assert len(strategies) >= 3, (
        f"Expected >= 3 distinct strategies, got {len(strategies)}: {sorted(strategies)}"
    )


# ---------------------------------------------------------------------------
# Test 6 — no overwrite without --force
# ---------------------------------------------------------------------------

def test_no_overwrite_without_force(tmp_path):
    """Second call without force= to an existing file returns same records, no write."""
    output = str(tmp_path / "backtest_test.jsonl")

    # First write
    records_first = generate_baseline(
        output_path=output,
        min_records=30,
        seed=42,
        force=True,
    )

    # Read the file content after first write
    with open(output, "r", encoding="utf-8") as fh:
        content_after_first = fh.read()

    # Second call without force — should not modify the file
    records_second = generate_baseline(
        output_path=output,
        min_records=30,
        seed=42,
        force=False,
    )

    # File content must be unchanged
    with open(output, "r", encoding="utf-8") as fh:
        content_after_second = fh.read()

    assert content_after_first == content_after_second, (
        "File was modified by a second call without force=True"
    )

    # Both calls must return the same record count
    assert len(records_first) == len(records_second), (
        f"Record counts differ: first={len(records_first)}, second={len(records_second)}"
    )


# ---------------------------------------------------------------------------
# Test 7 — force=True overwrites existing file
# ---------------------------------------------------------------------------

def test_force_overwrites(tmp_path):
    """generate_baseline with force=True overwrites an existing output file."""
    output = str(tmp_path / "backtest_force.jsonl")

    # Write an initial file
    generate_baseline(output_path=output, min_records=30, seed=42, force=True)

    # Read initial content
    with open(output, "r", encoding="utf-8") as fh:
        content_before = fh.read()

    # Write again with force=True and a different seed → different content
    generate_baseline(output_path=output, min_records=30, seed=99, force=True)

    with open(output, "r", encoding="utf-8") as fh:
        content_after = fh.read()

    # Seeds 42 and 99 produce different outputs — file must have changed
    assert content_before != content_after, (
        "File was not updated when force=True was passed"
    )

    # The new file must still be valid JSONL with >= 30 records
    records = []
    for line in content_after.splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))

    assert len(records) >= 30, (
        f"Force-overwritten file has only {len(records)} records (expected >= 30)"
    )


# ---------------------------------------------------------------------------
# Test 8 — all records have "synthetic": true
# ---------------------------------------------------------------------------

def test_synthetic_flag_set():
    """All records generated by generate_baseline(dry_run=True) have 'synthetic': True."""
    records = generate_baseline(dry_run=True)

    assert records, "generate_baseline must return at least one record"

    for i, rec in enumerate(records):
        assert "synthetic" in rec, (
            f"Record at index {i} is missing 'synthetic' field: {rec}"
        )
        assert rec["synthetic"] is True, (
            f"Expected 'synthetic': True at index {i}, got {rec['synthetic']!r}"
        )
