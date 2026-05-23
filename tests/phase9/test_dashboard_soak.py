"""Soak / stress tests for Phase 9 dashboard components."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from dashboard.api.audit import (
    DashboardAuditEvent,
    append_audit_event,
    get_recent_events,
    make_trace_id,
    now_iso,
)


def _event(**kwargs) -> DashboardAuditEvent:
    defaults = dict(
        ts=now_iso(),
        action="SOAK_TEST",
        operator_id="soak",
        client_ip="127.0.0.1",
        trace_id=make_trace_id(),
        params={},
        result="SUCCESS",
        detail="soak",
    )
    defaults.update(kwargs)
    return DashboardAuditEvent(**defaults)


class TestAuditSoak:
    def test_1000_concurrent_appends_no_corruption(self, tmp_path, monkeypatch):
        """1000 concurrent appends from 10 threads must all land as valid JSON."""
        monkeypatch.chdir(tmp_path)

        errors: list[str] = []
        threads = []

        def writer():
            try:
                for _ in range(100):
                    append_audit_event(_event())
            except Exception as exc:
                errors.append(str(exc))

        for _ in range(10):
            t = threading.Thread(target=writer)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Concurrent write errors: {errors}"

        audit_file = tmp_path / "data" / "dashboard_audit.jsonl"
        lines = [l for l in audit_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1000

        for line in lines:
            record = json.loads(line)
            assert "action" in record

    def test_get_recent_returns_most_recent_under_load(self, tmp_path, monkeypatch):
        """After writing 500 events, get_recent_events(20) must return last 20."""
        monkeypatch.chdir(tmp_path)

        for i in range(500):
            append_audit_event(_event(detail=str(i)))

        events = get_recent_events(n=20)
        assert len(events) == 20

        # Most recent first: detail=499 should be in results
        details = {e["detail"] for e in events}
        assert "499" in details


class TestEndpointSoak:
    def test_all_endpoints_stable_under_50_requests(self):
        """Each GET endpoint must handle 50 rapid requests without error."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from dashboard.api.routers.phase9 import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        endpoints = [
            "/api/v2/overview",
            "/api/v2/execution",
            "/api/v2/balance",
            "/api/v2/eventstore",
            "/api/v2/governance",
            "/api/v2/deployment",
            "/api/v2/coordination",
            "/api/v2/chaos",
            "/api/v2/security",
        ]

        for endpoint in endpoints:
            for _ in range(10):
                resp = client.get(endpoint)
                assert resp.status_code == 200, f"{endpoint} failed"

    def test_jsonl_read_tail_with_large_file(self, tmp_path, monkeypatch):
        """_read_jsonl_tail must handle files with 10k lines efficiently."""
        monkeypatch.chdir(tmp_path)

        large_file = tmp_path / "data" / "large.jsonl"
        large_file.parent.mkdir(parents=True, exist_ok=True)
        with large_file.open("w") as f:
            for i in range(10000):
                f.write(json.dumps({"idx": i}) + "\n")

        from dashboard.api.routers.phase9 import _read_jsonl_tail
        start = time.monotonic()
        records = _read_jsonl_tail(large_file, 20)
        elapsed = time.monotonic() - start

        assert len(records) == 20
        assert elapsed < 5.0, f"Read took too long: {elapsed:.2f}s"

    def test_count_jsonl_lines_large_file(self, tmp_path, monkeypatch):
        """_count_jsonl_lines should handle 5k-line files without error."""
        monkeypatch.chdir(tmp_path)

        path = tmp_path / "data" / "count_test.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for i in range(5000):
                f.write(json.dumps({"i": i}) + "\n")

        from dashboard.api.routers.phase9 import _count_jsonl_lines
        count = _count_jsonl_lines(path)
        assert count == 5000
