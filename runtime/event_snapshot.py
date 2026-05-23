"""EventStore snapshot/checkpoint system for OpenClaw.

Snapshots allow replay to resume from a checkpoint rather than replaying from
event 0.  A snapshot captures the full portfolio state at a given event
sequence number and is verified via SHA-256 checksum.

Snapshot triggers
-----------------
A snapshot is created when EITHER of the following is true:
    * The event sequence has advanced by ≥ snapshot_interval_events since the
      last snapshot.
    * More than snapshot_interval_hours hours have elapsed since the last
      snapshot.

Storage
-------
Each snapshot is serialised to JSON, gzip-compressed, and written atomically
via a tmp-file + os.replace pattern.  A human-readable index file
(``index.jsonl``) is maintained in the snapshot directory and is locked with
fcntl to prevent concurrent corruption.

Thread-safety
-------------
All mutable state is protected by a threading.Lock.  Snapshot writes never
propagate exceptions to the caller — all failures are caught and logged.

Checksum
--------
SHA-256 of ``json.dumps(meta_dict_without_checksum, sort_keys=True)``.
The "checksum" key is excluded before hashing so the hash is stable.
"""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import logging
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("openclaw.runtime.event_snapshot")


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class SnapshotMetadata:
    """Complete state capture at a single event sequence number."""
    snapshot_id: str                    # UUID4 string
    created_at: str                     # ISO-8601 UTC
    seq_at_snapshot: int                # event sequence number
    capital_state: str                  # e.g. "SAFE", "DEFENSIVE", "CRITICAL", "HALT"
    open_positions: dict                # {instrument: {side, qty, entry_price, ...}}
    realized_pnl: float
    strategy_weights: dict              # {strategy_name: weight}
    execution_failures: int
    active_halt: bool
    halt_reason: str
    event_count_at_snap: int
    checksum: str = ""                  # set after construction

    @classmethod
    def from_dict(cls, d: dict) -> "SnapshotMetadata":
        return cls(
            snapshot_id=d["snapshot_id"],
            created_at=d["created_at"],
            seq_at_snapshot=int(d["seq_at_snapshot"]),
            capital_state=str(d["capital_state"]),
            open_positions=dict(d.get("open_positions", {})),
            realized_pnl=float(d["realized_pnl"]),
            strategy_weights=dict(d.get("strategy_weights", {})),
            execution_failures=int(d["execution_failures"]),
            active_halt=bool(d["active_halt"]),
            halt_reason=str(d["halt_reason"]),
            event_count_at_snap=int(d["event_count_at_snap"]),
            checksum=str(d.get("checksum", "")),
        )


# ── Engine ────────────────────────────────────────────────────────────────────

