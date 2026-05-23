"""Tests for runtime/weight_scheduler.py — midnight weight application daemon."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from runtime.weight_scheduler import WeightApplicationDaemon, WeightAdjustmentRecord


def _make_daemon(tmp_path, **kwargs) -> WeightApplicationDaemon:
    defaults = dict(
        weights_path=str(tmp_path / "data" / "strategy_weights.json"),
        analysis_dir=str(tmp_path / "data" / "optimization"),
        audit_path=str(tmp_path / "data" / "weight_adjustments_audit.jsonl"),
        snapshots_dir=str(tmp_path / "data" / "weight_snapshots"),
        demo_mode=True,
        dry_run=False,
    )
    defaults.update(kwargs)
    return WeightApplicationDaemon(**defaults)


def _write_weights(tmp_path, weights: dict) -> None:
    p = tmp_path / "data" / "strategy_weights.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(weights, indent=2))


def _write_analysis(tmp_path, adjustments: dict, filename: str = "analysis_20260523.json") -> None:
    p = tmp_path / "data" / "optimization" / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"weight_adjustments": adjustments}))


class TestApplyNow:
    def test_applies_adjustments_to_weights(self, tmp_path):
        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 1.0, "trades": 10}})
        _write_analysis(tmp_path, {"EMA_CROSS": 1.2})

        daemon = _make_daemon(tmp_path)
        record = daemon.apply_now()

        assert record is not None
        assert "EMA_CROSS" in record.applied
        assert abs(record.applied["EMA_CROSS"]["new"] - 1.2) < 0.01

        weights = json.loads((tmp_path / "data" / "strategy_weights.json").read_text())
        assert abs(weights["EMA_CROSS"]["weight"] - 1.2) < 0.01

    def test_weight_clamped_to_max(self, tmp_path):
        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 1.9, "trades": 5}})
        _write_analysis(tmp_path, {"EMA_CROSS": 2.0})  # 1.9 * 2.0 = 3.8 → clamped to 2.0

        daemon = _make_daemon(tmp_path)
        record = daemon.apply_now()

        assert record.applied["EMA_CROSS"]["new"] == 2.0

    def test_weight_clamped_to_min(self, tmp_path):
        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 0.2, "trades": 5}})
        _write_analysis(tmp_path, {"EMA_CROSS": 0.3})  # 0.2 * 0.3 = 0.06 → clamped to 0.1

        daemon = _make_daemon(tmp_path)
        record = daemon.apply_now()

        assert record.applied["EMA_CROSS"]["new"] == 0.1

    def test_no_analysis_file_returns_none(self, tmp_path):
        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 1.0, "trades": 5}})
        daemon = _make_daemon(tmp_path)
        result = daemon.apply_now()
        assert result is None

    def test_idempotent_skips_same_file(self, tmp_path):
        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 1.0, "trades": 5}})
        _write_analysis(tmp_path, {"EMA_CROSS": 1.1})

        daemon = _make_daemon(tmp_path)
        rec1 = daemon.apply_now()
        rec2 = daemon.apply_now()  # same file → skipped

        assert rec1 is not None
        assert rec2 is None

    def test_force_reapplies_same_file(self, tmp_path):
        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 1.0, "trades": 5}})
        _write_analysis(tmp_path, {"EMA_CROSS": 1.1})

        daemon = _make_daemon(tmp_path)
        daemon.apply_now()
        rec2 = daemon.apply_now(force=True)  # force=True → re-applies

        assert rec2 is not None

    def test_dry_run_does_not_write_weights(self, tmp_path):
        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 1.0, "trades": 5}})
        _write_analysis(tmp_path, {"EMA_CROSS": 1.5})

        daemon = _make_daemon(tmp_path, dry_run=True)
        record = daemon.apply_now()

        assert record is not None
        assert record.dry_run is True

        # Original weights unchanged
        weights = json.loads((tmp_path / "data" / "strategy_weights.json").read_text())
        assert weights["EMA_CROSS"]["weight"] == 1.0

    def test_unknown_strategy_goes_to_skipped(self, tmp_path):
        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 1.0, "trades": 5}})
        _write_analysis(tmp_path, {"UNKNOWN_STRATEGY": 1.5})

        daemon = _make_daemon(tmp_path)
        record = daemon.apply_now()

        assert record is not None
        assert "UNKNOWN_STRATEGY" in record.skipped

    def test_malformed_factor_goes_to_rejected(self, tmp_path):
        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 1.0, "trades": 5}})
        _write_analysis(tmp_path, {"EMA_CROSS": "not_a_number"})

        daemon = _make_daemon(tmp_path)
        record = daemon.apply_now()

        assert record is not None
        assert "EMA_CROSS" in record.rejected

    def test_malformed_analysis_json_returns_none(self, tmp_path):
        (tmp_path / "data" / "optimization").mkdir(parents=True, exist_ok=True)
        p = tmp_path / "data" / "optimization" / "analysis_bad.json"
        p.write_text("not json }{")

        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 1.0}})
        daemon = _make_daemon(tmp_path)
        result = daemon.apply_now()

        assert result is None


class TestAuditLog:
    def test_audit_record_written_on_apply(self, tmp_path):
        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 1.0, "trades": 5}})
        _write_analysis(tmp_path, {"EMA_CROSS": 1.1})

        daemon = _make_daemon(tmp_path)
        daemon.apply_now()

        audit_path = tmp_path / "data" / "weight_adjustments_audit.jsonl"
        assert audit_path.exists()
        records = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
        assert len(records) == 1
        rec = records[0]
        assert rec["demo_mode"] is True
        assert "EMA_CROSS" in rec["applied"]

    def test_audit_has_checksum_before_and_after(self, tmp_path):
        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 1.0, "trades": 5}})
        _write_analysis(tmp_path, {"EMA_CROSS": 1.2})

        daemon = _make_daemon(tmp_path)
        record = daemon.apply_now()

        assert len(record.checksum_before) == 64  # sha256 hex
        assert len(record.checksum_after) == 64
        assert record.checksum_before != record.checksum_after

    def test_snapshot_created(self, tmp_path):
        _write_weights(tmp_path, {"EMA_CROSS": {"weight": 1.0, "trades": 5}})
        _write_analysis(tmp_path, {"EMA_CROSS": 1.2})

        daemon = _make_daemon(tmp_path)
        record = daemon.apply_now()

        assert record.snapshot_path != ""
        assert Path(record.snapshot_path).exists()


class TestLifecycle:
    def test_start_stop_clean(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        daemon.start()
        assert daemon.is_running()
        daemon.stop(timeout_s=3.0)
        assert not daemon.is_running()

    def test_double_start_noop(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        daemon.start()
        daemon.start()  # should not create second thread
        assert daemon.is_running()
        daemon.stop(timeout_s=3.0)

    def test_get_status_returns_dict(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        status = daemon.get_status()
        assert "running" in status
        assert "demo_mode" in status
        assert "min_weight" in status
        assert "max_weight" in status
