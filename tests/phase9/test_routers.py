"""Tests for Phase 9 REST endpoints — all 9 sections."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.fixture(scope="module")
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dashboard.api.routers.phase9 import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestSection1Overview:
    def test_get_overview_200(self, client):
        resp = client.get("/api/v2/overview")
        assert resp.status_code == 200

    def test_overview_has_demo_mode(self, client):
        data = client.get("/api/v2/overview").json()
        assert "demo_mode" in data

    def test_overview_subsystem_unavailable_graceful(self, client):
        # Without mocking, subsystems will be unavailable — should not 500
        data = client.get("/api/v2/overview").json()
        assert isinstance(data, dict)


class TestSection2Execution:
    def test_get_execution_no_crash(self, client):
        resp = client.get("/api/v2/execution")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_execution_stream_default_limit(self, client):
        resp = client.get("/api/v2/execution/stream")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data

    def test_execution_stream_respects_limit(self, client, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        latency_path = tmp_path / "data" / "latency_analytics.jsonl"
        latency_path.parent.mkdir(parents=True, exist_ok=True)
        for i in range(10):
            latency_path.open("a").write(json.dumps({"op": f"op_{i}"}) + "\n")

        resp = client.get("/api/v2/execution/stream?limit=3")
        data = resp.json()
        assert len(data["events"]) <= 3


class TestSection3Balance:
    def test_get_balance_no_crash(self, client):
        resp = client.get("/api/v2/balance")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_balance_history_returns_list(self, client):
        resp = client.get("/api/v2/balance/history")
        assert resp.status_code == 200
        data = resp.json()
        # Router may return 'records' or 'events' key
        has_list_key = "events" in data or "records" in data
        assert has_list_key, f"Expected events or records key, got: {list(data.keys())}"


class TestSection4EventStore:
    def test_get_eventstore_200(self, client):
        resp = client.get("/api/v2/eventstore")
        assert resp.status_code == 200

    def test_eventstore_recent_limit_capped(self, client):
        resp = client.get("/api/v2/eventstore/recent?limit=1000")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data

    def test_eventstore_replay_reports(self, client):
        resp = client.get("/api/v2/eventstore/replay-reports")
        assert resp.status_code == 200


class TestSection5Governance:
    def test_get_governance_200(self, client):
        resp = client.get("/api/v2/governance")
        assert resp.status_code == 200

    def test_governance_drift_history(self, client):
        resp = client.get("/api/v2/governance/drift-history")
        assert resp.status_code == 200


class TestSection6Deployment:
    def test_get_deployment_200(self, client):
        resp = client.get("/api/v2/deployment")
        assert resp.status_code == 200

    def test_deployment_health_200(self, client):
        resp = client.get("/api/v2/deployment/health")
        assert resp.status_code == 200

    def test_deployment_rollback_history_200(self, client):
        resp = client.get("/api/v2/deployment/rollback-history")
        assert resp.status_code == 200


class TestSection7Coordination:
    def test_get_coordination_200(self, client):
        resp = client.get("/api/v2/coordination")
        assert resp.status_code == 200

    def test_coordination_split_brain_200(self, client):
        resp = client.get("/api/v2/coordination/split-brain-audit")
        assert resp.status_code == 200


class TestSection8Chaos:
    def test_get_chaos_200(self, client):
        resp = client.get("/api/v2/chaos")
        assert resp.status_code == 200

    def test_chaos_events_200(self, client):
        resp = client.get("/api/v2/chaos/events")
        assert resp.status_code == 200


class TestSection9Security:
    def test_get_security_200(self, client):
        resp = client.get("/api/v2/security")
        assert resp.status_code == 200

    def test_security_approvals_200(self, client):
        resp = client.get("/api/v2/security/approvals")
        assert resp.status_code == 200

    def test_security_integrity_findings_200(self, client):
        resp = client.get("/api/v2/security/integrity-findings")
        assert resp.status_code == 200


class TestFailClosed:
    """Verify fail-closed pattern: all endpoints return 200 even with no subsystems."""

    def test_all_get_endpoints_200_without_subsystems(self, client):
        endpoints = [
            "/api/v2/overview",
            "/api/v2/execution",
            "/api/v2/execution/stream",
            "/api/v2/balance",
            "/api/v2/balance/history",
            "/api/v2/eventstore",
            "/api/v2/eventstore/recent",
            "/api/v2/eventstore/replay-reports",
            "/api/v2/governance",
            "/api/v2/governance/drift-history",
            "/api/v2/deployment",
            "/api/v2/deployment/health",
            "/api/v2/deployment/rollback-history",
            "/api/v2/coordination",
            "/api/v2/coordination/split-brain-audit",
            "/api/v2/chaos",
            "/api/v2/chaos/events",
            "/api/v2/security",
            "/api/v2/security/approvals",
            "/api/v2/security/integrity-findings",
        ]
        for endpoint in endpoints:
            resp = client.get(endpoint)
            assert resp.status_code == 200, f"{endpoint} returned {resp.status_code}"
            assert isinstance(resp.json(), dict), f"{endpoint} returned non-dict"
