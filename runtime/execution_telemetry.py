"""Execution Telemetry Collector for OpenClaw.

Aggregates latency, fill quality, and exchange health into a unified telemetry
stream.  Integrates with LatencyProfiler and SurvivabilityEngine.

Module singleton: get_telemetry() -> ExecutionTelemetryCollector
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("openclaw.runtime.execution_telemetry")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_event_id() -> str:
    return str(uuid.uuid4())


def _append_jsonl_locked(path: str, record: dict) -> None:
    """Append one JSON line to a JSONL file under fcntl.LOCK_EX."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(json.dumps(record) + "\n")
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except Exception as exc:
        logger.debug("execution_telemetry: flush write skipped: %s", exc)


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class TelemetryEvent:
    event_id:    str
    category:    str
    metric_name: str
    value:       float
    unit:        str   # "ms", "bps", "fraction", "score", "count"
    timestamp:   str
    tags:        Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionTelemetry:
    snapshot_at:               str
    ws_latency_p99_ms:         float
    rest_latency_p99_ms:       float
    order_ack_latency_p99_ms:  float
    fill_latency_p99_ms:       float
    avg_slippage_bps:          float
    avg_fill_rate:             float
    exchange_degradation_score: float
    execution_timing_drift:    float
    survivability_score:       float
    telemetry_health:          str   # "HEALTHY" | "DEGRADED" | "CRITICAL"


# ── ExecutionTelemetryCollector ───────────────────────────────────────────────


