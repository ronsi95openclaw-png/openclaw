"""Tests for Phase 10 extensions to scripts/generate_backtest_baseline.py."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.generate_backtest_baseline import (
    validate_record,
    import_from_jsonl,
    compute_import_checksum,
)


class TestValidateRecord:
    def test_valid_record_passes(self):
        rec = {
            "pnl": 10.5,
            "strategy": "EMA_CROSS",
            "outcome": "win",
            "ts": "2026-05-23T12:00:00+00:00",
        }
        ok, err = validate_record(rec, 0)
        assert ok is True
        assert err == ""

    def test_missing_pnl_fails(self):
        rec = {"strategy": "EMA_CROSS", "outcome": "win", "ts": "2026-05-23T00:00:00+00:00"}
        ok, err = validate_record(rec, 0)
        assert ok is False
        assert "pnl" in err.lower()

    def test_missing_strategy_fails(self):
        rec = {"pnl": 5.0, "outcome": "win", "ts": "2026-05-23T00:00:00+00:00"}
        ok, err = validate_record(rec, 0)
        assert ok is False

    def test_missing_outcome_fails(self):
        rec = {"pnl": 5.0, "strategy": "EMA_CROSS", "ts": "2026-05-23T00:00:00+00:00"}
        ok, err = validate_record(rec, 0)
        assert ok is False

    def test_missing_ts_fails(self):
        rec = {"pnl": 5.0, "strategy": "EMA_CROSS", "outcome": "win"}
        ok, err = validate_record(rec, 0)
        assert ok is False

    def test_non_numeric_pnl_fails(self):
        rec = {"pnl": "not_a_number", "strategy": "EMA_CROSS", "outcome": "win", "ts": "2026-05-23T00:00:00+00:00"}
        ok, err = validate_record(rec, 0)
        assert ok is False


class TestImportFromJsonl:
    def _write_jsonl(self, path: Path, records: list) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def test_valid_records_imported(self, tmp_path):
        import random
        rng = random.Random(42)
        records = [
            {"pnl": 10.0, "strategy": "EMA_CROSS", "outcome": "win", "ts": "2026-05-23T01:00:00+00:00"},
            {"pnl": -5.0, "strategy": "BREAKOUT", "outcome": "loss", "ts": "2026-05-23T02:00:00+00:00"},
        ]
        path = tmp_path / "trades.jsonl"
        self._write_jsonl(path, records)

        valid, errors = import_from_jsonl(str(path), rng)
        assert len(valid) == 2
        assert len(errors) == 0

    def test_malformed_line_goes_to_errors(self, tmp_path):
        import random
        rng = random.Random(42)
        path = tmp_path / "trades.jsonl"
        path.write_text('{"pnl": 10.0, "strategy": "EMA_CROSS", "outcome": "win", "ts": "2026-05-23T01:00:00+00:00"}\nnot json garbage\n')

        valid, errors = import_from_jsonl(str(path), rng)
        assert len(valid) == 1
        assert len(errors) == 1

    def test_invalid_record_goes_to_errors(self, tmp_path):
        import random
        rng = random.Random(42)
        records = [
            {"strategy": "EMA_CROSS", "outcome": "win", "ts": "2026-05-23T01:00:00+00:00"},  # missing pnl
        ]
        path = tmp_path / "trades.jsonl"
        self._write_jsonl(path, records)

        valid, errors = import_from_jsonl(str(path), rng)
        assert len(valid) == 0
        assert len(errors) == 1

    def test_sorted_by_ts(self, tmp_path):
        import random
        rng = random.Random(42)
        records = [
            {"pnl": 5.0, "strategy": "EMA_CROSS", "outcome": "win", "ts": "2026-05-23T03:00:00+00:00"},
            {"pnl": 3.0, "strategy": "BREAKOUT", "outcome": "win", "ts": "2026-05-23T01:00:00+00:00"},
            {"pnl": 4.0, "strategy": "BOLLINGER_BAND", "outcome": "win", "ts": "2026-05-23T02:00:00+00:00"},
        ]
        path = tmp_path / "trades.jsonl"
        self._write_jsonl(path, records)

        valid, _ = import_from_jsonl(str(path), rng)
        assert valid[0]["ts"] < valid[1]["ts"] < valid[2]["ts"]

    def test_duplicate_ids_deduplicated(self, tmp_path):
        import random
        rng = random.Random(42)
        records = [
            {"id": "trade-001", "pnl": 10.0, "strategy": "EMA_CROSS", "outcome": "win", "ts": "2026-05-23T01:00:00+00:00"},
            {"id": "trade-001", "pnl": 10.0, "strategy": "EMA_CROSS", "outcome": "win", "ts": "2026-05-23T01:00:00+00:00"},  # dup
        ]
        path = tmp_path / "trades.jsonl"
        self._write_jsonl(path, records)

        valid, _ = import_from_jsonl(str(path), rng)
        assert len(valid) == 1

    def test_real_import_labeled_correctly(self, tmp_path):
        import random
        rng = random.Random(42)
        records = [
            {"pnl": 10.0, "strategy": "EMA_CROSS", "outcome": "win", "ts": "2026-05-23T01:00:00+00:00"},
        ]
        path = tmp_path / "trades.jsonl"
        self._write_jsonl(path, records)

        valid, _ = import_from_jsonl(str(path), rng)
        assert valid[0].get("synthetic") is False
        assert valid[0].get("source") == "real_import"


class TestChecksum:
    def test_checksum_deterministic(self):
        records = [
            {"pnl": 10.0, "ts": "2026-05-23T01:00:00+00:00"},
            {"pnl": -5.0, "ts": "2026-05-23T02:00:00+00:00"},
        ]
        c1 = compute_import_checksum(records)
        c2 = compute_import_checksum(records)
        assert c1 == c2
        assert len(c1) == 64  # sha256 hex

    def test_checksum_changes_with_different_records(self):
        records_a = [{"pnl": 10.0, "ts": "2026-05-23T01:00:00+00:00"}]
        records_b = [{"pnl": 20.0, "ts": "2026-05-23T01:00:00+00:00"}]
        assert compute_import_checksum(records_a) != compute_import_checksum(records_b)

    def test_checksum_empty_records(self):
        c = compute_import_checksum([])
        assert isinstance(c, str)
        assert len(c) == 64
