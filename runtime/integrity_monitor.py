"""IntegrityMonitor — continuous validation of EventStore, snapshots, and replay.

Runs periodic scans (default 300 s) across seven check categories:

1. _check_event_store_integrity    — EventStore.verify_integrity() on last N events
2. _check_snapshot_integrity       — verify_snapshot() on last 5 snapshots
3. _check_sequence_monotonicity    — strictly increasing seq across last 100 events
4. _check_replay_determinism       — two reconstructions must produce identical state
5. _check_reconciliation_consistency — data/reconciliation.jsonl age during trading hours
6. _check_governance_persistence   — data/governance_decisions.jsonl readability
7. _check_event_store_growth       — seq growth during expected trading hours

Each check is isolated in its own try/except — one failing check never prevents
others.  Results accumulate into an IntegrityReport.

CRITICAL behaviour
------------------
When any finding has severity=CRITICAL:
  * Single Telegram alert per scan (not per finding).
  * Prometheus openclaw_integrity_failures_total{subsystem=...} incremented.
  * EventStore RECONCILIATION_INCIDENT appended (best-effort).
  * If halt_on_critical=True AND finding.auto_halt=True:
        data/integrity_halt.json written.
  * Finding appended to data/governance/logs/integrity_incidents.jsonl (fcntl locked).

Design invariants
-----------------
  * Bounded: only last event_scan_window events examined.
  * Incremental: last_scanned_seq tracks progress.
  * Read-only: never mutates EventStore.
  * Thread-safe: threading.Lock on all mutable state.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.runtime.integrity_monitor")

# ── File paths ────────────────────────────────────────────────────────────────

_RECON_LOG_PATH = "data/reconciliation.jsonl"
_GOV_DECISIONS_PATH = "data/governance_decisions.jsonl"
_INTEGRITY_HALT_PATH = "data/integrity_halt.json"
_INTEGRITY_INCIDENTS_PATH = "data/governance/logs/integrity_incidents.jsonl"

# Trading hours (UTC): 08:00 – 23:00
_TRADING_HOUR_START = 8
_TRADING_HOUR_END = 23

# Age thresholds
_RECON_STALE_DURING_TRADING_MINUTES = 10
_SNAPSHOT_AGE_WARNING_HOURS = 48
_GROWTH_STALE_MINUTES = 30

# Prometheus metric singletons (lazy-init)
_prom_integrity_failures: Any = None
_prom_scan_duration: Any = None
_prom_init_lock = threading.Lock()
_prom_initialized = False


def _init_prometheus() -> None:
    global _prom_integrity_failures, _prom_scan_duration, _prom_initialized
    if _prom_initialized:
        return
    with _prom_init_lock:
        if _prom_initialized:
            return
        try:
            from prometheus_client import Counter, Histogram  # type: ignore[import]
            _prom_integrity_failures = Counter(
                "openclaw_integrity_failures_total",
                "Total CRITICAL integrity findings by subsystem",
                ["subsystem"],
            )
            _prom_scan_duration = Histogram(
                "openclaw_integrity_scan_duration_seconds",
                "Duration of each IntegrityMonitor scan in seconds",
                buckets=[0.1, 0.5, 1.0, 5.0, 15.0, 30.0, 60.0],
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("integrity_monitor: prometheus_client unavailable: %s", exc)
        finally:
            _prom_initialized = True


# ── Enums & dataclasses ───────────────────────────────────────────────────────

class IntegritySeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    def __gt__(self, other: "IntegritySeverity") -> bool:
        order = {IntegritySeverity.INFO: 0, IntegritySeverity.WARNING: 1, IntegritySeverity.CRITICAL: 2}
        return order[self] > order[other]

    def __ge__(self, other: "IntegritySeverity") -> bool:
        return self == other or self > other


@dataclass
class IntegrityFinding:
    finding_id: str          # UUID4 string
    severity: IntegritySeverity
    subsystem: str
    description: str
    detected_at: str         # ISO-8601 UTC
    remediation_hint: str
    auto_halt: bool          # if True AND halt_on_critical=True → write halt marker


@dataclass
class IntegrityReport:
    generated_at: str                       # ISO-8601 UTC
    findings: List[IntegrityFinding]
    overall_severity: IntegritySeverity     # worst severity across all findings
    scan_duration_ms: float
    events_scanned: int
    snapshots_checked: int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_trading_hours() -> bool:
    """Return True if the current UTC hour is within trading hours."""
    h = datetime.now(timezone.utc).hour
    return _TRADING_HOUR_START <= h < _TRADING_HOUR_END


def _event_age_seconds(ts_str: Any) -> Optional[float]:
    """Return seconds since an ISO-8601 timestamp; None if unparseable."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        normalised = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalised)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None


