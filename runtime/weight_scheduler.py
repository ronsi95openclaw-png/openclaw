"""Midnight weight application daemon.

Reads the newest data/optimization/analysis_*.json at UTC midnight (or on demand),
applies weight_adjustments to data/strategy_weights.json with bounds enforcement,
snapshots prior weights, and appends to data/weight_adjustments_audit.jsonl.

The bot's existing _auto_apply_opus_weights() runs at the day-boundary inside the
scan loop. This module is additive — it provides proper midnight scheduling,
idempotent application (tracked by analysis file mtime), snapshotting, and a
structured audit trail.

Threading model: single daemon thread, threading.Event for clean stop.
Sleep in 30-second increments so stop() is responsive.
"""
from __future__ import annotations

import fcntl
import glob
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("openclaw.runtime.weight_scheduler")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS_PATH   = "data/strategy_weights.json"
_DEFAULT_ANALYSIS_DIR   = "data/optimization"
_DEFAULT_AUDIT_PATH     = "data/weight_adjustments_audit.jsonl"
_DEFAULT_SNAPSHOTS_DIR  = "data/weight_snapshots"

_MIN_WEIGHT = 0.1
_MAX_WEIGHT = 2.0

# How long to sleep per increment while waiting for midnight
_SLEEP_CHUNK_S = 30.0


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class WeightAdjustmentRecord:
    """Full audit record for a single weight-application cycle."""
    trace_id:        str            # UUID4
    ts:              str            # ISO8601 UTC timestamp of application
    analysis_file:   str            # filename (basename) of analysis JSON used
    analysis_mtime:  str            # mtime of analysis file as ISO8601 UTC (idempotency key)
    applied:         dict           # {strategy: {"old": float, "new": float}}
    skipped:         list           # strategies in adjustments but not in weights file
    rejected:        list           # strategies rejected (out-of-bounds factor, malformed)
    dry_run:         bool
    demo_mode:       bool
    checksum_before: str            # sha256 of weights JSON before application
    checksum_after:  str            # sha256 of weights JSON after application
    snapshot_path:   str            # absolute path to pre-application snapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seconds_until_midnight() -> float:
    """Return seconds from now until the next UTC midnight."""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (tomorrow - now).total_seconds()


