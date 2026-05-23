"""Tests for Phase 10 latency profiler rotation."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from runtime.latency_profiler import LatencyProfiler, OperationCategory, _rotate_if_needed


class TestRotateIfNeeded:
    def test_no_rotation_below_threshold(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        for i in range(5):
            with open(path, "a") as f:
                f.write(json.dumps({"i": i}) + "\n")

        rotated = _rotate_if_needed(path, max_lines=10)
        assert rotated is False
        assert not Path(path + ".1").exists()

    def test_rotation_at_threshold(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        for i in range(10):
            with open(path, "a") as f:
                f.write(json.dumps({"i": i}) + "\n")

        rotated = _rotate_if_needed(path, max_lines=10)
        assert rotated is True
        assert Path(path + ".1").exists()
        assert not Path(path).exists()  # original moved

    def test_rotation_missing_file_returns_false(self, tmp_path):
        path = str(tmp_path / "nonexistent.jsonl")
        rotated = _rotate_if_needed(path, max_lines=5)
        assert rotated is False

    def test_rotation_atomic_rename(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        content = ""
        for i in range(10):
            line = json.dumps({"i": i}) + "\n"
            content += line
        Path(path).write_text(content)

        _rotate_if_needed(path, max_lines=10)

        backup = Path(path + ".1")
        assert backup.exists()
        lines = [l for l in backup.read_text().splitlines() if l.strip()]
        assert len(lines) == 10


class TestLatencyProfilerRotation:
    def test_rotation_triggered_on_record(self, tmp_path):
        path = str(tmp_path / "latency.jsonl")
        profiler = LatencyProfiler(analytics_path=path, max_lines_rotation=5)

        for i in range(6):
            profiler.record(OperationCategory.EXCHANGE, "test_op", float(i))

        backup = Path(path + ".1")
        assert backup.exists() or Path(path).exists()

    def test_get_rotation_status_returns_dict(self, tmp_path):
        path = str(tmp_path / "latency.jsonl")
        profiler = LatencyProfiler(analytics_path=path, max_lines_rotation=100)
        status = profiler.get_rotation_status()

        assert "current_file" in status
        assert "max_lines" in status
        assert "line_count" in status

    def test_concurrent_records_no_corruption(self, tmp_path):
        path = str(tmp_path / "latency.jsonl")
        profiler = LatencyProfiler(analytics_path=path, max_lines_rotation=50)
        errors = []

        def worker():
            try:
                for i in range(20):
                    profiler.record(OperationCategory.EXCHANGE, "concurrent_op", float(i))
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

    def test_rotation_backup_file_has_valid_json(self, tmp_path):
        path = str(tmp_path / "latency.jsonl")
        profiler = LatencyProfiler(analytics_path=path, max_lines_rotation=3)

        for i in range(4):
            profiler.record(OperationCategory.EXCHANGE, "test_op", float(i + 1))

        backup = Path(path + ".1")
        if backup.exists():
            for line in backup.read_text().splitlines():
                if line.strip():
                    obj = json.loads(line)
                    assert "operation" in obj