class EventSnapshotEngine:
    """Creates, stores, verifies, and recovers EventStore snapshots.

    Parameters
    ----------
    snapshot_dir:
        Directory where ``.snap.gz`` files and ``index.jsonl`` are stored.
    snapshot_interval_events:
        Create a snapshot every N events (default 10 000).
    snapshot_interval_hours:
        Create a snapshot every N hours regardless of event count (default 24).
    """

    def __init__(
        self,
        snapshot_dir: str = "data/snapshots",
        snapshot_interval_events: int = 10_000,
        snapshot_interval_hours: float = 24.0,
    ) -> None:
        self._snapshot_dir = snapshot_dir
        self._interval_events = snapshot_interval_events
        self._interval_seconds = snapshot_interval_hours * 3600.0
        self._lock = threading.Lock()

        # Track last snapshot for trigger calculations
        self._last_snap_seq: int = 0
        self._last_snap_ts: float = 0.0  # Unix timestamp

        os.makedirs(snapshot_dir, exist_ok=True)
        self._sync_last_from_index()

    # ── Public API ────────────────────────────────────────────────────────────

    def maybe_snapshot(
        self,
        current_seq: int,
        portfolio_state: dict,
    ) -> Optional[SnapshotMetadata]:
        """Create a snapshot if either trigger condition is met.

        Returns the SnapshotMetadata on success, or None if neither trigger
        fired or the write failed.
        """
        import time

        with self._lock:
            event_trigger = (current_seq - self._last_snap_seq) >= self._interval_events
            time_trigger = (time.time() - self._last_snap_ts) >= self._interval_seconds

        if not (event_trigger or time_trigger):
            return None

        reason = "event_count" if event_trigger else "time_elapsed"
        logger.debug(
            "event_snapshot: trigger=%s seq=%d, creating snapshot", reason, current_seq
        )
        return self.force_snapshot(portfolio_state, current_seq)

    def force_snapshot(
        self,
        portfolio_state: dict,
        current_seq: int,
    ) -> SnapshotMetadata:
        """Always create a snapshot regardless of trigger state.

        Returns the created SnapshotMetadata.  On write failure the metadata
        is still returned (the checksum is valid) but an error is logged.
        """
        meta = self._build_metadata(portfolio_state, current_seq)
        self._write_snapshot(meta)
        with self._lock:
            self._last_snap_seq = current_seq
            import time
            self._last_snap_ts = time.time()
        return meta

    def load_latest_snapshot(self) -> Optional[SnapshotMetadata]:
        """Return the most recent valid snapshot, or None if none exist."""
        snapshots = self.list_snapshots()
        if not snapshots:
            return None
        return snapshots[0]

    def load_snapshot(self, snapshot_id: str) -> Optional[SnapshotMetadata]:
        """Load and return a specific snapshot by ID.

        Returns None if the snapshot file does not exist or cannot be parsed.
        """
        path = os.path.join(self._snapshot_dir, f"{snapshot_id}.snap.gz")
        if not os.path.exists(path):
            logger.warning("event_snapshot: snapshot file not found: %s", path)
            return None
        try:
            return self._read_snap_file(path)
        except Exception as exc:  # noqa: BLE001
            logger.error("event_snapshot: failed to load %s: %s", snapshot_id, exc)
            return None

    def verify_snapshot(self, meta: SnapshotMetadata) -> bool:
        """Re-read the snapshot file from disk and verify its checksum.

        Returns True only if the file is readable AND its checksum matches.
        """
        try:
            snap_path = os.path.join(self._snapshot_dir, f"{meta.snapshot_id}.snap.gz")
            disk_meta = self._read_snap_file(snap_path)
            expected = self._compute_checksum(asdict(disk_meta))
            return expected == disk_meta.checksum
        except Exception:
            return False

    def list_snapshots(self) -> List[SnapshotMetadata]:
        """Return all snapshots from the index, newest first.

        Snapshots that cannot be read are silently skipped.
        """
        entries = self._read_index()
        results: List[SnapshotMetadata] = []
        for entry in reversed(entries):  # index is oldest-first
            snap_id = entry.get("snapshot_id", "")
            path = os.path.join(self._snapshot_dir, f"{snap_id}.snap.gz")
            if not os.path.exists(path):
                continue
            try:
                meta = self._read_snap_file(path)
                results.append(meta)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "event_snapshot: skipping unreadable snapshot %s: %s", snap_id, exc
                )
        return results

    def delete_old_snapshots(self, keep_n: int = 5) -> None:
        """Keep only the *keep_n* most recent snapshots; delete the rest atomically."""
        snapshots = self.list_snapshots()  # newest first
        to_keep = {s.snapshot_id for s in snapshots[:keep_n]}
        to_delete = [s for s in snapshots if s.snapshot_id not in to_keep]

        for meta in to_delete:
            path = os.path.join(self._snapshot_dir, f"{meta.snapshot_id}.snap.gz")
            try:
                os.remove(path)
                logger.debug("event_snapshot: deleted old snapshot %s", meta.snapshot_id)
            except OSError as exc:
                logger.warning(
                    "event_snapshot: could not delete %s: %s", meta.snapshot_id, exc
                )

        # Rewrite the index to only include surviving snapshots
        surviving_ids = to_keep
        all_entries = self._read_index()
        surviving_entries = [e for e in all_entries if e.get("snapshot_id") in surviving_ids]
        self._rewrite_index(surviving_entries)

    def recover_from_latest_snapshot(
        self,
    ) -> Tuple[Optional[SnapshotMetadata], List[str]]:
        """Attempt recovery from the most recent valid snapshot.

        Walks through ALL index entries newest-first (including unreadable ones),
        verifying each.  Returns the first valid snapshot and a list of warnings
        accumulated during the search.  If all snapshots are corrupt or missing,
        returns (None, ["All snapshots corrupt or missing"]).
        """
        entries = self._read_index()
        warnings: List[str] = []

        if not entries:
            return None, ["All snapshots corrupt or missing"]

        for entry in reversed(entries):  # index is oldest-first; walk newest-first
            snap_id = entry.get("snapshot_id", "")
            path = os.path.join(self._snapshot_dir, f"{snap_id}.snap.gz")

            if not os.path.exists(path):
                msg = f"Snapshot file missing: {snap_id}"
                logger.warning("event_snapshot: %s", msg)
                warnings.append(msg)
                continue

            try:
                disk_meta = self._read_snap_file(path)
            except Exception as exc:
                msg = f"Corrupt snapshot {snap_id}: {exc}"
                logger.warning("event_snapshot: %s", msg)
                warnings.append(msg)
                continue

            expected = self._compute_checksum(asdict(disk_meta))
            if expected != disk_meta.checksum:
                msg = f"Checksum mismatch for snapshot {snap_id} (seq={disk_meta.seq_at_snapshot})"
                logger.warning("event_snapshot: %s", msg)
                warnings.append(msg)
                continue

            if warnings:
                logger.warning(
                    "event_snapshot: recovered from %s after %d corrupt/missing snapshot(s)",
                    snap_id, len(warnings),
                )
            return disk_meta, warnings

        warnings.append("All snapshots corrupt or missing")
        return None, warnings

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_metadata(
        self, portfolio_state: dict, current_seq: int
    ) -> SnapshotMetadata:
        """Construct a SnapshotMetadata from *portfolio_state* and compute its checksum."""
        snap_id = str(uuid.uuid4())
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        meta = SnapshotMetadata(
            snapshot_id=snap_id,
            created_at=now_iso,
            seq_at_snapshot=current_seq,
            capital_state=str(portfolio_state.get("capital_state", "UNKNOWN")),
            open_positions=dict(portfolio_state.get("open_positions", {})),
            realized_pnl=float(portfolio_state.get("realized_pnl", 0.0)),
            strategy_weights=dict(portfolio_state.get("strategy_weights", {})),
            execution_failures=int(portfolio_state.get("execution_failures", 0)),
            active_halt=bool(portfolio_state.get("active_halt", False)),
            halt_reason=str(portfolio_state.get("halt_reason", "")),
            event_count_at_snap=int(portfolio_state.get("event_count", current_seq)),
            checksum="",
        )
        meta.checksum = self._compute_checksum(asdict(meta))
        return meta

    def _write_snapshot(self, meta: SnapshotMetadata) -> None:
        """Atomic write: JSON → gzip → tmp file → os.replace → append to index.

        Any exception is caught and logged; it is NEVER propagated to the caller.
        """
        try:
            snap_id = meta.snapshot_id
            final_path = os.path.join(self._snapshot_dir, f"{snap_id}.snap.gz")

            # 1. Serialise
            raw_bytes = json.dumps(asdict(meta), sort_keys=True).encode("utf-8")

            # 2. Gzip compress and write atomically
            fd, tmp_path = tempfile.mkstemp(
                dir=self._snapshot_dir, suffix=".snap.gz.tmp"
            )
            try:
                with os.fdopen(fd, "wb") as fh:
                    with gzip.GzipFile(fileobj=fh, mode="wb") as gz:
                        gz.write(raw_bytes)
                # 4. Atomic rename
                os.replace(tmp_path, final_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            # 5. Append metadata line to index.jsonl (fcntl locked)
            self._append_index_entry(meta)

            logger.info(
                "event_snapshot: snapshot %s written (seq=%d)",
                snap_id, meta.seq_at_snapshot,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "event_snapshot: _write_snapshot failed for %s: %s",
                meta.snapshot_id, exc,
            )

    def _compute_checksum(self, meta_dict: dict) -> str:
        """SHA-256 of JSON-serialised *meta_dict* excluding the 'checksum' key."""
        d = {k: v for k, v in meta_dict.items() if k != "checksum"}
        canonical = json.dumps(d, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _read_snap_file(self, path: str) -> SnapshotMetadata:
        """Decompress and deserialise a ``.snap.gz`` file."""
        with gzip.open(path, "rb") as gz:
            raw = gz.read()
        d = json.loads(raw.decode("utf-8"))
        return SnapshotMetadata.from_dict(d)

    # ── Index helpers ─────────────────────────────────────────────────────────

    def _index_path(self) -> str:
        return os.path.join(self._snapshot_dir, "index.jsonl")

    def _read_index(self) -> List[dict]:
        path = self._index_path()
        if not os.path.exists(path):
            return []
        entries: List[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # Skip corrupt lines
        except OSError as exc:
            logger.warning("event_snapshot: could not read index: %s", exc)
        return entries

    def _append_index_entry(self, meta: SnapshotMetadata) -> None:
        """Append a one-line JSON summary to index.jsonl using an fcntl lock."""
        path = self._index_path()
        entry = {
            "snapshot_id": meta.snapshot_id,
            "created_at": meta.created_at,
            "seq_at_snapshot": meta.seq_at_snapshot,
            "checksum": meta.checksum,
        }
        line = json.dumps(entry, sort_keys=True) + "\n"
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    fh.write(line)
                    fh.flush()
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            logger.error("event_snapshot: failed to append to index: %s", exc)

    def _rewrite_index(self, entries: List[dict]) -> None:
        """Atomically rewrite index.jsonl to contain only *entries*."""
        path = self._index_path()
        dir_name = os.path.dirname(os.path.abspath(path))
        lines = [json.dumps(e, sort_keys=True) + "\n" for e in entries]
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".jsonl.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            os.replace(tmp_path, path)
        except Exception as exc:  # noqa: BLE001
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            logger.error("event_snapshot: failed to rewrite index: %s", exc)

    def _sync_last_from_index(self) -> None:
        """Initialise _last_snap_seq and _last_snap_ts from the most recent index entry."""
        entries = self._read_index()
        if not entries:
            return
        last = entries[-1]
        with self._lock:
            self._last_snap_seq = int(last.get("seq_at_snapshot", 0))
            # Parse created_at for time trigger initialisation
            created_str = last.get("created_at", "")
            if created_str:
                try:
                    dt = datetime.fromisoformat(created_str)
                    self._last_snap_ts = dt.timestamp()
                except ValueError:
                    pass


# ── Module-level singleton ────────────────────────────────────────────────────

_engine: Optional[EventSnapshotEngine] = None
_engine_lock = threading.Lock()


def get_snapshot_engine() -> EventSnapshotEngine:
    """Return the module-level singleton, initialising it on first call."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = EventSnapshotEngine()
    return _engine
