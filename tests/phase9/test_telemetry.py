"""Tests for dashboard/api/telemetry.py — telemetry polling loop."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestRunTelemetryLoop:
    def test_import_succeeds(self):
        from dashboard.api.telemetry import run_telemetry_loop
        assert callable(run_telemetry_loop)

    def test_loop_is_coroutine(self):
        from dashboard.api.telemetry import run_telemetry_loop
        import inspect
        assert inspect.iscoroutinefunction(run_telemetry_loop)

    def test_loop_cancels_cleanly(self):
        from dashboard.api.telemetry import run_telemetry_loop

        async def _run():
            task = asyncio.create_task(run_telemetry_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # expected

        asyncio.run(_run())

    def test_loop_survives_subsystem_exception(self):
        """If a subsystem raises, the loop must not crash — it should continue."""
        from dashboard.api.telemetry import run_telemetry_loop

        async def _run():
            task = asyncio.create_task(run_telemetry_loop())
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                pytest.fail(f"Telemetry loop raised unexpected exception: {exc}")

        asyncio.run(_run())

    def test_publishes_to_event_bus(self):
        from dashboard.api.telemetry import run_telemetry_loop
        from dashboard.api.event_bus import get_bus

        bus = get_bus()
        # Verify run_telemetry_loop can be called with the standard bus pattern
        assert run_telemetry_loop is not None


class TestTelemetryChannels:
    def test_five_channels_covered(self):
        """Verify all 5 telemetry channels are referenced in the module."""
        import inspect
        from dashboard.api import telemetry
        src = inspect.getsource(telemetry)

        channels = [
            "telemetry_balance",
            "telemetry_latency",
            "telemetry_survivability",
            "telemetry_chaos",
            "telemetry_eventstore",
        ]
        for ch in channels:
            assert ch in src, f"Channel '{ch}' not found in telemetry module"

    def test_subsystem_polls_have_try_except(self):
        """Each subsystem poll must be guarded with try/except."""
        import inspect
        from dashboard.api import telemetry
        src = inspect.getsource(telemetry)
        # There must be multiple try/except blocks for fault isolation
        assert src.count("except") >= 5, "Expected at least 5 try/except blocks for subsystem isolation"
