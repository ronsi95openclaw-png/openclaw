"""Tests for dashboard/api/audit.py — DashboardAuditEvent and audit helpers."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from dashboard.api.audit import (
    DashboardAuditEvent,
    append_audit_event,
    get_recent_events,
    make_trace_id,
    now_iso,
)


def _make_event(**kwargs) -> DashboardAuditEvent:
    defaults = dict(
        ts=now_iso(),
        action="TEST_ACTION",
        operator_id="test_operator",
        client_ip="127.0.0.1",
        trace_id=make_trace_id(),
        params={},
        result="SUCCESS",
        detail="unit test",
    )
    defaults.update(kwargs)
    return DashboardAuditEvent(**defaults)


class TestDashboardAuditEvent:
    def test_dataclass_fields(self):
        event = _make_event()
        assert event.action == "TEST_ACTION"
        assert event.operator_id == "test_operator"
        assert event.result == "SUCCESS"

    def test_make_trace_id_is_uuid_format(self):
        tid = make_trace_id()
        assert len(tid) == 36
        assert tid.count("-") == 4

    def test_now_iso_contains_timezone(self):
        ts = now_iso()
        assert "+" in ts or "Z" in ts or ts.endswith("+00:00")


class TestAppendAuditEvent:
    def test_appends_jsonl_record(self, tmp_path, monkeypatch):
        audit_file = tmp_path / "data" / "dashboard_audit.jsonl"
        monkeypatch.chdir(tmp_path)

        event = _make_event(action="ADVANCE_PHASE", result="SUCCESS")
        append_audit_event(event)

        assert audit_file.exists()
        records = [json.loads(line) for line in audit_file.read_text().splitlines() if line.strip()]
        assert len(records) == 1
        assert records[0]["action"] == "ADVANCE_PHASE"
        assert records[0]["result"] == "SUCCESS"

    def test_appends_multiple_records(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        for i in range(5):
            append_audit_event(_make_event(action=f"ACTION_{i}"))

        audit_file = tmp_path / "data" / "dashboard_audit.jsonl"
        lines = [l for l in audit_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 5

    def test_never_raises_on_invalid_path(self, monkeypatch):
        import dashboard.api.audit as audit_mod
        import unittest.mock as mock
        # Patch the internal path so writes fail silently
        with mock.patch.object(audit_mod, "_AUDIT_PATH", Path("/nonexistent_xyz/audit.jsonl")):
            event = _make_event()
            append_audit_event(event)  # must not raise

    def test_concurrent_writes_do_not_corrupt(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        errors = []
        threads = []

        def write_events():
            try:
                for _ in range(10):
                    append_audit_event(_make_event())
            except Exception as exc:
                errors.append(str(exc))

        for _ in range(5):
            t = threading.Thread(target=write_events)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent write errors: {errors}"

        audit_file = tmp_path / "data" / "dashboard_audit.jsonl"
        lines = [l for l in audit_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 50, f"Expected 50 records, got {len(lines)}"

        # All lines must be valid JSON
        for line in lines:
            record = json.loads(line)
            assert "action" in record

    def test_params_dict_serialized(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        event = _make_event(params={"deployment_id": "abc-123", "reason": "test"})
        append_audit_event(event)

        audit_file = tmp_path / "data" / "dashboard_audit.jsonl"
        record = json.loads(audit_file.read_text().strip())
        assert record["params"]["deployment_id"] == "abc-123"


class TestGetRecentEvents:
    def test_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = get_recent_events()
        assert result == []

    def test_returns_last_n_events(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        for i in range(10):
            append_audit_event(_make_event(action=f"ACT_{i}"))

        events = get_recent_events(n=3)
        assert len(events) == 3

    def test_returns_most_recent_first(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        for i in range(5):
            append_audit_event(_make_event(action=f"ACT_{i}", detail=str(i)))

        events = get_recent_events(n=5)
        # reversed order — most recent first
        assert events[0]["detail"] == "4"
        assert events[-1]["detail"] == "0"

    def test_skips_malformed_lines(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        audit_file = tmp_path / "data" / "dashboard_audit.jsonl"
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        audit_file.write_text('{"action": "GOOD"}\nnot_json_garbage\n{"action": "ALSO_GOOD"}\n')

        events = get_recent_events(n=10)
        assert len(events) == 2
        actions = {e["action"] for e in events}
        assert actions == {"GOOD", "ALSO_GOOD"}

    def test_returns_empty_on_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = get_recent_events(n=5)
        assert result == []