def _read_last_jsonl_line(path: str) -> Optional[Dict[str, Any]]:
    """Read the last non-empty, valid JSON line from a JSONL file."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size == 0:
                return None
            pos = size - 1
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


def _make_finding(
    severity: IntegritySeverity,
    subsystem: str,
    description: str,
    remediation_hint: str = "",
    auto_halt: bool = False,
) -> IntegrityFinding:
    return IntegrityFinding(
        finding_id=str(uuid.uuid4()),
        severity=severity,
        subsystem=subsystem,
        description=description,
        detected_at=_now_iso(),
        remediation_hint=remediation_hint,
        auto_halt=auto_halt,
    )


def _worst_severity(findings: List[IntegrityFinding]) -> IntegritySeverity:
    if not findings:
        return IntegritySeverity.INFO
    order = {IntegritySeverity.INFO: 0, IntegritySeverity.WARNING: 1, IntegritySeverity.CRITICAL: 2}
    return max(findings, key=lambda f: order[f.severity]).severity


# ── IntegrityMonitor ──────────────────────────────────────────────────────────

class IntegrityMonitor:
    """Continuously validates EventStore, snapshots, and replay consistency.

    Parameters
    ----------
    scan_interval_seconds:
        How often to run a full scan (default 300 s / 5 min).
    event_scan_window:
        Maximum number of recent events to examine per scan.
    halt_on_critical:
        If True, write data/integrity_halt.json when a CRITICAL finding has
        auto_halt=True.
    """

    def __init__(
        self,
        scan_interval_seconds: float = 300.0,
        event_scan_window: int = 1000,
        halt_on_critical: bool = False,
    ) -> None:
        self._scan_interval = scan_interval_seconds
        self._event_scan_window = event_scan_window
        self._halt_on_critical = halt_on_critical

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._last_report: Optional[IntegrityReport] = None
        self._last_scanned_seq: int = 0
        self._seq_at_last_check: int = 0   # for growth check
        self._seq_check_ts: float = 0.0    # monotonic ts of _seq_at_last_check

        # Ensure governance log directory exists
        try:
            os.makedirs(os.path.dirname(_INTEGRITY_INCIDENTS_PATH), exist_ok=True)
        except OSError:
            pass

        try:
            _init_prometheus()
        except Exception:  # noqa: BLE001
            pass

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Launch the background scan thread (idempotent)."""
        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="IntegrityMonitor",
                daemon=True,
            )
            self._running = True
            self._thread.start()
        logger.info("integrity_monitor: started (interval=%ss)", self._scan_interval)

    def stop(self) -> None:
        """Stop the background thread gracefully."""
        with self._lock:
            if not self._running:
                return
            thread = self._thread

        self._stop_event.set()
        if thread is not None:
            thread.join(timeout=10.0)
        with self._lock:
            self._running = False
        logger.info("integrity_monitor: stopped")

    def run_scan(self) -> IntegrityReport:
        """Run all checks and return an IntegrityReport.  Callable on demand."""
        t0 = time.monotonic()
        all_findings: List[IntegrityFinding] = []
        events_scanned = 0
        snapshots_checked = 0

        # Each check is fully isolated
        checks = [
            ("event_store",          self._check_event_store_integrity),
            ("snapshot",             self._check_snapshot_integrity),
            ("seq_monotonicity",     self._check_sequence_monotonicity),
            ("replay_determinism",   self._check_replay_determinism),
            ("reconciliation",       self._check_reconciliation_consistency),
            ("governance",           self._check_governance_persistence),
            ("event_store_growth",   self._check_event_store_growth),
        ]

        for _name, fn in checks:
            try:
                result = fn()
                # Result is (findings, events_scanned_delta, snapshots_checked_delta)
                # or just findings for simpler checks.
                if isinstance(result, tuple):
                    findings, ev_delta, snap_delta = result
                    events_scanned += ev_delta
                    snapshots_checked += snap_delta
                else:
                    findings = result
                all_findings.extend(findings)
            except Exception as exc:  # noqa: BLE001
                # A check itself crashing is a WARNING
                logger.error("integrity_monitor: check %r raised: %s", _name, exc)
                all_findings.append(_make_finding(
                    IntegritySeverity.WARNING,
                    _name,
                    f"Check raised unexpected exception: {exc}",
                    remediation_hint="Investigate integrity_monitor logs",
                ))

        scan_duration_ms = (time.monotonic() - t0) * 1000.0
        overall = _worst_severity(all_findings)

        report = IntegrityReport(
            generated_at=_now_iso(),
            findings=all_findings,
            overall_severity=overall,
            scan_duration_ms=round(scan_duration_ms, 2),
            events_scanned=events_scanned,
            snapshots_checked=snapshots_checked,
        )

        with self._lock:
            self._last_report = report

        # Handle CRITICAL findings
        critical_findings = [f for f in all_findings if f.severity == IntegritySeverity.CRITICAL]
        if critical_findings:
            self._handle_critical_findings(critical_findings)

        # Prometheus scan duration (best-effort)
        try:
            if _prom_scan_duration is not None:
                _prom_scan_duration.observe(scan_duration_ms / 1000.0)
        except Exception:  # noqa: BLE001
            pass

        logger.info(
            "integrity_monitor: scan complete — severity=%s findings=%d "
            "duration=%.1fms events_scanned=%d",
            overall.value,
            len(all_findings),
            scan_duration_ms,
            events_scanned,
        )
        return report

    def get_last_report(self) -> Optional[IntegrityReport]:
        """Return the most recent scan report (None if no scan has run yet)."""
        with self._lock:
            return self._last_report

    def get_status(self) -> dict:
        """Return a point-in-time status dict."""
        with self._lock:
            report = self._last_report
            running = self._running

        status: Dict[str, Any] = {
            "running": running,
            "scan_interval_seconds": self._scan_interval,
            "event_scan_window": self._event_scan_window,
            "halt_on_critical": self._halt_on_critical,
            "last_scan_at": report.generated_at if report else None,
            "last_overall_severity": report.overall_severity.value if report else None,
            "last_finding_count": len(report.findings) if report else 0,
            "last_scan_duration_ms": report.scan_duration_ms if report else None,
        }
        return status

    # ── Daemon loop ───────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        logger.debug("integrity_monitor: _run_loop started")
        while not self._stop_event.is_set():
            try:
                self.run_scan()
            except Exception as exc:  # noqa: BLE001
                logger.error("integrity_monitor: run_scan raised: %s", exc)

            # Sleep in small increments so stop_event is responsive
            deadline = time.monotonic() + self._scan_interval
            while not self._stop_event.is_set() and time.monotonic() < deadline:
                self._stop_event.wait(timeout=min(5.0, deadline - time.monotonic()))

        logger.debug("integrity_monitor: _run_loop exiting")

    # ── Individual checks ─────────────────────────────────────────────────────

    def _check_event_store_integrity(self):
        """Check EventStore checksums and sequence gaps on last N events.

        Returns (findings, events_scanned, 0).
        """
        findings: List[IntegrityFinding] = []
        events_scanned = 0
        subsystem = "event_store"

        try:
            from runtime.event_store import EventStore

            store = EventStore()
            latest_seq = store.get_latest_seq()
            start_seq = max(0, latest_seq - self._event_scan_window + 1)

            ok, errors = store.verify_integrity(start_seq=start_seq)
            events = store.read_from(seq=start_seq, limit=self._event_scan_window)
            events_scanned = len(events)

            checksum_errors = [e for e in errors if "Checksum" in e or "Malformed" in e]
            gap_errors = [e for e in errors if "Sequence gap" in e or "JSON parse" in e]

            if checksum_errors:
                findings.append(_make_finding(
                    IntegritySeverity.CRITICAL,
                    subsystem,
                    f"EventStore checksum failure(s) in last {self._event_scan_window} events: "
                    f"{'; '.join(checksum_errors[:3])}",
                    remediation_hint=(
                        "EventStore may be corrupt. Restore from latest clean snapshot."
                    ),
                    auto_halt=True,
                ))
            if gap_errors:
                findings.append(_make_finding(
                    IntegritySeverity.WARNING,
                    subsystem,
                    f"Sequence gaps or parse errors detected: {'; '.join(gap_errors[:3])}",
                    remediation_hint="Check for concurrent writers or file truncation.",
                ))

            # Update incremental tracking
            with self._lock:
                self._last_scanned_seq = latest_seq

        except Exception as exc:  # noqa: BLE001
            findings.append(_make_finding(
                IntegritySeverity.WARNING,
                subsystem,
                f"EventStore integrity check failed to run: {exc}",
                remediation_hint="Verify EventStore file is accessible.",
            ))

        return findings, events_scanned, 0

    def _check_snapshot_integrity(self):
        """Verify the last 5 snapshots and check age of the newest one.

        Returns (findings, 0, snapshots_checked).
        """
        findings: List[IntegrityFinding] = []
        snapshots_checked = 0
        subsystem = "snapshot"
        _MAX_SNAPSHOTS_TO_CHECK = 5

        try:
            from runtime.event_snapshot import EventSnapshotEngine

            engine = EventSnapshotEngine()
            snapshots = engine.list_snapshots()  # newest-first
            to_check = snapshots[:_MAX_SNAPSHOTS_TO_CHECK]
            snapshots_checked = len(to_check)

            for meta in to_check:
                ok = engine.verify_snapshot(meta)
                if not ok:
                    findings.append(_make_finding(
                        IntegritySeverity.CRITICAL,
                        subsystem,
                        f"Snapshot {meta.snapshot_id} (seq={meta.seq_at_snapshot}) "
                        f"failed checksum verification",
                        remediation_hint=(
                            "Snapshot file is corrupt. Do not use for recovery. "
                            "Check disk health."
                        ),
                        auto_halt=False,
                    ))

            # Check age of the newest snapshot
            if snapshots:
                newest = snapshots[0]
                age_sec = _event_age_seconds(newest.created_at)
                if age_sec is not None and age_sec > _SNAPSHOT_AGE_WARNING_HOURS * 3600:
                    findings.append(_make_finding(
                        IntegritySeverity.WARNING,
                        subsystem,
                        f"Newest snapshot is {age_sec / 3600:.1f} hours old "
                        f"(threshold: {_SNAPSHOT_AGE_WARNING_HOURS}h). "
                        f"Snapshot daemon may be stalled.",
                        remediation_hint=(
                            "Check SnapshotDaemon status and consecutive_failures counter."
                        ),
                    ))
            else:
                findings.append(_make_finding(
                    IntegritySeverity.WARNING,
                    subsystem,
                    "No snapshots found in snapshot index.",
                    remediation_hint=(
                        "Run force_snapshot_now() to create an initial snapshot."
                    ),
                ))

        except Exception as exc:  # noqa: BLE001
            findings.append(_make_finding(
                IntegritySeverity.WARNING,
                subsystem,
                f"Snapshot integrity check failed to run: {exc}",
                remediation_hint="Verify snapshot directory and EventSnapshotEngine.",
            ))

        return findings, 0, snapshots_checked

    def _check_sequence_monotonicity(self):
        """Verify last 100 events have strictly increasing seq numbers.

        Returns (findings, events_scanned, 0).
        """
        findings: List[IntegrityFinding] = []
        events_scanned = 0
        subsystem = "seq_monotonicity"
        _WINDOW = 100

        try:
            from runtime.event_store import EventStore

            store = EventStore()
            latest_seq = store.get_latest_seq()
            start_seq = max(0, latest_seq - _WINDOW + 1)
            events = store.read_from(seq=start_seq, limit=_WINDOW)
            events_scanned = len(events)

            prev_seq: Optional[int] = None
            for ev in events:
                if prev_seq is not None:
                    if ev.seq <= prev_seq:
                        findings.append(_make_finding(
                            IntegritySeverity.CRITICAL,
                            subsystem,
                            f"Non-monotonic sequence: seq={ev.seq} follows seq={prev_seq}. "
                            f"Possible duplicate or out-of-order write.",
                            remediation_hint=(
                                "Stop all writers immediately. Inspect EventStore for corruption."
                            ),
                            auto_halt=True,
                        ))
                prev_seq = ev.seq

        except Exception as exc:  # noqa: BLE001
            findings.append(_make_finding(
                IntegritySeverity.WARNING,
                subsystem,
                f"Sequence monotonicity check failed to run: {exc}",
                remediation_hint="Verify EventStore accessibility.",
            ))

        return findings, events_scanned, 0

    def _check_replay_determinism(self):
        """Run reconstruct_portfolio_state() twice and compare results.

        Returns (findings, events_scanned, 0).
        """
        findings: List[IntegrityFinding] = []
        events_scanned = 0
        subsystem = "replay_determinism"
        _WINDOW = 500

        try:
            from runtime.event_store import EventStore, EventReplayEngine

            store = EventStore()
            latest_seq = store.get_latest_seq()
            start_seq = max(0, latest_seq - _WINDOW + 1)
            events = store.read_from(seq=start_seq, limit=_WINDOW)
            events_scanned = len(events)

            engine = EventReplayEngine(store)

            result_a = store.reconstruct_state_from_events(events)
            result_b = store.reconstruct_state_from_events(events)

            if result_a != result_b:
                findings.append(_make_finding(
                    IntegritySeverity.CRITICAL,
                    subsystem,
                    "Non-deterministic replay: two reconstructions of the same event window "
                    "produced different results. State machine is not deterministic.",
                    remediation_hint=(
                        "Check for side-effects in event handlers. Do not proceed with trading."
                    ),
                    auto_halt=True,
                ))

        except Exception as exc:  # noqa: BLE001
            findings.append(_make_finding(
                IntegritySeverity.WARNING,
                subsystem,
                f"Replay determinism check failed to run: {exc}",
                remediation_hint="Verify EventStore and EventReplayEngine.",
            ))

        return findings, events_scanned, 0

    def _check_reconciliation_consistency(self):
        """Check age of the most recent reconciliation entry during trading hours.

        Returns (findings, 0, 0).
        """
        findings: List[IntegrityFinding] = []
        subsystem = "reconciliation"

        try:
            if not os.path.exists(_RECON_LOG_PATH):
                return findings, 0, 0  # Not an error if file doesn't exist yet

            last_entry = _read_last_jsonl_line(_RECON_LOG_PATH)
            if last_entry is None:
                return findings, 0, 0

            ts_str = last_entry.get("ts", "")
            age_sec = _event_age_seconds(ts_str)

            if _is_trading_hours() and age_sec is not None:
                stale_threshold = _RECON_STALE_DURING_TRADING_MINUTES * 60
                if age_sec > stale_threshold:
                    findings.append(_make_finding(
                        IntegritySeverity.WARNING,
                        subsystem,
                        f"Reconciliation log is {age_sec / 60:.1f} minutes old during "
                        f"trading hours (threshold: {_RECON_STALE_DURING_TRADING_MINUTES} min). "
                        f"Reconciliation engine may be stalled.",
                        remediation_hint=(
                            "Check ReconciliationEngine status and loop health."
                        ),
                    ))

        except Exception as exc:  # noqa: BLE001
            findings.append(_make_finding(
                IntegritySeverity.WARNING,
                subsystem,
                f"Reconciliation consistency check failed to run: {exc}",
                remediation_hint="Verify reconciliation.jsonl is accessible.",
            ))

        return findings, 0, 0

    def _check_governance_persistence(self):
        """Verify data/governance_decisions.jsonl is readable and last entry is valid JSON.

        Returns (findings, 0, 0).
        """
        findings: List[IntegrityFinding] = []
        subsystem = "governance"

        try:
            if not os.path.exists(_GOV_DECISIONS_PATH):
                findings.append(_make_finding(
                    IntegritySeverity.WARNING,
                    subsystem,
                    f"Governance decisions file not found: {_GOV_DECISIONS_PATH}",
                    remediation_hint=(
                        "Verify governance_decisions.jsonl path and file permissions."
                    ),
                ))
                return findings, 0, 0

            # Try to open and read the last line
            last_entry = None
            raw_last_line = ""
            try:
                with open(_GOV_DECISIONS_PATH, "rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    if size == 0:
                        return findings, 0, 0
                    pos = size - 1
                    buf = b""
                    while pos >= 0:
                        fh.seek(pos)
                        ch = fh.read(1)
                        if ch == b"\n" and buf.strip():
                            break
                        buf = ch + buf
                        pos -= 1
                raw_last_line = buf.strip().decode("utf-8", errors="replace")
            except OSError as exc:
                findings.append(_make_finding(
                    IntegritySeverity.WARNING,
                    subsystem,
                    f"Cannot read governance decisions file: {exc}",
                    remediation_hint="Check file permissions on governance_decisions.jsonl.",
                ))
                return findings, 0, 0

            if not raw_last_line:
                return findings, 0, 0

            try:
                last_entry = json.loads(raw_last_line)
            except json.JSONDecodeError as exc:
                findings.append(_make_finding(
                    IntegritySeverity.CRITICAL,
                    subsystem,
                    f"Last entry in governance_decisions.jsonl is truncated or invalid JSON: {exc}. "
                    f"File may have been corrupted during a crash.",
                    remediation_hint=(
                        "Do not append to governance_decisions.jsonl until the file is repaired. "
                        "Truncate or remove the corrupt last line."
                    ),
                    auto_halt=False,
                ))

        except Exception as exc:  # noqa: BLE001
            findings.append(_make_finding(
                IntegritySeverity.WARNING,
                subsystem,
                f"Governance persistence check failed to run: {exc}",
                remediation_hint="Investigate integrity_monitor logs.",
            ))

        return findings, 0, 0

    def _check_event_store_growth(self):
        """Detect if event store has stalled during expected trading hours.

        Returns (findings, 0, 0).
        """
        findings: List[IntegrityFinding] = []
        subsystem = "event_store_growth"

        try:
            from runtime.event_store import EventStore

            store = EventStore()
            current_seq = store.get_latest_seq()
            now_mono = time.monotonic()

            with self._lock:
                cached_seq = self._seq_at_last_check
                cached_ts = self._seq_check_ts

            # Update the cached seq/ts for next run
            with self._lock:
                self._seq_at_last_check = current_seq
                self._seq_check_ts = now_mono

            # Only warn if we have a prior sample and are in trading hours
            if cached_ts > 0 and _is_trading_hours():
                elapsed = now_mono - cached_ts
                stale_threshold = _GROWTH_STALE_MINUTES * 60
                if elapsed >= stale_threshold and current_seq == cached_seq:
                    findings.append(_make_finding(
                        IntegritySeverity.WARNING,
                        subsystem,
                        f"EventStore seq has not grown in {elapsed / 60:.1f} minutes "
                        f"during trading hours (seq={current_seq}). "
                        f"Bot scan loop may be stalled.",
                        remediation_hint=(
                            "Check CryptoComBot scan loop status and exchange connectivity."
                        ),
                    ))

        except Exception as exc:  # noqa: BLE001
            findings.append(_make_finding(
                IntegritySeverity.WARNING,
                subsystem,
                f"EventStore growth check failed to run: {exc}",
                remediation_hint="Verify EventStore accessibility.",
            ))

        return findings, 0, 0

    # ── CRITICAL handling ─────────────────────────────────────────────────────

    def _handle_critical_findings(self, critical_findings: List[IntegrityFinding]) -> None:
        """Perform all CRITICAL-severity side-effects (single alert per scan)."""
        # Build a combined description for the Telegram alert
        summary_lines = []
        for f in critical_findings:
            summary_lines.append(f"  [{f.subsystem}] {f.description}")
        alert_text = (
            f"[IntegrityMonitor] CRITICAL: {len(critical_findings)} finding(s)\n"
            + "\n".join(summary_lines[:5])  # cap at 5 to avoid message overflow
        )

        # Telegram alert (single per scan)
        try:
            from runtime.telegram_alerts import _send
            _send(alert_text)
        except Exception:  # noqa: BLE001
            pass

        for f in critical_findings:
            # Prometheus counter
            try:
                if _prom_integrity_failures is not None:
                    _prom_integrity_failures.labels(subsystem=f.subsystem).inc()
            except Exception:  # noqa: BLE001
                pass

            # Append RECONCILIATION_INCIDENT to EventStore (best-effort)
            try:
                from runtime.event_store import EventStore, EventType
                import uuid as _uuid
                store = EventStore()
                store.append(
                    event_type=EventType.RECONCILIATION_INCIDENT,
                    trace_id=str(_uuid.uuid4()),
                    payload={
                        "source": "IntegrityMonitor",
                        "finding_id": f.finding_id,
                        "subsystem": f.subsystem,
                        "description": f.description,
                        "severity": f.severity.value,
                    },
                )
            except Exception:  # noqa: BLE001
                pass

            # Halt marker
            if self._halt_on_critical and f.auto_halt:
                self._write_halt_marker(f)

            # Persist to governance incidents log
            self._persist_integrity_incident(f)

    def _write_halt_marker(self, finding: IntegrityFinding) -> None:
        """Atomically write data/integrity_halt.json."""
        payload = {
            "halted_at": _now_iso(),
            "source": "IntegrityMonitor",
            "finding_id": finding.finding_id,
            "subsystem": finding.subsystem,
            "description": finding.description,
        }
        dir_name = os.path.dirname(os.path.abspath(_INTEGRITY_HALT_PATH))
        os.makedirs(dir_name, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2)
                os.replace(tmp_path, _INTEGRITY_HALT_PATH)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            logger.critical(
                "integrity_monitor: halt marker written to %s (subsystem=%s)",
                _INTEGRITY_HALT_PATH,
                finding.subsystem,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("integrity_monitor: failed to write halt marker: %s", exc)

    def _persist_integrity_incident(self, finding: IntegrityFinding) -> None:
        """Append a finding to data/governance/logs/integrity_incidents.jsonl (fcntl locked)."""
        record = {
            "ts": finding.detected_at,
            "finding_id": finding.finding_id,
            "severity": finding.severity.value,
            "subsystem": finding.subsystem,
            "description": finding.description,
            "remediation_hint": finding.remediation_hint,
            "auto_halt": finding.auto_halt,
        }
        line = json.dumps(record, sort_keys=True) + "\n"
        try:
            os.makedirs(os.path.dirname(_INTEGRITY_INCIDENTS_PATH), exist_ok=True)
            with open(_INTEGRITY_INCIDENTS_PATH, "a", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    fh.write(line)
                    fh.flush()
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            logger.error(
                "integrity_monitor: failed to persist incident to %s: %s",
                _INTEGRITY_INCIDENTS_PATH,
                exc,
            )


# ── Module-level singleton ────────────────────────────────────────────────────

_monitor: Optional[IntegrityMonitor] = None
_monitor_lock = threading.Lock()


def get_monitor() -> IntegrityMonitor:
    """Return the process-wide IntegrityMonitor singleton.

    Uses double-checked locking; safe to call from any thread.
    """
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            if _monitor is None:
                _monitor = IntegrityMonitor()
    return _monitor
