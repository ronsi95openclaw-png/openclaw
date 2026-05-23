"""Telemetry poller — publishes typed telemetry events to EventBus.

Runs as an asyncio task alongside the existing _poll_bot_state loop.
Polls each subsystem on its own schedule:
  - balance_guardian:   every 10s
  - latency_stats:      every 5s
  - survivability:      every 15s
  - chaos_snapshot:     every 30s
  - eventstore_seq:     every 5s

Event types added to EventBus:
  "telemetry_balance"        — balance guardian status
  "telemetry_latency"        — latency profiler stats
  "telemetry_survivability"  — survivability score
  "telemetry_chaos"          — chaos incident count
  "telemetry_eventstore"     — latest seq + throughput

Design rules:
- Each subsystem poll in a separate try/except; one failing poll never stops others.
- Never raises out of run_telemetry_loop().
- All module imports are lazy (inside functions), wrapped in try/except.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI
    from dashboard.api.event_bus import EventBus

logger = logging.getLogger("openclaw.dashboard.telemetry")

# ── Per-subsystem poll intervals (seconds) ─────────────────────────────────────

_INTERVAL_BALANCE       = 10.0
_INTERVAL_LATENCY       = 5.0
_INTERVAL_SURVIVABILITY = 15.0
_INTERVAL_CHAOS         = 30.0
_INTERVAL_EVENTSTORE    = 5.0


# ── Individual poll helpers ────────────────────────────────────────────────────


def _poll_balance() -> dict:
    """Return balance guardian status dict or {"status": "unavailable"}."""
    try:
        from runtime.live_balance_guardian import get_guardian  # type: ignore[import]
        status = get_guardian().get_status()
        return {"status": "ok", **status}
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        logger.debug("telemetry: balance poll error: %s", exc)
        return {"status": "unavailable", "error": str(exc)}


def _poll_latency() -> dict:
    """Return latency profiler stats list or {"status": "unavailable"}."""
    try:
        from runtime.latency_profiler import get_profiler  # type: ignore[import]
        profiler = get_profiler()
        all_stats = profiler.get_all_stats()
        degradation = profiler.get_exchange_degradation_score()
        return {
            "status": "ok",
            "exchange_degradation_score": degradation,
            "operation_count": len(all_stats),
            "stats": [
                {
                    "category":   s.category.value,
                    "operation":  s.operation,
                    "p50_ms":     s.p50_ms,
                    "p95_ms":     s.p95_ms,
                    "p99_ms":     s.p99_ms,
                    "ewma_ms":    s.ewma_ms,
                    "sample_count": s.sample_count,
                    "anomaly_detected": s.anomaly_detected,
                }
                for s in all_stats[:20]  # cap at 20 to avoid oversized payloads
            ],
        }
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        logger.debug("telemetry: latency poll error: %s", exc)
        return {"status": "unavailable", "error": str(exc)}


def _poll_survivability() -> dict:
    """Return survivability score or {"status": "unavailable"}."""
    try:
        from runtime.survivability import get_survivability_engine  # type: ignore[import]
        report = get_survivability_engine().compute_score()
        return {
            "status": "ok",
            "current_score":    report.current_score,
            "classification":   report.classification.value,
            "deployment_ready": report.deployment_ready,
        }
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        logger.debug("telemetry: survivability poll error: %s", exc)
        return {"status": "unavailable", "error": str(exc)}


def _poll_chaos() -> dict:
    """Return chaos runtime incident count or {"status": "unavailable"}."""
    try:
        from runtime.chaos_runtime import get_chaos_runtime  # type: ignore[import]
        runtime = get_chaos_runtime()
        report = runtime.get_incident_report()
        return {
            "status":          "ok",
            "total_events":    report.get("total_events", 0),
            "degraded_count":  report.get("degraded_count", 0),
            "recovered_count": report.get("recovered_count", 0),
        }
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        logger.debug("telemetry: chaos poll error: %s", exc)
        return {"status": "unavailable", "error": str(exc)}


def _poll_eventstore() -> dict:
    """Return EventStore latest seq or {"status": "unavailable"}."""
    try:
        from runtime.event_store import EventStore  # type: ignore[import]
        store = EventStore()
        seq = store.get_latest_seq()
        ok, _ = store.verify_integrity()
        return {
            "status":      "ok",
            "latest_seq":  seq,
            "checksum_ok": ok,
        }
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        logger.debug("telemetry: eventstore poll error: %s", exc)
        return {"status": "unavailable", "error": str(exc)}


# ── Main telemetry loop ────────────────────────────────────────────────────────


async def run_telemetry_loop() -> None:
    """Run forever, publishing telemetry events to the EventBus. Never crashes."""
    from dashboard.api.event_bus import get_bus  # local import — server may not be ready

    # Track last poll times per subsystem
    last: dict = {
        "balance":       0.0,
        "latency":       0.0,
        "survivability": 0.0,
        "chaos":         0.0,
        "eventstore":    0.0,
    }

    logger.info("Telemetry loop started")

    while True:
        try:
            now = time.monotonic()
            bus = get_bus()

            if now - last["balance"] >= _INTERVAL_BALANCE:
                try:
                    data = _poll_balance()
                    bus.publish("telemetry_balance", data)
                except Exception as exc:
                    logger.debug("telemetry: balance publish error: %s", exc)
                last["balance"] = now

            if now - last["latency"] >= _INTERVAL_LATENCY:
                try:
                    data = _poll_latency()
                    bus.publish("telemetry_latency", data)
                except Exception as exc:
                    logger.debug("telemetry: latency publish error: %s", exc)
                last["latency"] = now

            if now - last["survivability"] >= _INTERVAL_SURVIVABILITY:
                try:
                    data = _poll_survivability()
                    bus.publish("telemetry_survivability", data)
                except Exception as exc:
                    logger.debug("telemetry: survivability publish error: %s", exc)
                last["survivability"] = now

            if now - last["chaos"] >= _INTERVAL_CHAOS:
                try:
                    data = _poll_chaos()
                    bus.publish("telemetry_chaos", data)
                except Exception as exc:
                    logger.debug("telemetry: chaos publish error: %s", exc)
                last["chaos"] = now

            if now - last["eventstore"] >= _INTERVAL_EVENTSTORE:
                try:
                    data = _poll_eventstore()
                    bus.publish("telemetry_eventstore", data)
                except Exception as exc:
                    logger.debug("telemetry: eventstore publish error: %s", exc)
                last["eventstore"] = now

        except Exception as exc:
            logger.error("telemetry loop top-level error: %s", exc)

        await asyncio.sleep(2.0)


def init_telemetry(app: "FastAPI", bus: "EventBus") -> None:
    """Called once during startup to register the telemetry task."""
    # The task is scheduled from the server startup() function using
    # asyncio.create_task(run_telemetry_loop()) directly.
    # This function is a no-op hook for explicit DI / test wiring.
    pass