def _weights_checksum(weights: dict) -> str:
    """SHA256 of deterministically serialised weights dict."""
    payload = json.dumps(weights, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _mtime_iso(path: str) -> str:
    """Return file mtime as an ISO8601 UTC string, or empty string on error."""
    try:
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    except OSError:
        return ""


def _newest_analysis_file(analysis_dir: str) -> Optional[str]:
    """Return the path to the newest analysis_*.json in analysis_dir, or None."""
    pattern = os.path.join(analysis_dir, "analysis_*.json")
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def _read_weights(weights_path: str) -> Optional[dict]:
    """Read strategy weights JSON. Returns None on any error."""
    try:
        with open(weights_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("weight_scheduler: cannot read weights file %s: %s", weights_path, exc)
        return None


def _write_weights_atomic(weights_path: str, weights: dict) -> None:
    """Atomically write weights dict to weights_path (tmp + fsync + os.replace).

    Uses fcntl.LOCK_EX on the temp file. os.replace is atomic on POSIX.
    """
    weights_dir = os.path.dirname(os.path.abspath(weights_path))
    os.makedirs(weights_dir, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=weights_dir, prefix=".weights_tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.write(json.dumps(weights, indent=2) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        os.replace(tmp_path, weights_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _append_audit_record(audit_path: str, record: WeightAdjustmentRecord) -> None:
    """Append a WeightAdjustmentRecord to the audit JSONL via fcntl.LOCK_EX.

    Uses a tempfile to build the line, then appends atomically.
    """
    audit_dir = os.path.dirname(os.path.abspath(audit_path))
    os.makedirs(audit_dir, exist_ok=True)

    line = json.dumps(asdict(record), separators=(",", ":")) + "\n"

    fd, tmp_path = tempfile.mkstemp(dir=audit_dir, prefix=".audit_tmp_", suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

        # Append tmp content to the audit log
        with open(audit_path, "ab") as dest_fh:
            fcntl.flock(dest_fh.fileno(), fcntl.LOCK_EX)
            with open(tmp_path, "rb") as src_fh:
                dest_fh.write(src_fh.read())
            dest_fh.flush()
            os.fsync(dest_fh.fileno())
            fcntl.flock(dest_fh.fileno(), fcntl.LOCK_UN)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Main daemon class
# ---------------------------------------------------------------------------

class WeightApplicationDaemon:
    """Background thread that applies Claude Opus weight adjustments at UTC midnight.

    Parameters
    ----------
    weights_path:
        Path to strategy_weights.json (relative to CWD or absolute).
    analysis_dir:
        Directory containing analysis_*.json files from Claude Opus.
    audit_path:
        Path to the JSONL audit log.
    snapshots_dir:
        Directory where pre-application weight snapshots are stored.
    min_weight:
        Lower bound for any strategy weight after adjustment (default: 0.1).
    max_weight:
        Upper bound for any strategy weight after adjustment (default: 2.0).
    demo_mode:
        When True, adjustments are applied but logged as demo. Mirrors bot state.
    dry_run:
        When True, compute what would be applied but do NOT write weights.
        Still appends an audit record with dry_run=True.
    """

    def __init__(
        self,
        weights_path: str = _DEFAULT_WEIGHTS_PATH,
        analysis_dir: str = _DEFAULT_ANALYSIS_DIR,
        audit_path: str = _DEFAULT_AUDIT_PATH,
        snapshots_dir: str = _DEFAULT_SNAPSHOTS_DIR,
        min_weight: float = _MIN_WEIGHT,
        max_weight: float = _MAX_WEIGHT,
        demo_mode: bool = True,
        dry_run: bool = False,
    ) -> None:
        self._weights_path  = weights_path
        self._analysis_dir  = analysis_dir
        self._audit_path    = audit_path
        self._snapshots_dir = snapshots_dir
        self._min_weight    = min_weight
        self._max_weight    = max_weight
        self._demo_mode     = demo_mode
        self._dry_run       = dry_run

        self._stop_event = threading.Event()
        self._lock       = threading.Lock()
        self._thread: Optional[threading.Thread] = None

        # Idempotency: track mtime of the last analysis file we processed
        self._last_applied_mtime: Optional[str] = None
        self._last_record: Optional[WeightAdjustmentRecord] = None

        # Ensure snapshots directory exists at init time
        Path(snapshots_dir).mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the daemon thread if not already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.debug("weight_scheduler: daemon already running; ignoring start()")
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="WeightApplicationDaemon",
                daemon=True,
            )
            self._thread.start()
        logger.info(
            "weight_scheduler: daemon started (demo=%s dry_run=%s)",
            self._demo_mode, self._dry_run,
        )

    def stop(self, timeout_s: float = 5.0) -> None:
        """Signal the daemon to stop and join with timeout."""
        self._stop_event.set()
        thread: Optional[threading.Thread] = None
        with self._lock:
            thread = self._thread

        if thread is not None:
            thread.join(timeout=timeout_s)

        with self._lock:
            self._thread = None

        logger.info("weight_scheduler: daemon stopped")

    def is_running(self) -> bool:
        """Return True if the daemon thread is alive."""
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def apply_now(self, force: bool = False) -> Optional[WeightAdjustmentRecord]:
        """Apply weights immediately in the calling thread.

        Used for testing and manual triggering without waiting for midnight.

        Args:
            force: If True, apply even if the analysis file mtime is unchanged
                   (i.e., re-apply the same file). Default False (idempotent).

        Returns:
            WeightAdjustmentRecord on success, None if nothing to apply.
        """
        return self._apply(force=force)

    def get_last_record(self) -> Optional[WeightAdjustmentRecord]:
        """Return the last successfully applied WeightAdjustmentRecord, or None."""
        with self._lock:
            return self._last_record

    def get_status(self) -> dict:
        """Return a status dict suitable for dashboard/telemetry."""
        with self._lock:
            last = self._last_record
            running = self._thread is not None and self._thread.is_alive()
            return {
                "running":             running,
                "demo_mode":           self._demo_mode,
                "dry_run":             self._dry_run,
                "last_applied_mtime":  self._last_applied_mtime,
                "last_trace_id":       last.trace_id if last else None,
                "last_ts":             last.ts if last else None,
                "last_analysis_file":  last.analysis_file if last else None,
                "last_applied":        last.applied if last else {},
                "last_skipped":        last.skipped if last else [],
                "last_rejected":       last.rejected if last else [],
                "weights_path":        self._weights_path,
                "analysis_dir":        self._analysis_dir,
                "snapshots_dir":       self._snapshots_dir,
                "min_weight":          self._min_weight,
                "max_weight":          self._max_weight,
            }

    # ── Internal loop ──────────────────────────────────────────────────────────

    def _run(self) -> None:
        """Main daemon loop.

        Sleeps until next UTC midnight in 30-second increments (so stop() is
        responsive). After applying, sleeps until the following midnight.
        All exceptions are caught — this thread must never crash.
        """
        logger.debug("weight_scheduler: daemon thread entering main loop")
        while not self._stop_event.is_set():
            wait_s = _seconds_until_midnight()
            logger.debug(
                "weight_scheduler: next application in %.0f seconds (%.2f hours)",
                wait_s, wait_s / 3600.0,
            )

            # Sleep in _SLEEP_CHUNK_S increments so stop() works promptly
            elapsed = 0.0
            while elapsed < wait_s and not self._stop_event.is_set():
                chunk = min(_SLEEP_CHUNK_S, wait_s - elapsed)
                time.sleep(chunk)
                elapsed += chunk

            if self._stop_event.is_set():
                break

            # Midnight: apply
            try:
                record = self._apply(force=False)
                if record:
                    logger.info(
                        "weight_scheduler: midnight apply done — "
                        "applied=%d skipped=%d rejected=%d trace_id=%s",
                        len(record.applied),
                        len(record.skipped),
                        len(record.rejected),
                        record.trace_id,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "weight_scheduler: unhandled exception in midnight apply: %s",
                    exc, exc_info=True,
                )

            # Sleep a few seconds to avoid re-triggering at exactly midnight
            if not self._stop_event.is_set():
                time.sleep(5.0)

        logger.debug("weight_scheduler: daemon thread exiting")

    def _apply(self, force: bool = False) -> Optional[WeightAdjustmentRecord]:
        """Core weight-application logic.

        1. Find the newest analysis file. Skip if none.
        2. Check idempotency: skip if same mtime already applied (unless force).
        3. Parse weight_adjustments from the analysis JSON.
        4. Read current weights.
        5. Snapshot current weights.
        6. Apply adjustments with bounds enforcement.
        7. Write new weights (unless dry_run).
        8. Append audit record.
        9. Return WeightAdjustmentRecord.

        Returns None if there is nothing to apply (no analysis file, nothing
        changed, analysis file empty/malformed).
        """
        trace_id = str(uuid.uuid4())
        now_iso  = datetime.now(timezone.utc).isoformat()

        # Step 1: Find newest analysis file
        analysis_path = _newest_analysis_file(self._analysis_dir)
        if not analysis_path:
            logger.debug("weight_scheduler: no analysis_*.json files in %s — skipping", self._analysis_dir)
            return None

        analysis_basename = os.path.basename(analysis_path)
        analysis_mtime    = _mtime_iso(analysis_path)

        # Step 2: Idempotency check
        if not force:
            with self._lock:
                last_mtime = self._last_applied_mtime
            if last_mtime and last_mtime == analysis_mtime:
                logger.debug(
                    "weight_scheduler: analysis file %s already applied (mtime=%s) — skipping",
                    analysis_basename, analysis_mtime,
                )
                return None

        # Step 3: Parse analysis JSON
        try:
            with open(analysis_path, "r", encoding="utf-8") as fh:
                report = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "weight_scheduler: cannot read/parse %s: %s — skipping",
                analysis_path, exc,
            )
            return None

        if not isinstance(report, dict):
            logger.warning(
                "weight_scheduler: %s is not a JSON object — skipping",
                analysis_path,
            )
            return None

        adjustments = report.get("weight_adjustments", {})
        if not adjustments or not isinstance(adjustments, dict):
            logger.debug(
                "weight_scheduler: %s has no 'weight_adjustments' — skipping",
                analysis_basename,
            )
            return None

        # Step 4: Read current weights
        current_weights = _read_weights(self._weights_path)
        if current_weights is None:
            logger.warning(
                "weight_scheduler: cannot read weights file %s — skipping apply",
                self._weights_path,
            )
            return None

        checksum_before = _weights_checksum(current_weights)

        # Step 5: Snapshot current weights (before any modification)
        snapshot_path = self._snapshot_weights(current_weights, now_iso)

        # Step 6: Apply adjustments with bounds
        new_weights = {}
        for strategy, raw_data in current_weights.items():
            # Weights file stores either a dict (with 'weight' key) or a float
            if isinstance(raw_data, dict):
                new_weights[strategy] = dict(raw_data)
            else:
                # Flat float fallback
                new_weights[strategy] = raw_data

        applied:  dict = {}
        skipped:  list = []
        rejected: list = []

        for strategy, factor in adjustments.items():
            if strategy not in current_weights:
                skipped.append(strategy)
                continue

            # Validate factor
            try:
                factor_f = float(factor)
            except (TypeError, ValueError) as exc:
                rejected.append(strategy)
                logger.warning(
                    "weight_scheduler: reject %s factor=%r — not a number: %s",
                    strategy, factor, exc,
                )
                continue

            if not (0.01 <= factor_f <= 100.0):
                rejected.append(strategy)
                logger.warning(
                    "weight_scheduler: reject %s factor=%.4f — outside sane range [0.01, 100.0]",
                    strategy, factor_f,
                )
                continue

            # Extract old weight
            raw_data = current_weights[strategy]
            if isinstance(raw_data, dict):
                old_w = float(raw_data.get("weight", 1.0))
            else:
                old_w = float(raw_data)

            new_w = max(self._min_weight, min(self._max_weight, old_w * factor_f))
            new_w = round(new_w, 4)

            # Apply to new_weights
            if isinstance(new_weights[strategy], dict):
                new_weights[strategy]["weight"] = new_w
            else:
                new_weights[strategy] = new_w

            applied[strategy] = {"old": round(old_w, 4), "new": new_w}

        if not applied:
            logger.info(
                "weight_scheduler: %s — no applicable adjustments "
                "(skipped=%d rejected=%d)",
                analysis_basename, len(skipped), len(rejected),
            )
            # Still build a record so the audit trail is complete
            checksum_after = checksum_before  # unchanged
        else:
            checksum_after = _weights_checksum(new_weights)

        # Step 7: Write new weights (unless dry_run or nothing applied)
        if applied and not self._dry_run:
            try:
                _write_weights_atomic(self._weights_path, new_weights)
                logger.info(
                    "weight_scheduler: wrote updated weights to %s (%d change(s))",
                    self._weights_path, len(applied),
                )
            except Exception as exc:
                logger.error(
                    "weight_scheduler: failed to write weights: %s", exc, exc_info=True
                )
                # Do not update idempotency key — allow retry
                return None
        elif self._dry_run and applied:
            logger.info(
                "weight_scheduler: DRY RUN — would apply %d change(s): %s",
                len(applied), applied,
            )

        # Step 8: Build and append audit record
        record = WeightAdjustmentRecord(
            trace_id=trace_id,
            ts=now_iso,
            analysis_file=analysis_basename,
            analysis_mtime=analysis_mtime,
            applied=applied,
            skipped=skipped,
            rejected=rejected,
            dry_run=self._dry_run,
            demo_mode=self._demo_mode,
            checksum_before=checksum_before,
            checksum_after=checksum_after,
            snapshot_path=snapshot_path,
        )

        try:
            _append_audit_record(self._audit_path, record)
        except Exception as exc:
            logger.error(
                "weight_scheduler: failed to write audit record: %s", exc, exc_info=True
            )

        # Step 9: Update in-memory idempotency key
        with self._lock:
            self._last_applied_mtime = analysis_mtime
            self._last_record = record

        return record

    def _snapshot_weights(self, weights: dict, ts_iso: str) -> str:
        """Copy current weights to snapshots_dir/weights_YYYYMMDD_HHMMSS.json.

        Returns the absolute path to the snapshot file.
        """
        # Convert ISO timestamp to a filename-safe string
        ts_safe = ts_iso.replace(":", "").replace("-", "").replace("+", "").replace(".", "")[:15]
        snapshot_name = f"weights_{ts_safe}.json"
        snapshot_path = os.path.abspath(
            os.path.join(self._snapshots_dir, snapshot_name)
        )

        Path(self._snapshots_dir).mkdir(parents=True, exist_ok=True)

        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=self._snapshots_dir,
                prefix=".snapshot_tmp_",
                suffix=".json",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                    fh.write(json.dumps(weights, indent=2) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                os.replace(tmp_path, snapshot_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as exc:
            logger.warning(
                "weight_scheduler: failed to snapshot weights: %s — continuing", exc
            )
            return ""

        logger.debug("weight_scheduler: snapshot written to %s", snapshot_path)
        return snapshot_path


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------

_scheduler_instance: Optional[WeightApplicationDaemon] = None
_scheduler_lock = threading.Lock()


def get_weight_scheduler(
    demo_mode: bool = True,
    dry_run: bool = False,
) -> WeightApplicationDaemon:
    """Return the process-singleton WeightApplicationDaemon (create if needed).

    Uses double-checked locking to avoid race conditions on first call.
    The daemon is NOT automatically started; call .start() explicitly.

    Args:
        demo_mode: Passed to the daemon on creation (ignored on subsequent calls).
        dry_run: When True, weights are computed but not written to disk.

    Returns:
        The singleton WeightApplicationDaemon instance.
    """
    global _scheduler_instance
    if _scheduler_instance is None:
        with _scheduler_lock:
            if _scheduler_instance is None:
                _scheduler_instance = WeightApplicationDaemon(
                    demo_mode=demo_mode,
                    dry_run=dry_run,
                )
    return _scheduler_instance
