"""Tests for Phase 9 privileged command endpoints: advance-phase and chaos inject."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestAdvancePhaseGuard:
    """Verify the advance-phase endpoint refuses CANARY_PHASE_4 → STABLE."""

    def _make_orch_with_state(self, state_value: str) -> MagicMock:
        """Build a mock orchestrator whose deployment has the given state."""
        from deployment.orchestrator.orchestrator import DeploymentState
        state = DeploymentState(state_value)
        record = SimpleNamespace(
            state=state,
            deployment_id="dep-001",
            canary_phase=4 if state == DeploymentState.CANARY_PHASE_4 else 3,
            health_score=80.0,
        )
        orch = MagicMock()
        orch._lock = __import__("threading").Lock()
        orch._deployments = {"dep-001": record}
        advanced_record = SimpleNamespace(
            deployment_id="dep-001",
            state=DeploymentState.STABLE if state != DeploymentState.CANARY_PHASE_4 else state,
            canary_phase=4,
            health_score=90.0,
        )
        orch.advance_phase.return_value = advanced_record
        return orch

    def test_phase4_returns_403(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from dashboard.api.routers.phase9 import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        orch = self._make_orch_with_state("CANARY_PHASE_4")

        with patch("deployment.orchestrator.orchestrator.get_orchestrator", return_value=orch):
            resp = client.post(
                "/api/v2/deployment/advance-phase",
                json={"deployment_id": "dep-001", "operator_id": "test-op"},
                headers={"X-Forwarded-For": "127.0.0.1"},
            )

        assert resp.status_code == 403
        assert "Ed25519" in resp.json().get("detail", "")

    def test_phase4_advance_never_called(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from dashboard.api.routers.phase9 import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        orch = self._make_orch_with_state("CANARY_PHASE_4")

        with patch("deployment.orchestrator.orchestrator.get_orchestrator", return_value=orch):
            client.post(
                "/api/v2/deployment/advance-phase",
                json={"deployment_id": "dep-001", "operator_id": "test-op"},
            )

        orch.advance_phase.assert_not_called()

    def test_phase3_can_advance(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from dashboard.api.routers.phase9 import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        orch = self._make_orch_with_state("CANARY_PHASE_3")

        with patch("deployment.orchestrator.orchestrator.get_orchestrator", return_value=orch):
            resp = client.post(
                "/api/v2/deployment/advance-phase",
                json={"deployment_id": "dep-001", "operator_id": "test-op"},
            )

        assert resp.status_code == 200
        orch.advance_phase.assert_called_once_with(
            deployment_id="dep-001",
            operator_id="test-op",
        )

    def test_missing_operator_id_returns_422(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from dashboard.api.routers.phase9 import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.post(
            "/api/v2/deployment/advance-phase",
            json={"deployment_id": "dep-001"},
        )
        assert resp.status_code == 422


class TestChaosInjectValidation:
    """Verify chaos inject validates event_type and enforces DEMO_MODE."""

    def _app(self):
        from fastapi import FastAPI
        from dashboard.api.routers.phase9 import router
        app = FastAPI()
        app.include_router(router)
        return app

    def test_unknown_event_type_returns_400(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._app())
        resp = client.post(
            "/api/v2/chaos/inject",
            json={"event_type": "NONEXISTENT_CHAOS_TYPE"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "detail" in body

    def test_valid_event_type_accepted_in_demo_mode(self, monkeypatch):
        from fastapi.testclient import TestClient
        from runtime.chaos_runtime import ChaosEventType, ChaosRuntimeConfig

        monkeypatch.setenv("DEMO_MODE", "true")

        mock_event = SimpleNamespace(
            event_id="evt-001",
            event_type=ChaosEventType.LATENCY_SPIKE,
            outcome="injected",
            duration_ms=12.5,
            subsystem_impact={"latency": True},
        )
        mock_runtime = MagicMock()
        mock_runtime.run_event.return_value = mock_event

        with patch("runtime.chaos_runtime.get_chaos_runtime", return_value=mock_runtime):
            client = TestClient(self._app())
            resp = client.post(
                "/api/v2/chaos/inject",
                json={"event_type": "LATENCY_SPIKE", "seed": 42},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["event_type"] == "LATENCY_SPIKE"

    def test_destructive_events_blocked_in_live_mode(self, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setenv("DEMO_MODE", "false")

        client = TestClient(self._app())
        resp = client.post(
            "/api/v2/chaos/inject",
            json={"event_type": "BALANCE_CORRUPTION_SIMULATION"},
        )
        # Import guard: check if chaos_runtime available
        assert resp.status_code in (400, 403, 503)

    def test_missing_event_type_returns_422(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._app())
        resp = client.post("/api/v2/chaos/inject", json={})
        assert resp.status_code == 422


class TestValidateTelegramEndpoint:
    """Test /api/v2/security/validate-telegram."""

    def test_returns_configured_false_when_no_token(self, monkeypatch):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from dashboard.api.routers.phase9 import router

        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.post("/api/v2/security/validate-telegram")
        assert resp.status_code == 200
        data = resp.json()
        assert "configured" in data
        assert data["configured"] is False


class TestOverviewEndpoint:
    """Test /api/v2/overview returns valid structure."""

    def test_overview_returns_expected_keys(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from dashboard.api.routers.phase9 import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/v2/overview")
        assert resp.status_code == 200
        data = resp.json()

        required_keys = [
            "demo_mode",
            "uptime_s",
            "survivability_score",
            "integrity_ok",
        ]
        for k in required_keys:
            assert k in data, f"Missing key: {k}"

    def test_overview_uptime_positive(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from dashboard.api.routers.phase9 import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/v2/overview")
        data = resp.json()
        assert data["uptime_s"] >= 0

    def test_overview_demo_mode_reflects_env(self, monkeypatch):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from dashboard.api.routers.phase9 import router

        monkeypatch.setenv("DEMO_MODE", "true")

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/v2/overview")
        data = resp.json()
        assert data["demo_mode"] is True
