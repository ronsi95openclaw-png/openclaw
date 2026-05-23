"""Latency Profiler for OpenClaw — p50/p95/p99 across all operations.

Captures timing samples per (category, operation) pair, computes percentile
statistics, detects anomalies, and exports Prometheus text metrics.

Module singleton: get_profiler() -> LatencyProfiler
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger("openclaw.runtime.latency_profiler")

# ── Enums ─────────────────────────────────────────────────────────────────────


class OperationCategory(str, Enum):
    WEBSOCKET            = "WEBSOCKET"
    REST_API             = "REST_API"
    EXCHANGE             = "EXCHANGE"
    ORDER_ACKNOWLEDGEMENT = "ORDER_ACKNOWLEDGEMENT"
    FILL_CONFIRMATION    = "FILL_CONFIRMATION"
    RECONCILIATION       = "RECONCILIATION"
    SNAPSHOT             = "SNAPSHOT"
    EVENT_PERSISTENCE    = "EVENT_PERSISTENCE"
    LOCK_ACQUISITION     = "LOCK_ACQUISITION"


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class LatencySample:
    category:    OperationCategory
    operation:   str
    duration_ms: float
    timestamp:   str
    tags:        Dict[str, Any] = field(default_factory=dict)


@dataclass
class LatencyStats:
    category:                 OperationCategory
    operation:                str
    p50_ms:                   float
    p95_ms:                   float
    p99_ms:                   float
    min_ms:                   float
    max_ms:                   float
    ewma_ms:                  float
    sample_count:             int
    anomaly_detected:         bool
    exchange_degradation_score: float


# ── Baselines (p50 ms) ─────────────────────────────────────────────────────────

_DEFAULT_BASELINES: Dict[str, float] = {
    "WEBSOCKET":             5.0,
    "REST_API":              50.0,
    "ORDER_ACKNOWLEDGEMENT": 100.0,
    "FILL_CONFIRMATION":     200.0,
    "RECONCILIATION":        500.0,
    "SNAPSHOT":              200.0,
    "EVENT_PERSISTENCE":     20.0,
    "LOCK_ACQUISITION":      5.0,
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(sorted_samples: List[float], pct: float) -> float:
    """Compute percentile using linear interpolation on a sorted list."""
    n = len(sorted_samples)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_samples[0]
    rank = pct / 100.0 * (n - 1)
    lower = int(rank)
    upper = min(lower + 1, n - 1)
    frac = rank - lower
    return sorted_samples[lower] + frac * (sorted_samples[upper] - sorted_samples[lower])


def _rotate_if_needed(path: str, max_lines: int = 10_000) -> bool:
    """Rotate path to path.1 if line count >= max_lines.

    Steps:
    1. Count lines in path (quick pre-check without lock).
    2. If count < max_lines: return False.
    3. Open path with fcntl.LOCK_EX.
    4. Recount under lock (TOCTOU guard).
    5. If still >= max_lines:
       a. fsync the file.
       b. os.replace(path, path + ".1")  — atomic rename.
       c. return True.
    Returns True if rotation occurred.
    """
    try:
        if not os.path.exists(path):
            return False
        # Quick pre-check (no lock — avoids holding lock for large files)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            pre_count = sum(1 for _ in fh)
        if pre_count < max_lines:
            return False
        # Acquire exclusive lock and recount (TOCTOU guard)
        with open(path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                # Recount under lock
                fh.flush()
                fh.seek(0)
            finally:
                # We will release after the rename; open in read mode for recount
                pass
        # Reopen for an accurate locked count + fsync + rename
        with open(path, "r+b") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                locked_count = sum(1 for _ in fh)
                if locked_count < max_lines:
                    return False
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        os.replace(path, path + ".1")
        logger.debug("latency_profiler: rotated %s → %s.1 (%d lines)", path, path, locked_count)
        return True
    except Exception as exc:
        logger.debug("latency_profiler: rotation skipped: %s", exc)
        return False


def _append_jsonl_atomic(path: str, record: dict, max_lines: int = 10_000) -> None:
    """Append a single JSON line to a JSONL file using fcntl.LOCK_EX.

    Rotates path to path.1 atomically when the file reaches max_lines.
    Because JSONL is an append-only log we use fcntl.LOCK_EX on an open of the
    target directly (append mode) — safe for concurrent writers.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # Rotate if needed before appending
        _rotate_if_needed(path, max_lines=max_lines)
        with open(path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(json.dumps(record) + "\n")
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except Exception as exc:
        logger.debug("latency_profiler: analytics write skipped: %s", exc)


# ── LatencyProfiler ───────────────────────────────────────────────────────────


class LatencyProfiler:
    """Captures and analyses latency samples across all OpenClaw operations.

    Thread-safe.  Fail-closed: all exceptions are swallowed after logging.
    """

    def __init__(
        self,
        analytics_path: str = "data/latency_analytics.jsonl",
        max_samples_per_op: int = 10_000,
        anomaly_multiplier: float = 3.0,
        baselines: Optional[Dict[str, float]] = None,
        max_lines_rotation: int = 10_000,
    ) -> None:
        self._analytics_path     = analytics_path
        self._max_samples        = max_samples_per_op
        self._anomaly_multiplier = anomaly_multiplier
        self._baselines          = baselines if baselines is not None else dict(_DEFAULT_BASELINES)
        self._max_lines_rotation = max_lines_rotation

        # keyed by f"{category.value}:{operation}"
        self._samples: Dict[str, List[float]] = {}
        self._ewma:    Dict[str, float]        = {}
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def record(
        self,
        category: OperationCategory,
        operation: str,
        duration_ms: float,
        tags: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Thread-safe sample ingestion."""
        key = f"{category.value}:{operation}"
        with self._lock:
            samples = self._samples.setdefault(key, [])
            samples.append(duration_ms)
            if len(samples) > self._max_samples:
                # Trim oldest samples
                del samples[:len(samples) - self._max_samples]

            # Update EWMA (alpha = 0.1)
            prev = self._ewma.get(key, duration_ms)
            self._ewma[key] = 0.1 * duration_ms + 0.9 * prev

        # Non-blocking persistence
        try:
            _append_jsonl_atomic(
                self._analytics_path,
                {
                    "category":    category.value,
                    "operation":   operation,
                    "duration_ms": duration_ms,
                    "timestamp":   _now_iso(),
                    "tags":        tags or {},
                },
                max_lines=self._max_lines_rotation,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("latency record persist error: %s", exc)

        # Optional Prometheus integration
        try:
            from runtime.metrics import record_latency  # type: ignore[attr-defined]
            record_latency(category.value, operation, duration_ms)
        except Exception:
            pass

    def get_stats(
        self, category: OperationCategory, operation: str
    ) -> Optional[LatencyStats]:
        """Return computed statistics for the given (category, operation) pair."""
        key = f"{category.value}:{operation}"
        with self._lock:
            raw = self._samples.get(key)
            if not raw:
                return None
            sorted_s = sorted(raw)
            ewma     = self._ewma.get(key, 0.0)

        n    = len(sorted_s)
        p50  = _percentile(sorted_s, 50)
        p95  = _percentile(sorted_s, 95)
        p99  = _percentile(sorted_s, 99)
        baseline = self._baselines.get(category.value, 50.0)
        degradation = min(1.0, p99 / (baseline * 5.0)) if baseline > 0 else 0.0
        anomaly = p99 > self._anomaly_multiplier * p50 if p50 > 0 else False

        return LatencyStats(
            category=category,
            operation=operation,
            p50_ms=p50,
            p95_ms=p95,
            p99_ms=p99,
            min_ms=sorted_s[0],
            max_ms=sorted_s[-1],
            ewma_ms=ewma,
            sample_count=n,
            anomaly_detected=anomaly,
            exchange_degradation_score=degradation,
        )

    def get_all_stats(self) -> List[LatencyStats]:
        """Return stats for every tracked (category, operation) pair."""
        with self._lock:
            keys = list(self._samples.keys())

        results: List[LatencyStats] = []
        for key in keys:
            try:
                cat_str, op = key.split(":", 1)
                cat = OperationCategory(cat_str)
                stats = self.get_stats(cat, op)
                if stats is not None:
                    results.append(stats)
            except (ValueError, KeyError):
                continue
        return results

    def detect_timing_drift(
        self,
        category: OperationCategory,
        operation: str,
        window: int = 100,
    ) -> float:
        """Compare EWMA of last `window` samples vs samples before that.

        Returns drift ratio: current_ewma / historical_ewma.
        1.0 = no drift; >1.5 = degradation.
        Returns 1.0 if insufficient data.
        """
        key = f"{category.value}:{operation}"
        with self._lock:
            raw = self._samples.get(key, [])
            if len(raw) < window * 2:
                return 1.0
            recent   = list(raw[-window:])
            historic = list(raw[-window * 2:-window])

        alpha = 0.1

        def _ewma_of(samples: List[float]) -> float:
            acc = samples[0]
            for s in samples[1:]:
                acc = alpha * s + (1.0 - alpha) * acc
            return acc

        current_ewma    = _ewma_of(recent)
        historical_ewma = _ewma_of(historic)
        if historical_ewma == 0.0:
            return 1.0
        return current_ewma / historical_ewma

    def get_exchange_degradation_score(self) -> float:
        """Max degradation score across all WEBSOCKET and REST_API operations."""
        max_score = 0.0
        for cat in (OperationCategory.WEBSOCKET, OperationCategory.REST_API):
            with self._lock:
                keys = [k for k in self._samples if k.startswith(f"{cat.value}:")]
            for key in keys:
                try:
                    _, op = key.split(":", 1)
                    stats = self.get_stats(cat, op)
                    if stats and stats.exchange_degradation_score > max_score:
                        max_score = stats.exchange_degradation_score
                except Exception:
                    continue
        return max_score

    @contextlib.contextmanager
    def measure(
        self,
        category: OperationCategory,
        operation: str,
        tags: Optional[Dict[str, Any]] = None,
    ) -> Generator[None, None, None]:
        """Context manager that records elapsed time as a latency sample."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self.record(category, operation, elapsed_ms, tags)

    def export_prometheus_metrics(self) -> str:
        """Return Prometheus text format gauges for p50/p95/p99 per operation."""
        lines: List[str] = []
        for stats in self.get_all_stats():
            cat = stats.category.value
            op  = stats.operation
            base = f'openclaw_latency_ms{{category="{cat}",operation="{op}"'
            lines.append(f'{base},quantile="p50"}} {stats.p50_ms:.3f}')
            lines.append(f'{base},quantile="p95"}} {stats.p95_ms:.3f}')
            lines.append(f'{base},quantile="p99"}} {stats.p99_ms:.3f}')
        return "\n".join(lines) + ("\n" if lines else "")

    def get_rotation_status(self) -> dict:
        """Return rotation diagnostics for the analytics log file."""
        try:
            path = Path(self._analytics_path)
            line_count = sum(1 for ln in path.open(encoding="utf-8", errors="replace")
                             if ln.strip()) if path.exists() else 0
            rotated_exists = Path(self._analytics_path + ".1").exists()
            return {
                "current_file":        str(path),
                "line_count":          line_count,
                "max_lines":           self._max_lines_rotation,
                "pct_full":            round(line_count / self._max_lines_rotation * 100, 1)
                                       if self._max_lines_rotation else 0,
                "rotated_backup_exists": rotated_exists,
            }
        except Exception:
            return {"status": "unavailable"}


# ── Module singleton ──────────────────────────────────────────────────────────

_profiler: Optional[LatencyProfiler] = None
_profiler_lock = threading.Lock()


def get_profiler() -> LatencyProfiler:
    """Return the module-level LatencyProfiler singleton (double-checked locking)."""
    global _profiler
    if _profiler is None:
        with _profiler_lock:
            if _profiler is None:
                _profiler = LatencyProfiler()
    return _profiler
