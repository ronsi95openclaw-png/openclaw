"""SnapshotDaemon — automated EventStore snapshot creation for OpenClaw.

Creates portfolio-state snapshots on two triggers:
  * Sequence-count threshold (every N events, default 10 000)
  * Time threshold (every N hours, default 24)

Both triggers are subject to a cooldown (default 300 s) to prevent thrashing.

Failure handling
----------------
* Consecutive failures increment a counter.
* At 3+ failures: Telegram WARNING alert.
* At max_failures (5): CRITICAL Telegram alert + emergency.jsonl persisted.
* Failed writes NEVER touch existing snapshot files (tmp → replace only).

Recovery rehearsal
------------------
Every 24 h the daemon calls recover_from_latest_snapshot() and logs the result.
If all snapshots are corrupt, a CRITICAL log line is emitted.

Prometheus metrics (best-effort, all wrapped in try/except)
-----------------------------------------------------------
  openclaw_snapshots_total              counter
  openclaw_snapshot_failures_total      counter
  openclaw_snapshot_last_success_ts     gauge
  openclaw_snapshot_integrity_ok        gauge  (1=ok, 0=failed)

Thread-safety
-------------
All mutable state is protected by threading.Lock.
The daemon thread is always daemon=True.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("openclaw.runtime.snapshot_daemon")

# ── Prometheus metric names (module-level singletons, best-effort) ─────────────

_prom_snapshots_total: Any = None
_prom_failures_total: Any = None
_prom_last_success_ts: Any = None
_prom_integrity_ok: Any = None
_prom_init_lock = threading.Lock()
_prom_initialized = False


def _init_prometheus() -> None:
    global _prom_snapshots_total, _prom_failures_total
    global _prom_last_success_ts, _prom_integrity_ok, _prom_initialized
    if _prom_initialized:
        return
    with _prom_init_lock:
        if _prom_initialized:
            return
        try:
            from prometheus_client import Counter, Gauge  # type: ignore[import]
            _prom_snapshots_total = Counter(
                "openclaw_snapshots_total",
                "Total number of successful EventStore snapshots created",
            )
            _prom_failures_total = Counter(
                "openclaw_snapshot_failures_total",
                "Total number of snapshot creation failures",
            )
            _prom_last_success_ts = Gauge(
                "openclaw_snapshot_last_success_ts",
                "Unix timestamp of the last successful snapshot",
            )
            _prom_integrity_ok = Gauge(
                "openclaw_snapshot_integrity_ok",
                "1 if the last snapshot passed integrity check, 0 otherwise",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("snapshot_daemon: prometheus_client unavailable: %s", exc)
        finally:
            _prom_initialized = True


# ── Constants ──────────────────────────────────────────────────────────────────

_EMERGENCY_LOG_PATH = "data/governance/logs/emergency.jsonl"
_BOT_STATE_PATH = "data/cryptocom_state.json"
_LOOP_SLEEP_SECONDS = 60.0
_FORCE_SNAPSHOT_TIMEOUT = 30.0
_RECOVERY_REHEARSAL_INTERVAL = 86400.0  # 24 h
_SHUTDOWN_DRAIN_TIMEOUT = 10.0


# ── SnapshotDaemon ────────────────────────────────────────────────────────────

class SnapshotDaemon:
    """Automated snapshot manager for the OpenClaw EventStore.

    Parameters
    ----------
    snapshot_dir:
        Directory passed to EventSnapshotEngine (default ``data/snapshots``).
    event_store_path:
        Path to the JSONL event store (default ``data/events.jsonl``).
    interval_events:
        Create a snapshot after this many new events since the last snapshot.
    interval_hours:
        Create a snapshot when this many hours have elapsed since the last
        snapshot, regardless of event count.
    cooldown_seconds:
        Minimum seconds that must elapse between any two snapshots.  Enforced
        for both seq-triggered and time-triggered snapshots.
    max_failures:
        Number of consecutive failures before a CRITICAL halt alert is emitted.
    """

    def __init__(
        self,
        snapshot_dir: str = "data/snapshots",
        event_store_path: str = "data/events.jsonl",
        interval_events: int = 10_000,
        interval_hours: float = 24.0,
        cooldown_seconds: float = 300.0,
        max_failures: int = 5,
    ) -> None:
        self._snapshot_dir = snapshot_dir
        self._event_store_path = event_store_path
        self._interval_events = interval_events
        self._interval_seconds = interval_hours * 3600.0
        self._cooldown_seconds = cooldown_seconds
        self._max_failures = max_failures

        # ── Mutable state (all protected by _lock) ─────────────────────────────
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Snapshot tracking
        self._last_snapshot_ts: float = 0.0
        self._last_snapshot_seq: int = 0
        self._last_snapshot_id: Optional[str] = None
        self._consecutive_failures: int = 0
        self._total_snapshots: int = 0
        self._last_failure_reason: Optional[str] = None

        # Recovery rehearsal tracking
        self._last_recovery_rehearsal_ts: float = 0.0

        # Force-snapshot mechanism
        self._force_requested = threading.Event()
        self._force_done = threading.Event()
        self._force_result: Optional[Exception] = None

        # Initialise Prometheus metrics (best-effort)
        try:
            _init_prometheus()
        except Exception:  # noqa: BLE001
            pass

        # Ensure snapshot directory and governance log directory exist
        os.makedirs(snapshot_dir, exist_ok=True)
        os.makedirs(os.path.dirname(_EMERGENCY_LOG_PATH), exist_ok=True)

        # Sync last_snapshot_seq / _ts from existing index so cooldown /
        # interval calculations are correct after a process restart.
        self._sync_from_index()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Launch the background daemon thread (idempotent)."""
        with self._lock:
            if self._running:
                logger.debug("snapshot_daemon: already running — ignoring start()")
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="SnapshotDaemon",
                daemon=True,
            )
            self._running = True
            self._thread.start()
        logger.info("snapshot_daemon: started")

    def stop(self) -> None:
        """Gracefully stop the daemon.

        Signals a pre-shutdown snapshot, sets the stop event, and waits up to
        10 seconds for the thread to join.
        """
        with self._lock:
            if not self._running:
                return
            thread = self._thread

        logger.info("snapshot_daemon: stopping — requesting pre-shutdown snapshot")
        try:
            self.force_snapshot_now()
        except Exception as exc:  # noqa: BLE001
            logger.warning("snapshot_daemon: pre-shutdown snapshot failed: %s", exc)

        self._stop_event.set()
        if thread is not None:
            thread.join(timeout=_SHUTDOWN_DRAIN_TIMEOUT)
            if thread.is_alive():
                logger.warning(
                    "snapshot_daemon: thread did not stop within %ss", _SHUTDOWN_DRAIN_TIMEOUT
                )

        with self._lock:
            self._running = False
        logger.info("snapshot_daemon: stopped")

    def force_snapshot_now(self) -> None:
        """Immediately create a snapshot.  Blocks until done or timeout (30 s).

        Raises RuntimeError if the snapshot fails or times out.
        """
        if not self._running:
            # Daemon not running — execute inline
            self._do_snapshot(force=True)
            return

        with self._lock:
            self._force_result = None
        self._force_done.clear()
        self._force_requested.set()

        if not self._force_done.wait(timeout=_FORCE_SNAPSHOT_TIMEOUT):
            raise RuntimeError(
                f"snapshot_daemon: force_snapshot_now() timed out after {_FORCE_SNAPSHOT_TIMEOUT}s"
            )

        with self._lock:
            err = self._force_result
        if err is not None:
            raise err

    def notify_event_written(self, seq: int) -> None:
        """Called on every EventStore write to check seq-based trigger.

        Thread-safe; never blocks the caller.
        """
        with self._lock:
            last_seq = self._last_snapshot_seq
            last_ts = self._last_snapshot_ts

        if (seq - last_seq) >= self._interval_events:
            if (time.monotonic() - last_ts) >= self._cooldown_seconds:
                # Schedule snapshot on the daemon thread (non-blocking)
                # We signal via force_requested only if daemon is running.
                if self._running:
                    self._force_requested.set()

    def get_status(self) -> dict:
        """Return a point-in-time status dict."""
        with self._lock:
            return {
                "running": self._running,
                "last_snapshot_ts": (
                    datetime.fromtimestamp(self._last_snapshot_ts, tz=timezone.utc).isoformat()
                    if self._last_snapshot_ts > 0
                    else None
                ),
                "last_snapshot_seq": self._last_snapshot_seq,
                "last_snapshot_id": self._last_snapshot_id,
                "consecutive_failures": self._consecutive_failures,
                "total_snapshots": self._total_snapshots,
                "last_failure_reason": self._last_failure_reason,
            }

    # ── Internal: daemon loop ─────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main daemon loop — runs in the background thread."""
        logger.debug("snapshot_daemon: _run_loop started")

        while not self._stop_event.is_set():
            # Wait up to _LOOP_SLEEP_SECONDS or until a force/seq trigger fires
            triggered = self._force_requested.wait(timeout=_LOOP_SLEEP_SECONDS)

            if self._stop_event.is_set():
                break

            # Determine if this wake-up is a force request
            is_force = triggered and self._force_requested.is_set()

            # Check time-based trigger (even if not explicitly forced)
            with self._lock:
                last_ts = self._last_snapshot_ts
                last_seq = self._last_snapshot_seq

            time_trigger = (time.monotonic() - last_ts) >= self._interval_seconds
            should_snap = is_force or time_trigger

            if should_snap:
                # Enforce cooldown (except for explicit force from force_snapshot_now)
                within_cooldown = (time.monotonic() - last_ts) < self._cooldown_seconds
                if within_cooldown and not is_force:
                    logger.debug("snapshot_daemon: snapshot suppressed by cooldown")
                else:
                    self._do_snapshot(force=is_force)

                    if is_force:
                        self._force_requested.clear()
                        self._force_done.set()
                    continue  # skip the clear below — already cleared

            # Not a snapshot cycle; clear force request if it was stale
            if is_force:
                self._force_requested.clear()
                self._force_done.set()

            # Recovery rehearsal (every 24 h on the loop tick)
            with self._lock:
                last_rehearsal = self._last_recovery_rehearsal_ts
            if (time.monotonic() - last_rehearsal) >= _RECOVERY_REHEARSAL_INTERVAL:
                self._recovery_rehearsal()

        logger.debug("snapshot_daemon: _run_loop exiting")

    def _do_snapshot(self, force: bool = False) -> None:
        """Create one snapshot.  Updates state and handles all failure paths."""
        label = "forced" if force else "scheduled"
        logger.info("snapshot_daemon: creating %s snapshot", label)

        try:
            from runtime.event_snapshot import EventSnapshotEngine
            from runtime.event_store import EventStore

            store = EventStore(store_path=self._event_store_path)
            current_seq = store.get_latest_seq()
            portfolio_state = self._build_portfolio_state(current_seq)

            engine = EventSnapshotEngine(snapshot_dir=self._snapshot_dir)
            meta = engine.force_snapshot(portfolio_state, current_seq)

            # ── Integrity verification ─────────────────────────────────────────
            ok = engine.verify_snapshot(meta)
            if not ok:
                # Delete the bad file and count as failure
                bad_path = os.path.join(
                    self._snapshot_dir, f"{meta.snapshot_id}.snap.gz"
                )
                try:
                    if os.path.exists(bad_path):
                        os.remove(bad_path)
                except OSError:
                    pass
                self._on_failure(
                    RuntimeError(
                        f"integrity check failed for snapshot {meta.snapshot_id}"
                    )
                )
                try:
                    from runtime.telegram_alerts import _send
                    _send(
                        f"[SnapshotDaemon] CRITICAL: snapshot integrity check failed "
                        f"(id={meta.snapshot_id}, seq={current_seq})"
                    )
                except Exception:  # noqa: BLE001
                    pass
                # Prometheus integrity gauge → 0
                try:
                    if _prom_integrity_ok is not None:
                        _prom_integrity_ok.set(0)
                except Exception:  # noqa: BLE001
                    pass

                if force:
                    with self._lock:
                        self._force_result = RuntimeError(
                            "Snapshot integrity check failed"
                        )
                return

            # ── Success path ───────────────────────────────────────────────────
            now_mono = time.monotonic()
            now_unix = time.time()

            with self._lock:
                self._last_snapshot_ts = now_mono
                self._last_snapshot_seq = current_seq
                self._last_snapshot_id = meta.snapshot_id
                self._consecutive_failures = 0
                self._last_failure_reason = None
                self._total_snapshots += 1

            logger.info(
                "snapshot_daemon: snapshot %s created (seq=%d)",
                meta.snapshot_id,
                current_seq,
            )

            # Delete old snapshots (keep 10)
            try:
                engine.delete_old_snapshots(keep_n=10)
            except Exception as exc:  # noqa: BLE001
                logger.warning("snapshot_daemon: delete_old_snapshots failed: %s", exc)

            # Prometheus metrics (best-effort)
            try:
                if _prom_snapshots_total is not None:
                    _prom_snapshots_total.inc()
                if _prom_last_success_ts is not None:
                    _prom_last_success_ts.set(now_unix)
                if _prom_integrity_ok is not None:
                    _prom_integrity_ok.set(1)
            except Exception:  # noqa: BLE001
                pass

            if force:
                with self._lock:
                    self._force_result = None

        except Exception as exc:  # noqa: BLE001
            self._on_failure(exc)
            if force:
                with self._lock:
                    self._force_result = exc

    def _on_failure(self, exc: Exception) -> None:
        """Handle a snapshot failure: log, alert, persist if critical."""
        reason = str(exc)
        with self._lock:
            self._consecutive_failures += 1
            self._last_failure_reason = reason
            failures = self._consecutive_failures

        logger.warning(
            "snapshot_daemon: snapshot failed (consecutive=%d): %s", failures, reason
        )

        # Prometheus counter (best-effort)
        try:
            if _prom_failures_total is not None:
                _prom_failures_total.inc()
        except Exception:  # noqa: BLE001
            pass

        # Telegram alert at 3+ consecutive failures
        if failures >= 3:
            try:
                from runtime.telegram_alerts import _send
                _send(
                    f"[SnapshotDaemon] WARNING: {failures} consecutive snapshot failures.\n"
                    f"Last error: {reason}"
                )
            except Exception:  # noqa: BLE001
                pass

        # CRITICAL halt if at max_failures
        if failures >= self._max_failures:
            logger.critical(
                "snapshot_daemon: reached %d consecutive failures — emitting CRITICAL alert",
                failures,
            )
            try:
                from runtime.telegram_alerts import _send
                _send(
                    f"[SnapshotDaemon] CRITICAL: {failures} consecutive snapshot failures!\n"
                    f"Snapshot system may be DOWN. Manual intervention required.\n"
                    f"Last error: {reason}"
                )
            except Exception:  # noqa: BLE001
                pass

            # Persist incident to governance emergency log
            self._persist_emergency_incident(failures, reason)

    def _persist_emergency_incident(self, failure_count: int, reason: str) -> None:
        """Append a CRITICAL incident record to data/governance/logs/emergency.jsonl."""
        incident = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "SnapshotDaemon",
            "severity": "CRITICAL",
            "reason": f"Snapshot system: {failure_count} consecutive failures",
            "last_error": reason,
            "consecutive_failures": failure_count,
        }
        line = json.dumps(incident, sort_keys=True) + "\n"
        try:
            os.makedirs(os.path.dirname(_EMERGENCY_LOG_PATH), exist_ok=True)
            with open(_EMERGENCY_LOG_PATH, "a", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    fh.write(line)
                    fh.flush()
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            logger.error(
                "snapshot_daemon: failed to persist emergency incident: %s", exc
            )

    # ── Recovery rehearsal ────────────────────────────────────────────────────

    def _recovery_rehearsal(self) -> None:
        """Attempt a dry-run recovery from the latest snapshot and log outcome."""
        logger.info("snapshot_daemon: running recovery rehearsal")
        with self._lock:
            self._last_recovery_rehearsal_ts = time.monotonic()

        try:
            from runtime.event_snapshot import EventSnapshotEngine

            engine = EventSnapshotEngine(snapshot_dir=self._snapshot_dir)
            meta, warnings = engine.recover_from_latest_snapshot()

            if meta is None:
                logger.critical(
                    "snapshot_daemon: recovery rehearsal FAILED — all snapshots are "
                    "corrupt or missing. Warnings: %s",
                    warnings,
                )
            else:
                if warnings:
                    logger.warning(
                        "snapshot_daemon: recovery rehearsal succeeded with warnings "
                        "(snapshot=%s, seq=%d): %s",
                        meta.snapshot_id,
                        meta.seq_at_snapshot,
                        warnings,
                    )
                else:
                    logger.info(
                        "snapshot_daemon: recovery rehearsal OK (snapshot=%s, seq=%d)",
                        meta.snapshot_id,
                        meta.seq_at_snapshot,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.error("snapshot_daemon: recovery rehearsal error: %s", exc)

    # ── Portfolio state builder ───────────────────────────────────────────────

    def _build_portfolio_state(self, event_count: int) -> dict:
        """Read latest capital state from data/cryptocom_state.json if it exists.

        Returns a dict compatible with EventSnapshotEngine.force_snapshot().
        """
        state: Dict[str, Any] = {
            "capital_state": "UNKNOWN",
            "open_positions": {},
            "realized_pnl": 0.0,
            "active_halt": False,
            "halt_reason": "",
            "event_count": event_count,
            "execution_failures": 0,
            "strategy_weights": {},
        }

        try:
            if os.path.exists(_BOT_STATE_PATH):
                with open(_BOT_STATE_PATH, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)

                state["capital_state"] = str(raw.get("capital_state", "UNKNOWN"))
                state["realized_pnl"] = float(raw.get("realized_pnl", 0.0))
                state["active_halt"] = bool(raw.get("active_halt", False))
                state["halt_reason"] = str(raw.get("halt_reason", ""))
                state["execution_failures"] = int(raw.get("execution_failures", 0))

                # open_positions: accept list or dict; normalise to dict keyed by trace_id
                raw_pos = raw.get("open_positions", {})
                if isinstance(raw_pos, list):
                    state["open_positions"] = {
                        p.get("trace_id", f"pos_{i}"): p
                        for i, p in enumerate(raw_pos)
                    }
                elif isinstance(raw_pos, dict):
                    state["open_positions"] = raw_pos

                # Strategy weights
                raw_weights = raw.get("strategy_weights", {})
                if isinstance(raw_weights, dict):
                    state["strategy_weights"] = raw_weights

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "snapshot_daemon: could not read bot state (%s) — using defaults", exc
            )

        # Also attempt to read strategy_weights.json for richer data
        try:
            weights_path = "data/strategy_weights.json"
            if os.path.exists(weights_path) and not state["strategy_weights"]:
                with open(weights_path, "r", encoding="utf-8") as fh:
                    w = json.load(fh)
                if isinstance(w, dict):
                    state["strategy_weights"] = w
        except Exception:  # noqa: BLE001
            pass

        return state

    # ── Startup sync ──────────────────────────────────────────────────────────

    def _sync_from_index(self) -> None:
        """Sync last_snapshot_seq and last_snapshot_ts from the snapshot index.

        Called once at __init__ so cooldown / interval calculations are correct
        after a process restart.
        """
        try:
            from runtime.event_snapshot import EventSnapshotEngine

            engine = EventSnapshotEngine(snapshot_dir=self._snapshot_dir)
            snapshots = engine.list_snapshots()
            if not snapshots:
                return

            latest = snapshots[0]  # list_snapshots() returns newest-first
            with self._lock:
                self._last_snapshot_seq = latest.seq_at_snapshot
                self._last_snapshot_id = latest.snapshot_id
                try:
                    dt = datetime.fromisoformat(latest.created_at)
                    self._last_snapshot_ts = dt.timestamp()
                except ValueError:
                    pass

            logger.debug(
                "snapshot_daemon: synced from index — last_seq=%d, last_id=%s",
                self._last_snapshot_seq,
                self._last_snapshot_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("snapshot_daemon: could not sync from index: %s", exc)


# ── Module-level singleton ────────────────────────────────────────────────────

_daemon: Optional[SnapshotDaemon] = None
_daemon_lock = threading.Lock()


def get_daemon() -> SnapshotDaemon:
    """Return the process-wide SnapshotDaemon singleton.

    Uses double-checked locking; safe to call from any thread.
    """
    global _daemon
    if _daemon is None:
        with _daemon_lock:
            if _daemon is None:
                _daemon = SnapshotDaemon()
    return _daemon
