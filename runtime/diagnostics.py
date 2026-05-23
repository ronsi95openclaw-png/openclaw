"""Subsystem health diagnostics engine for OpenClaw.

Provides a full point-in-time snapshot of every runtime subsystem:
CryptoComBot, RuntimeOrchestrator, CapitalPreservationEngine,
ReconciliationEngine, DriftDetector, EventStore, ExecutionAnalyticsEngine,
Dashboard API (FastAPI :8000), and Prometheus (:9090).

Usage
-----
    from runtime.diagnostics import get_diagnostics_engine
    engine = get_diagnostics_engine()
    report = engine.run_full_check()
    print(report.overall_status)

All subsystem checks are wrapped in try/except so a single failing
subsystem can never crash the diagnostics pass itself.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.runtime.diagnostics")

# ── File paths ─────────────────────────────────────────────────────────────────

_REPLAY_JOURNAL    = Path("data/replay_journal.jsonl")
_BOT_STATE         = Path("data/cryptocom_state.json")
_CAPITAL_STATE     = Path("data/capital_state.json")
_RECON_LOG         = Path("data/reconciliation.jsonl")
_EVENT_STORE       = Path("data/event_store.jsonl")
_DRIFT_EVENTS      = Path("data/drift_events.jsonl")
_EXEC_ANALYTICS    = Path("data/execution_analytics.jsonl")
_EMERGENCY_LOG     = Path("governance/logs/emergency.jsonl")

# Maximum age (seconds) before a journal/log event is flagged as stale.
_JOURNAL_STALENESS_SEC  = 3600   # 1 hour
_RECON_STALENESS_SEC    = 86400  # 24 hours

# Prometheus connection timeout (seconds)
_PROMETHEUS_TIMEOUT = 2.0


# ── Enums ─────────────────────────────────────────────────────────────────────

class SubsystemStatus(Enum):
    """Severity-ordered health states.  Higher ordinal = more severe."""
    HEALTHY     = "HEALTHY"
    DEGRADED    = "DEGRADED"
    UNREACHABLE = "UNREACHABLE"
    UNKNOWN     = "UNKNOWN"

    def severity(self) -> int:
        return {
            SubsystemStatus.HEALTHY:     0,
            SubsystemStatus.DEGRADED:    1,
            SubsystemStatus.UNREACHABLE: 2,
            SubsystemStatus.UNKNOWN:     3,
        }[self]


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class SubsystemHealth:
    name:          str
    status:        SubsystemStatus
    last_check_ts: str                    # ISO-8601 UTC
    latency_ms:    float
    details:       Dict[str, Any] = field(default_factory=dict)
    error:         Optional[str]  = None


@dataclass
class DiagnosticsReport:
    generated_at:              str                      # ISO-8601 UTC
    overall_status:            SubsystemStatus          # worst of all subsystems
    subsystems:                Dict[str, SubsystemHealth]

    # Capital
    capital_state:             str                      # e.g. "SAFE", "DEFENSIVE", …

    # Positions
    open_positions:            int

    # Reconciliation
    reconciliation_status:     str                      # "PASSED" | "FAILED" | "UNKNOWN"
    last_reconciliation_ts:    Optional[str]

    # Drift
    drift_events_active:       int

    # Dashboard
    websocket_connections:     int

    # Replay journal
    replay_journal_size_bytes: int
    replay_journal_event_count: int

    # Event store
    event_store_last_seq:      int

    # System metrics
    memory_mb:                 float
    thread_count:              int
    open_fds:                  int

    # Incidents
    recent_critical_incidents: List[Dict[str, Any]] = field(default_factory=list)

    # Uptime
    uptime_seconds:            float = 0.0


# ── DiagnosticsEngine ─────────────────────────────────────────────────────────

class DiagnosticsEngine:
    """Run a full subsystem health check and return a DiagnosticsReport."""

    def __init__(self) -> None:
        self._start_time = time.monotonic()

    # ── Public API ─────────────────────────────────────────────────────────────

    def run_full_check(self) -> DiagnosticsReport:
        """Run all subsystem checks and aggregate into a DiagnosticsReport."""
        now_ts = datetime.now(timezone.utc).isoformat()

        # Run all subsystem checks
        subsystems: Dict[str, SubsystemHealth] = {}

        checks = [
            ("exchange_connectivity", self.check_exchange_connectivity),
            ("capital_engine",        self.check_capital_engine),
            ("reconciliation",        self.check_reconciliation),
            ("replay_journal",        self.check_replay_journal),
            ("event_store",           self.check_event_store),
            ("execution_analytics",   self.check_execution_analytics),
            ("drift_detector",        self.check_drift_detector),
            ("prometheus",            self.check_prometheus),
        ]

        for name, fn in checks:
            try:
                health = fn()
            except Exception as exc:
                health = SubsystemHealth(
                    name=name,
                    status=SubsystemStatus.UNREACHABLE,
                    last_check_ts=datetime.now(timezone.utc).isoformat(),
                    latency_ms=0.0,
                    error=f"Unhandled exception in check: {exc}",
                )
            subsystems[name] = health

        # Determine overall status (worst severity wins)
        overall = SubsystemStatus.HEALTHY
        for sh in subsystems.values():
            if sh.status.severity() > overall.severity():
                overall = sh.status

        # Gather supplementary data
        capital_state   = self._get_capital_state()
        open_positions  = self._get_open_positions()
        recon_status, recon_ts = self._get_reconciliation_summary()
        drift_active    = self._count_active_drift_events()
        ws_connections  = self._get_websocket_connections()
        journal_bytes, journal_count = self._get_journal_stats()
        event_store_seq = self._get_event_store_seq()
        sys_metrics     = self._get_system_metrics()
        incidents       = self._get_recent_critical_incidents(n=5)
        uptime          = time.monotonic() - self._start_time

        return DiagnosticsReport(
            generated_at              = now_ts,
            overall_status            = overall,
            subsystems                = subsystems,
            capital_state             = capital_state,
            open_positions            = open_positions,
            reconciliation_status     = recon_status,
            last_reconciliation_ts    = recon_ts,
            drift_events_active       = drift_active,
            websocket_connections     = ws_connections,
            replay_journal_size_bytes = journal_bytes,
            replay_journal_event_count= journal_count,
            event_store_last_seq      = event_store_seq,
            memory_mb                 = sys_metrics.get("memory_mb", 0.0),
            thread_count              = sys_metrics.get("thread_count", 0),
            open_fds                  = sys_metrics.get("open_fds", 0),
            recent_critical_incidents = incidents,
            uptime_seconds            = uptime,
        )

    # ── Subsystem checks ───────────────────────────────────────────────────────

    def check_exchange_connectivity(self) -> SubsystemHealth:
        """Try to fetch a live BTC_USDT ticker and measure round-trip latency."""
        name = "exchange_connectivity"
        t0 = time.monotonic()
        try:
            from trading.exchange import fetch_ticker
            ticker = fetch_ticker("BTC_USDT")
            latency_ms = (time.monotonic() - t0) * 1000
            last_price = ticker.get("last", 0.0)
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.HEALTHY,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                details={"instrument": "BTC_USDT", "last_price": last_price},
            )
        except EnvironmentError as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.DEGRADED,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                details={"note": "API keys not configured — demo/dev environment"},
                error=str(exc),
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.UNREACHABLE,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )

    def check_capital_engine(self) -> SubsystemHealth:
        """Import CapitalPreservationEngine, get_state(), verify state not None."""
        name = "capital_engine"
        t0 = time.monotonic()
        try:
            from risk.capital_preservation import CapitalPreservationEngine
            engine = CapitalPreservationEngine()
            state = engine.get_state()
            if state is None:
                raise ValueError("get_state() returned None")
            latency_ms = (time.monotonic() - t0) * 1000
            details: Dict[str, Any] = {"state": state.value}
            try:
                status_dict = engine.get_status_dict()
                details.update({
                    "risk_scalar":      status_dict.get("risk_scalar"),
                    "daily_drawdown":   status_dict.get("daily_drawdown_pct"),
                    "loss_streak":      status_dict.get("loss_streak"),
                    "state_file_exists": _CAPITAL_STATE.exists(),
                })
            except Exception:
                pass
            sub_status = SubsystemStatus.HEALTHY
            if state.value in ("CRITICAL", "EMERGENCY_HALT"):
                sub_status = SubsystemStatus.DEGRADED
            return SubsystemHealth(
                name=name,
                status=sub_status,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                details=details,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.UNREACHABLE,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )

    def check_reconciliation(self) -> SubsystemHealth:
        """Read the last line of data/reconciliation.jsonl and check its timestamp."""
        name = "reconciliation"
        t0 = time.monotonic()
        try:
            if not _RECON_LOG.exists():
                return SubsystemHealth(
                    name=name,
                    status=SubsystemStatus.UNKNOWN,
                    last_check_ts=datetime.now(timezone.utc).isoformat(),
                    latency_ms=0.0,
                    details={"note": "reconciliation.jsonl not found — no run yet"},
                )
            last_entry = _read_last_jsonl_line(_RECON_LOG)
            latency_ms = (time.monotonic() - t0) * 1000
            if last_entry is None:
                return SubsystemHealth(
                    name=name,
                    status=SubsystemStatus.UNKNOWN,
                    last_check_ts=datetime.now(timezone.utc).isoformat(),
                    latency_ms=round(latency_ms, 2),
                    details={"note": "reconciliation.jsonl is empty"},
                )
            ts_str    = last_entry.get("ts", "")
            passed    = last_entry.get("passed", None)
            age_sec   = _event_age_seconds(ts_str)
            stale     = age_sec is not None and age_sec > _RECON_STALENESS_SEC

            sub_status = SubsystemStatus.HEALTHY
            if passed is False:
                sub_status = SubsystemStatus.DEGRADED
            if stale:
                sub_status = SubsystemStatus.DEGRADED

            return SubsystemHealth(
                name=name,
                status=sub_status,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                details={
                    "last_ts":           ts_str,
                    "passed":            passed,
                    "halt_required":     last_entry.get("halt_required"),
                    "critical_count":    last_entry.get("critical_count", 0),
                    "age_seconds":       age_sec,
                    "stale":             stale,
                },
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.UNREACHABLE,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )

    def check_replay_journal(self) -> SubsystemHealth:
        """Check replay_journal.jsonl existence, size, line count, last event age."""
        name = "replay_journal"
        t0 = time.monotonic()
        try:
            if not _REPLAY_JOURNAL.exists():
                return SubsystemHealth(
                    name=name,
                    status=SubsystemStatus.UNKNOWN,
                    last_check_ts=datetime.now(timezone.utc).isoformat(),
                    latency_ms=0.0,
                    details={"note": "replay_journal.jsonl not found — no events yet"},
                )
            stat      = _REPLAY_JOURNAL.stat()
            size_bytes = stat.st_size
            line_count = _count_jsonl_lines(_REPLAY_JOURNAL)
            last_entry = _read_last_jsonl_line(_REPLAY_JOURNAL)
            latency_ms = (time.monotonic() - t0) * 1000

            age_sec    = None
            last_ts    = ""
            if last_entry is not None:
                last_ts = last_entry.get("ts", "")
                age_sec = _event_age_seconds(last_ts)

            stale      = age_sec is not None and age_sec > _JOURNAL_STALENESS_SEC
            sub_status = SubsystemStatus.DEGRADED if stale else SubsystemStatus.HEALTHY

            return SubsystemHealth(
                name=name,
                status=sub_status,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                details={
                    "size_bytes":   size_bytes,
                    "line_count":   line_count,
                    "last_ts":      last_ts,
                    "age_seconds":  age_sec,
                    "stale":        stale,
                },
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.UNREACHABLE,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )

    def check_event_store(self) -> SubsystemHealth:
        """Import EventStore, call get_latest_seq(), verify file exists."""
        name = "event_store"
        t0 = time.monotonic()
        try:
            from runtime.event_store import EventStore
            store = EventStore()
            seq = store.get_latest_seq()
            latency_ms = (time.monotonic() - t0) * 1000
            file_exists = _EVENT_STORE.exists()
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.HEALTHY,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                details={
                    "latest_seq":   seq,
                    "file_exists":  file_exists,
                    "file_size_bytes": _EVENT_STORE.stat().st_size if file_exists else 0,
                },
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.UNREACHABLE,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )

    def check_execution_analytics(self) -> SubsystemHealth:
        """Import ExecutionAnalyticsEngine and check data/execution_analytics.jsonl."""
        name = "execution_analytics"
        t0 = time.monotonic()
        try:
            from runtime.execution_analytics import ExecutionAnalyticsEngine
            engine = ExecutionAnalyticsEngine()
            latency_ms = (time.monotonic() - t0) * 1000
            file_exists = _EXEC_ANALYTICS.exists()
            details: Dict[str, Any] = {"file_exists": file_exists}
            if file_exists:
                details["file_size_bytes"] = _EXEC_ANALYTICS.stat().st_size
                details["line_count"] = _count_jsonl_lines(_EXEC_ANALYTICS)
            try:
                report = engine.generate_report()
                details["total_trades"] = report.total_trades
                details["avg_slippage_bps"] = report.avg_slippage_bps
            except Exception:
                pass
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.HEALTHY,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                details=details,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.UNREACHABLE,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )

    def check_drift_detector(self) -> SubsystemHealth:
        """Read data/drift_events.jsonl last entry age."""
        name = "drift_detector"
        t0 = time.monotonic()
        try:
            if not _DRIFT_EVENTS.exists():
                return SubsystemHealth(
                    name=name,
                    status=SubsystemStatus.UNKNOWN,
                    last_check_ts=datetime.now(timezone.utc).isoformat(),
                    latency_ms=0.0,
                    details={"note": "drift_events.jsonl not found — no drift events recorded yet"},
                )
            last_entry = _read_last_jsonl_line(_DRIFT_EVENTS)
            latency_ms = (time.monotonic() - t0) * 1000
            if last_entry is None:
                return SubsystemHealth(
                    name=name,
                    status=SubsystemStatus.UNKNOWN,
                    last_check_ts=datetime.now(timezone.utc).isoformat(),
                    latency_ms=round(latency_ms, 2),
                    details={"note": "drift_events.jsonl is empty"},
                )
            ts_str  = last_entry.get("ts", "")
            age_sec = _event_age_seconds(ts_str)
            stale   = age_sec is not None and age_sec > _JOURNAL_STALENESS_SEC
            line_count = _count_jsonl_lines(_DRIFT_EVENTS)
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.DEGRADED if stale else SubsystemStatus.HEALTHY,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                details={
                    "last_ts":     ts_str,
                    "age_seconds": age_sec,
                    "stale":       stale,
                    "event_count": line_count,
                    "last_event":  last_entry,
                },
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.UNREACHABLE,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )

    def check_prometheus(self) -> SubsystemHealth:
        """Try a TCP connection to localhost:9090 with a 2-second timeout."""
        name = "prometheus"
        t0 = time.monotonic()
        try:
            sock = socket.create_connection(("localhost", 9090),
                                            timeout=_PROMETHEUS_TIMEOUT)
            sock.close()
            latency_ms = (time.monotonic() - t0) * 1000
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.HEALTHY,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                details={"endpoint": "localhost:9090"},
            )
        except (ConnectionRefusedError, socket.timeout, OSError) as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return SubsystemHealth(
                name=name,
                status=SubsystemStatus.UNREACHABLE,
                last_check_ts=datetime.now(timezone.utc).isoformat(),
                latency_ms=round(latency_ms, 2),
                details={"endpoint": "localhost:9090"},
                error=str(exc),
            )

    # ── System metrics ─────────────────────────────────────────────────────────

    def _get_system_metrics(self) -> Dict[str, Any]:
        """Return memory_mb, thread_count, open_fds via psutil (or /proc fallback)."""
        metrics: Dict[str, Any] = {
            "memory_mb":    0.0,
            "thread_count": threading.active_count(),
            "open_fds":     0,
        }
        # Try psutil first
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            mem_info = proc.memory_info()
            metrics["memory_mb"]    = round(mem_info.rss / (1024 * 1024), 2)
            metrics["thread_count"] = proc.num_threads()
            try:
                metrics["open_fds"] = proc.num_fds()
            except Exception:
                pass
            return metrics
        except ImportError:
            pass

        # /proc fallback (Linux)
        pid = os.getpid()
        try:
            status_path = Path(f"/proc/{pid}/status")
            if status_path.exists():
                for line in status_path.read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        metrics["memory_mb"] = round(kb / 1024, 2)
                        break
        except Exception:
            pass

        try:
            fd_dir = Path(f"/proc/{pid}/fd")
            if fd_dir.exists():
                metrics["open_fds"] = len(list(fd_dir.iterdir()))
        except Exception:
            pass

        return metrics

    # ── Supplementary data ─────────────────────────────────────────────────────

    def _get_capital_state(self) -> str:
        try:
            if _CAPITAL_STATE.exists():
                data = json.loads(_CAPITAL_STATE.read_text())
                return data.get("state", "UNKNOWN")
        except Exception:
            pass
        try:
            from risk.capital_preservation import CapitalPreservationEngine
            return CapitalPreservationEngine().get_state().value
        except Exception:
            pass
        return "UNKNOWN"

    def _get_open_positions(self) -> int:
        try:
            if _BOT_STATE.exists():
                data = json.loads(_BOT_STATE.read_text())
                positions = data.get("open_positions", [])
                if isinstance(positions, list):
                    return len(positions)
        except Exception:
            pass
        return 0

    def _get_reconciliation_summary(self) -> tuple[str, Optional[str]]:
        """Return (status_str, last_ts_str) from the last reconciliation log entry."""
        try:
            if not _RECON_LOG.exists():
                return "UNKNOWN", None
            last = _read_last_jsonl_line(_RECON_LOG)
            if last is None:
                return "UNKNOWN", None
            passed = last.get("passed", None)
            ts_str = last.get("ts", None)
            if passed is True:
                return "PASSED", ts_str
            if passed is False:
                return "FAILED", ts_str
            return "UNKNOWN", ts_str
        except Exception:
            return "UNKNOWN", None

    def _count_active_drift_events(self) -> int:
        """Count lines in drift_events.jsonl where resolved=False (or not present)."""
        try:
            if not _DRIFT_EVENTS.exists():
                return 0
            count = 0
            with _DRIFT_EVENTS.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if not entry.get("resolved", False):
                            count += 1
                    except Exception:
                        continue
            return count
        except Exception:
            return 0

    def _get_websocket_connections(self) -> int:
        """Read the _ws_connection_count global from the dashboard API server module."""
        try:
            import dashboard.api.server as _srv
            return getattr(_srv, "_ws_connection_count", 0)
        except Exception:
            return 0

    def _get_journal_stats(self) -> tuple[int, int]:
        """Return (size_bytes, line_count) for replay_journal.jsonl."""
        try:
            if not _REPLAY_JOURNAL.exists():
                return 0, 0
            size_bytes = _REPLAY_JOURNAL.stat().st_size
            line_count = _count_jsonl_lines(_REPLAY_JOURNAL)
            return size_bytes, line_count
        except Exception:
            return 0, 0

    def _get_event_store_seq(self) -> int:
        """Return the latest sequence number from the event store."""
        try:
            from runtime.event_store import EventStore
            return EventStore().get_latest_seq()
        except Exception:
            pass
        # Fallback: read last line of the store file directly
        try:
            last = _read_last_jsonl_line(_EVENT_STORE)
            if last is not None:
                return int(last.get("seq", 0))
        except Exception:
            pass
        return 0

    def _get_recent_critical_incidents(self, n: int = 5) -> List[Dict[str, Any]]:
        """Return the last N CRITICAL entries from reconciliation.jsonl and
        governance/logs/emergency.jsonl, merged and sorted descending by ts."""
        incidents: List[Dict[str, Any]] = []

        # Reconciliation CRITICAL mismatches
        try:
            if _RECON_LOG.exists():
                entries = _read_tail_jsonl(_RECON_LOG, lines=50)
                for entry in entries:
                    if entry.get("critical_count", 0) > 0:
                        incidents.append({
                            "source":    "reconciliation",
                            "ts":        entry.get("ts", ""),
                            "severity":  "CRITICAL",
                            "notes":     entry.get("notes", ""),
                            "critical_count": entry.get("critical_count", 0),
                        })
        except Exception:
            pass

        # Emergency governance log
        try:
            if _EMERGENCY_LOG.exists():
                entries = _read_tail_jsonl(_EMERGENCY_LOG, lines=50)
                for entry in entries:
                    incidents.append({
                        "source":   "governance/emergency",
                        "ts":       entry.get("ts", entry.get("timestamp", "")),
                        "severity": "CRITICAL",
                        "reason":   entry.get("reason", entry.get("halt_reason", "")),
                        "operator": entry.get("operator_id", ""),
                    })
        except Exception:
            pass

        # Sort descending by ts string (ISO-8601 strings sort lexicographically)
        incidents.sort(key=lambda x: x.get("ts", ""), reverse=True)
        return incidents[:n]


# ── File helpers ──────────────────────────────────────────────────────────────

def _read_last_jsonl_line(path: Path) -> Optional[Dict[str, Any]]:
    """Read the last non-empty, valid JSON line from a JSONL file."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell() - 1
            buf = b""
            while pos >= 0:
                fh.seek(pos)
                ch = fh.read(1)
                if ch == b"\n" and buf.strip():
                    break
                buf = ch + buf
                pos -= 1
        line = buf.strip()
        if not line:
            return None
        return json.loads(line.decode("utf-8"))
    except Exception:
        return None


def _read_tail_jsonl(path: Path, lines: int = 50) -> List[Dict[str, Any]]:
    """Read the last N valid JSON lines from a JSONL file (most-recent-last)."""
    results: List[Dict[str, Any]] = []
    if not path.exists():
        return results
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(raw_lines):
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except Exception:
                continue
            if len(results) >= lines:
                break
    except Exception:
        pass
    return list(reversed(results))


def _count_jsonl_lines(path: Path) -> int:
    """Count non-empty lines in a JSONL file efficiently."""
    count = 0
    try:
        with path.open("rb") as fh:
            for line in fh:
                if line.strip():
                    count += 1
    except Exception:
        pass
    return count


def _event_age_seconds(ts_str: Any) -> Optional[float]:
    """Return seconds since an ISO-8601 timestamp. Returns None if unparseable."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        normalised = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalised)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds()
    except Exception:
        return None


# ── Singleton ─────────────────────────────────────────────────────────────────

_engine_instance: Optional[DiagnosticsEngine] = None
_engine_lock = threading.Lock()


def get_diagnostics_engine() -> DiagnosticsEngine:
    """Return the process-level singleton DiagnosticsEngine."""
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = DiagnosticsEngine()
    return _engine_instance