class ExecutionTelemetryCollector:
    """Aggregates execution telemetry from all subsystems.

    Uses LatencyProfiler for latency tracking and SurvivabilityEngine for
    survivability scoring.  All methods are thread-safe.  Fail-closed on all
    external module imports.
    """

    _MAX_FILL_BUFFER = 1000

    def __init__(
        self,
        analytics_path: str = "data/execution_telemetry.jsonl",
        flush_interval_s: float = 30.0,
    ) -> None:
        self._analytics_path  = analytics_path
        self._flush_interval  = flush_interval_s
        self._lock            = threading.Lock()

        # Rolling buffers: (slippage_bps, fill_rate)
        self._fill_buffer: Deque[Tuple[float, float]] = deque(
            maxlen=self._MAX_FILL_BUFFER
        )

    # ── LatencyProfiler accessor (lazy import, fail-closed) ───────────────────

    def _profiler(self):
        try:
            from runtime.latency_profiler import get_profiler, OperationCategory
            return get_profiler(), OperationCategory
        except Exception as exc:
            logger.debug("execution_telemetry: cannot import latency_profiler: %s", exc)
            return None, None

    # ── Record methods ────────────────────────────────────────────────────────

    def record_ws_message(self, latency_ms: float) -> None:
        """Record a WebSocket message round-trip latency."""
        profiler, OperationCategory = self._profiler()
        if profiler is not None:
            try:
                profiler.record(OperationCategory.WEBSOCKET, "ws_message", latency_ms)
            except Exception as exc:
                logger.debug("record_ws_message error: %s", exc)

    def record_rest_call(self, endpoint: str, latency_ms: float) -> None:
        """Record a REST API call latency for a named endpoint."""
        profiler, OperationCategory = self._profiler()
        if profiler is not None:
            try:
                profiler.record(OperationCategory.REST_API, endpoint, latency_ms)
            except Exception as exc:
                logger.debug("record_rest_call error: %s", exc)

    def record_order_ack(self, latency_ms: float) -> None:
        """Record order acknowledgement latency."""
        profiler, OperationCategory = self._profiler()
        if profiler is not None:
            try:
                profiler.record(
                    OperationCategory.ORDER_ACKNOWLEDGEMENT, "order_ack", latency_ms
                )
            except Exception as exc:
                logger.debug("record_order_ack error: %s", exc)

    def record_fill(
        self, latency_ms: float, slippage_bps: float, fill_rate: float
    ) -> None:
        """Record fill latency, slippage, and fill rate."""
        profiler, OperationCategory = self._profiler()
        if profiler is not None:
            try:
                profiler.record(
                    OperationCategory.FILL_CONFIRMATION, "fill_confirm", latency_ms
                )
            except Exception as exc:
                logger.debug("record_fill latency error: %s", exc)
        with self._lock:
            self._fill_buffer.append((slippage_bps, fill_rate))

    def record_reconciliation(self, latency_ms: float) -> None:
        """Record reconciliation cycle latency."""
        profiler, OperationCategory = self._profiler()
        if profiler is not None:
            try:
                profiler.record(
                    OperationCategory.RECONCILIATION, "reconcile", latency_ms
                )
            except Exception as exc:
                logger.debug("record_reconciliation error: %s", exc)

    def record_snapshot(self, latency_ms: float) -> None:
        """Record snapshot write latency."""
        profiler, OperationCategory = self._profiler()
        if profiler is not None:
            try:
                profiler.record(OperationCategory.SNAPSHOT, "snapshot_write", latency_ms)
            except Exception as exc:
                logger.debug("record_snapshot error: %s", exc)

    def record_event_persistence(self, latency_ms: float) -> None:
        """Record event persistence (EventStore append) latency."""
        profiler, OperationCategory = self._profiler()
        if profiler is not None:
            try:
                profiler.record(
                    OperationCategory.EVENT_PERSISTENCE, "event_append", latency_ms
                )
            except Exception as exc:
                logger.debug("record_event_persistence error: %s", exc)

    def record_lock_acquisition(self, resource: str, latency_ms: float) -> None:
        """Record lock acquisition latency for a named resource."""
        profiler, OperationCategory = self._profiler()
        if profiler is not None:
            try:
                profiler.record(OperationCategory.LOCK_ACQUISITION, resource, latency_ms)
            except Exception as exc:
                logger.debug("record_lock_acquisition error: %s", exc)

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def get_telemetry_snapshot(self) -> ExecutionTelemetry:
        """Aggregate all telemetry into a single ExecutionTelemetry snapshot."""
        profiler, OperationCategory = self._profiler()

        def _p99(cat_val: str, op: str) -> float:
            if profiler is None or OperationCategory is None:
                return 0.0
            try:
                cat   = OperationCategory(cat_val)
                stats = profiler.get_stats(cat, op)
                return stats.p99_ms if stats else 0.0
            except Exception:
                return 0.0

        ws_p99       = _p99("WEBSOCKET", "ws_message")
        rest_p99     = _p99("REST_API", "fetch_ticker")
        # Use max across all REST endpoints for a broader read
        if profiler is not None and OperationCategory is not None:
            try:
                all_stats = profiler.get_all_stats()
                rest_candidates = [
                    s.p99_ms
                    for s in all_stats
                    if s.category == OperationCategory.REST_API
                ]
                if rest_candidates:
                    rest_p99 = max(rest_candidates)
            except Exception:
                pass

        ack_p99      = _p99("ORDER_ACKNOWLEDGEMENT", "order_ack")
        fill_p99     = _p99("FILL_CONFIRMATION", "fill_confirm")

        # Fill quality
        with self._lock:
            fills = list(self._fill_buffer)
        if fills:
            avg_slip      = sum(f[0] for f in fills) / len(fills)
            avg_fill_rate = sum(f[1] for f in fills) / len(fills)
        else:
            avg_slip      = 0.0
            avg_fill_rate = 1.0

        # Exchange degradation + drift
        degradation = 0.0
        drift       = 1.0
        if profiler is not None and OperationCategory is not None:
            try:
                degradation = profiler.get_exchange_degradation_score()
            except Exception:
                pass
            try:
                drift = profiler.detect_timing_drift(
                    OperationCategory.WEBSOCKET, "ws_message"
                )
            except Exception:
                pass

        # Survivability score
        survivability = 50.0
        try:
            from runtime.survivability import get_survivability_engine
            engine = get_survivability_engine()
            report = engine.compute_score()
            survivability = report.current_score
        except Exception as exc:
            logger.debug("execution_telemetry: survivability read skipped: %s", exc)

        # Health classification
        if degradation < 0.3 and survivability >= 70.0:
            health = "HEALTHY"
        elif degradation < 0.7 and survivability >= 40.0:
            health = "DEGRADED"
        else:
            health = "CRITICAL"

        return ExecutionTelemetry(
            snapshot_at=_now_iso(),
            ws_latency_p99_ms=ws_p99,
            rest_latency_p99_ms=rest_p99,
            order_ack_latency_p99_ms=ack_p99,
            fill_latency_p99_ms=fill_p99,
            avg_slippage_bps=avg_slip,
            avg_fill_rate=avg_fill_rate,
            exchange_degradation_score=degradation,
            execution_timing_drift=drift,
            survivability_score=survivability,
            telemetry_health=health,
        )

    def flush_to_store(self) -> None:
        """Append current ExecutionTelemetry snapshot to analytics JSONL."""
        snapshot = self.get_telemetry_snapshot()
        record   = asdict(snapshot)
        _append_jsonl_locked(self._analytics_path, record)

    def get_rollback_triggers(self) -> List[str]:
        """Return list of condition strings that should initiate a rollback."""
        snapshot = self.get_telemetry_snapshot()
        triggers: List[str] = []
        if snapshot.ws_latency_p99_ms > 1000.0:
            triggers.append("LATENCY_EXPLOSION")
        if snapshot.exchange_degradation_score > 0.8:
            triggers.append("EXCHANGE_DEGRADED")
        if snapshot.avg_fill_rate < 0.5:
            triggers.append("FILL_RATE_COLLAPSED")
        if snapshot.survivability_score < 40.0:
            triggers.append("SURVIVABILITY_CRITICAL")
        return triggers


# ── Module singleton ──────────────────────────────────────────────────────────

_telemetry: Optional[ExecutionTelemetryCollector] = None
_telemetry_lock = threading.Lock()


def get_telemetry() -> ExecutionTelemetryCollector:
    """Return the module-level ExecutionTelemetryCollector singleton."""
    global _telemetry
    if _telemetry is None:
        with _telemetry_lock:
            if _telemetry is None:
                _telemetry = ExecutionTelemetryCollector()
    return _telemetry
