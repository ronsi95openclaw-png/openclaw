"""Tests for Phase 10 dashboard audit rotation."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dashboard.api.audit import (
    DashboardAuditEvent,
    append_audit_event,
    get_recent_events,
    get_retention_status,
    _get_file_age_days,
    _check_and_rotate,
    _archive_audit_file,
    make_trace_id,
    now_iso,
)


def _event(**kwargs) -> DashboardAuditEvent:
    defaults = dict(
        ts=now_iso(),
        action="TEST",
        operator_id="test",
        client_ip="127.0.0.1",
        trace_id=make_trace_id(),
        params={},
        result="SUCCESS",
        detail="rotation test",
    )
    defaults.update(kwargs)
    return DashboardAuditEvent(**defaults)


class TestGetFileAgeDays:
    def test_returns_zero_if_not_exists(self, tmp_path):
        p = tmp_path / "nonexistent.jsonl"
        age = _get_file_age_days(p)
        assert age == 0.0

    def test_new_file_is_young(self, tmp_path):
        p = tmp_path / "audit.jsonl"
        p.write_text('{"test": 1}\n')
        age = _get_file_age_days(p)
        assert age < 1.0  # less than 1 day old

    def test_old_file_reports_correct_age(self, tmp_path):
        import os
        p = tmp_path / "audit.jsonl"
        p.write_text('{"test": 1}\n')
        # Set mtime to 35 days ago
        past = time.time() - 35 * 86400
        os.utime(p, (past, past))
        age = _get_file_age_days(p)
        assert 34 < age < 36


class TestArchiveAuditFile:
    def test_atomic_rename_when_no_archive_exists(self, tmp_path):
        import os
        p = tmp_path / "dashboard_audit.jsonl"
        p.write_text('{"action": "TEST"}\n')

        # Set mtime to 40 days ago to get a specific archive name
        past = time.time() - 40 * 86400
        os.utime(p, (past, past))

        archive = _archive_audit_file(p)
        assert archive is not None
        assert archive.exists()
        assert not p.exists()

    def test_merge_when_archive_exists(self, tmp_path):
        import os
        p = tmp_path / "dashboard_audit.jsonl"
        p.write_text('{"action": "NEW"}\n')
        past = time.time() - 40 * 86400
        os.utime(p, (past, past))

        # Pre-create an archive
        archive_name = datetime.fromtimestamp(past, tz=timezone.utc).strftime("dashboard_audit_%Y%m.jsonl")
        archive_path = tmp_path / archive_name
        archive_path.write_text('{"action": "OLD"}\n')

        result = _archive_audit_file(p)
        assert result is not None
        assert result.exists()
        content = result.read_text()
        assert "OLD" in content
        assert "NEW" in content
        assert not p.exists()


class TestCheckAndRotate:
    def test_young_file_not_rotated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        p = tmp_path / "data" / "dashboard_audit.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"action": "TEST"}\n')

        rotated = _check_and_rotate(p, max_age_days=30)
        assert rotated is False
        assert p.exists()

    def test_old_file_rotated(self, tmp_path, monkeypatch):
        import os
        monkeypatch.chdir(tmp_path)
        p = tmp_path / "data" / "dashboard_audit.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"action": "TEST"}\n')

        past = time.time() - 35 * 86400
        os.utime(p, (past, past))

        rotated = _check_and_rotate(p, max_age_days=30)
        assert rotated is True
        assert not p.exists()


class TestGetRetentionStatus:
    def test_returns_dict_with_required_keys(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        status = get_retention_status()
        required = ["current_file", "age_days", "line_count", "max_age_days", "archive_count", "archives"]
        for k in required:
            assert k in status, f"Missing key: {k}"

    def test_no_file_age_zero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        status = get_retention_status()
        assert status["age_days"] == 0.0
        assert status["line_count"] == 0


class TestAppendAfterRotation:
    def test_append_still_works_after_rotation(self, tmp_path, monkeypatch):
        import os
        import dashboard.api.audit as audit_mod
        monkeypatch.chdir(tmp_path)

        audit_path = tmp_path / "data" / "dashboard_audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text('{"action": "OLD"}\n')

        # Force old age
        past = time.time() - 35 * 86400
        os.utime(audit_path, (past, past))

        monkeypatch.setattr(audit_mod, "_AUDIT_PATH", audit_path)
        append_audit_event(_event(action="AFTER_ROTATION"))

        # New file should exist with the new event
        assert audit_path.exists()
        records = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
        assert any(r["action"] == "AFTER_ROTATION" for r in records)
