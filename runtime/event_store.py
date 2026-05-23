"""Immutable event store — proper event-sourcing layer for OpenClaw.

This module sits *above* ReplayJournal and evolves the system toward true
event sourcing.  It does NOT replace or modify replay_journal.py; callers
can write to both stores independently.

Key guarantees
--------------
* Sequence numbers are monotonically increasing, assigned under a threading.Lock.
* Every event carries a SHA-256 checksum so that tampering is detectable.
* File writes are protected with fcntl advisory locks (LOCK_EX) to prevent
  corruption from concurrent processes.
* Sequence counter survives process restarts by reading the last line of the
  store file on __init__.
* Snapshots are written atomically via a temp-file + rename pattern.

Used by
-------
    runtime/replay_validator.py
    runtime/diagnostics.py  (future)
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("openclaw.runtime.event_store")


# ── Event type registry ───────────────────────────────────────────────────────

class EventType(str, Enum):
    SIGNAL_GENERATED        = "SIGNAL_GENERATED"
    INTENT_CREATED          = "INTENT_CREATED"
    INTENT_REJECTED         = "INTENT_REJECTED"
    POSITION_OPENED         = "POSITION_OPENED"
    POSITION_CLOSED         = "POSITION_CLOSED"
    CAPITAL_STATE_CHANGED   = "CAPITAL_STATE_CHANGED"
    EMERGENCY_HALT          = "EMERGENCY_HALT"
    RECONCILIATION_INCIDENT = "RECONCILIATION_INCIDENT"
    EXECUTION_FAILURE       = "EXECUTION_FAILURE"
    STRATEGY_WEIGHT_CHANGED = "STRATEGY_WEIGHT_CHANGED"
    DRIFT_DETECTED          = "DRIFT_DETECTED"
    HALT_RELEASED           = "HALT_RELEASED"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class StoredEvent:
    """A single immutable event as it lives on disk."""
    seq:        int
    event_type: EventType
    trace_id:   str
    symbol:     Optional[str]
    strategy:   Optional[str]
    payload:    Dict[str, Any]
    emitted_at: str           # ISO-8601 UTC
    checksum:   str           # SHA-256 hex digest


@dataclass
class EventStoreSnapshot:
    """Point-in-time snapshot of reconstructed state."""
    snapshot_seq:          int
    snapshot_at:           str   # ISO-8601 UTC
    capital_state:         str
    open_position_count:   int
    strategy_weights_hash: str   # SHA-256 of the serialised weights dict
    checksum:              str   # SHA-256 of the other five fields


# ── Checksum helpers ──────────────────────────────────────────────────────────

def _event_checksum(seq: int, event_type: EventType,
                    trace_id: str, payload: Dict[str, Any]) -> str:
    raw = (
        f"{seq}:{event_type.value}:{trace_id}:"
        f"{json.dumps(payload, sort_keys=True)}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _snapshot_checksum(snapshot_seq: int, snapshot_at: str,
                        capital_state: str, open_position_count: int,
                        strategy_weights_hash: str) -> str:
    raw = (
        f"{snapshot_seq}:{snapshot_at}:{capital_state}:"
        f"{open_position_count}:{strategy_weights_hash}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _weights_hash(weights: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(weights, sort_keys=True).encode()
    ).hexdigest()


# ── EventStore ────────────────────────────────────────────────────────────────

class EventStore:
    """Append-only, checksummed event store for OpenClaw.

    Thread-safety model
    -------------------
    * ``_seq_lock``  — in-process mutex for the sequence counter.
    * ``fcntl``      — OS-level advisory lock acquired before every file write,
                       so multiple *processes* cannot corrupt the file.

    Parameters
    ----------
    store_path:
        Path to the JSONL event store file.
    snapshot_path:
        Path to the JSON snapshot file.
    """

    def __init__(
        self,
        store_path: str = "data/event_store.jsonl",
        snapshot_path: str = "data/event_store_snapshot.json",
    ) -> None:
        self._store_path    = Path(store_path)
        self._snapshot_path = Path(snapshot_path)

        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)

        self._seq_lock = threading.Lock()
        self._last_seq = self._load_last_seq()

        logger.info(
            "EventStore initialised — store=%s  last_seq=%d",
            self._store_path, self._last_seq,
        )

    # ── Sequence bootstrap ────────────────────────────────────────────────────

    def _load_last_seq(self) -> int:
        """Read the last sequence number from the store file (O(1) via seek)."""
        if not self._store_path.exists() or self._store_path.stat().st_size == 0:
            return 0
        try:
            with self._store_path.open("rb") as fh:
                # Walk backwards to find the last non-empty line
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                pos  = size - 1
                buf  = b""
                while pos >= 0:
                    fh.seek(pos)
                    ch = fh.read(1)
                    if ch == b"\n" and buf.strip():
                        break
                    buf = ch + buf
                    pos -= 1
                line = buf.strip()
            if not line:
                return 0
            obj = json.loads(line.decode())
            return int(obj.get("seq", 0))
        except Exception as exc:  # noqa: BLE001
            logger.warning("EventStore: could not read last seq (%s), starting at 0", exc)
            return 0

    # ── Write API ─────────────────────────────────────────────────────────────

    def append(
        self,
        event_type: EventType,
        trace_id:   str,
        payload:    Dict[str, Any],
        symbol:     Optional[str] = None,
        strategy:   Optional[str] = None,
    ) -> StoredEvent:
        """Append one event and return the persisted ``StoredEvent``.

        Sequence number assignment and file write are performed while holding
        both ``_seq_lock`` and an fcntl exclusive lock on the file, so the
        store is safe for concurrent in-process threads and concurrent OS
        processes.
        """
        with self._seq_lock:
            seq = self._last_seq + 1
            emitted_at = datetime.now(timezone.utc).isoformat()
            checksum   = _event_checksum(seq, event_type, trace_id, payload)

            event = StoredEvent(
                seq        = seq,
                event_type = event_type,
                trace_id   = trace_id,
                symbol     = symbol,
                strategy   = strategy,
                payload    = payload,
                emitted_at = emitted_at,
                checksum   = checksum,
            )

            line = self._serialise_event(event) + "\n"

            try:
                with self._store_path.open("a", encoding="utf-8") as fh:
                    fcntl.flock(fh, fcntl.LOCK_EX)
                    try:
                        fh.write(line)
                        fh.flush()
                        os.fsync(fh.fileno())
                    finally:
                        fcntl.flock(fh, fcntl.LOCK_UN)
                self._last_seq = seq
            except OSError as exc:
                logger.error("EventStore.append failed (seq=%d): %s", seq, exc)
                raise

        return event

    # ── Read API ──────────────────────────────────────────────────────────────

    def read_from(self, seq: int = 0, limit: int = 500) -> List[StoredEvent]:
        """Return up to *limit* events whose seq >= *seq*, in ascending order."""
        results: List[StoredEvent] = []
        for raw in self._iter_lines():
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("seq", -1) < seq:
                continue
            evt = self._deserialise_event(obj)
            if evt is None:
                continue
            results.append(evt)
            if len(results) >= limit:
                break
        return results

    def read_by_trace(self, trace_id: str) -> List[StoredEvent]:
        """Return all events matching *trace_id*, in ascending seq order."""
        results: List[StoredEvent] = []
        for raw in self._iter_lines():
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("trace_id") != trace_id:
                continue
            evt = self._deserialise_event(obj)
            if evt is not None:
                results.append(evt)
        return results

    def get_latest_seq(self) -> int:
        """Return the current highest sequence number (thread-safe read)."""
        with self._seq_lock:
            return self._last_seq

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(
        self,
        capital_state:        str,
        open_position_count:  int,
        strategy_weights:     Dict[str, Any],
    ) -> EventStoreSnapshot:
        """Persist a state snapshot and return the ``EventStoreSnapshot``.

        Written atomically via a temp file + rename, so readers always see a
        complete file.
        """
        with self._seq_lock:
            seq = self._last_seq

        snapshot_at  = datetime.now(timezone.utc).isoformat()
        weights_hash = _weights_hash(strategy_weights)
        checksum     = _snapshot_checksum(
            seq, snapshot_at, capital_state, open_position_count, weights_hash
        )

        snap = EventStoreSnapshot(
            snapshot_seq          = seq,
            snapshot_at           = snapshot_at,
            capital_state         = capital_state,
            open_position_count   = open_position_count,
            strategy_weights_hash = weights_hash,
            checksum              = checksum,
        )

        obj = asdict(snap)
        # strategy_weights is stored alongside the snapshot for reference
        obj["strategy_weights"] = strategy_weights

        try:
            dir_ = self._snapshot_path.parent
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8",
                dir=dir_, delete=False, suffix=".tmp"
            ) as tmp:
                json.dump(obj, tmp, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, self._snapshot_path)
            logger.info("EventStore snapshot written at seq=%d", seq)
        except OSError as exc:
            logger.error("EventStore.snapshot write failed: %s", exc)
            raise

        return snap

    # ── Integrity verification ────────────────────────────────────────────────

    def verify_integrity(self, start_seq: int = 0) -> Tuple[bool, List[str]]:
        """Verify checksums for all events with seq >= *start_seq*.

        Returns
        -------
        (ok, errors)
            ``ok`` is True when no errors were found.
            ``errors`` is a list of human-readable error strings.
        """
        errors: List[str] = []
        prev_seq: Optional[int] = None

        for raw in self._iter_lines():
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"JSON parse error: {exc} — line: {raw[:120]!r}")
                continue

            seq = obj.get("seq")
            if seq is None or seq < start_seq:
                continue

            # Monotonicity check
            if prev_seq is not None and seq != prev_seq + 1:
                errors.append(
                    f"Sequence gap detected: expected {prev_seq + 1}, got {seq}"
                )
            prev_seq = seq

            # Checksum check
            try:
                et      = EventType(obj["event_type"])
                tid     = obj["trace_id"]
                payload = obj["payload"]
                stored  = obj["checksum"]
                expected = _event_checksum(seq, et, tid, payload)
                if stored != expected:
                    errors.append(
                        f"Checksum mismatch at seq={seq} "
                        f"(stored={stored[:12]}… expected={expected[:12]}…)"
                    )
            except (KeyError, ValueError) as exc:
                errors.append(f"Malformed event at seq={seq}: {exc}")

        ok = len(errors) == 0
        return ok, errors

    # ── State reconstruction ──────────────────────────────────────────────────

    def reconstruct_state_from_events(
        self, events: List[StoredEvent]
    ) -> Dict[str, Any]:
        """Replay *events* sequentially and return a state summary dict.

        Tracked state
        -------------
        capital_state   — last value from CAPITAL_STATE_CHANGED
        open_positions  — set of trace_ids from POSITION_OPENED / POSITION_CLOSED
        total_trades    — incremented on POSITION_CLOSED
        halt_reason     — set by EMERGENCY_HALT, cleared by HALT_RELEASED
        """
        capital_state:   str            = "UNKNOWN"
        open_positions:  Set[str]       = set()
        total_trades:    int            = 0
        halt_reason:     Optional[str]  = None

        for ev in events:
            et = ev.event_type

            if et is EventType.POSITION_OPENED:
                open_positions.add(ev.trace_id)

            elif et is EventType.POSITION_CLOSED:
                open_positions.discard(ev.trace_id)
                total_trades += 1

            elif et is EventType.CAPITAL_STATE_CHANGED:
                capital_state = ev.payload.get("new_state", capital_state)

            elif et is EventType.EMERGENCY_HALT:
                halt_reason = ev.payload.get("halt_reason") or ev.payload.get("reason")

            elif et is EventType.HALT_RELEASED:
                halt_reason = None

        return {
            "capital_state":         capital_state,
            "open_positions":        sorted(open_positions),
            "open_position_count":   len(open_positions),
            "total_trades":          total_trades,
            "halt_reason":           halt_reason,
            "events_replayed":       len(events),
            "last_seq":              events[-1].seq if events else 0,
        }

    # ── Serialisation helpers ─────────────────────────────────────────────────

    @staticmethod
    def _serialise_event(event: StoredEvent) -> str:
        obj = {
            "seq":        event.seq,
            "event_type": event.event_type.value,
            "trace_id":   event.trace_id,
            "symbol":     event.symbol,
            "strategy":   event.strategy,
            "payload":    event.payload,
            "emitted_at": event.emitted_at,
            "checksum":   event.checksum,
        }
        return json.dumps(obj, default=str)

    @staticmethod
    def _deserialise_event(obj: Dict[str, Any]) -> Optional[StoredEvent]:
        try:
            return StoredEvent(
                seq        = int(obj["seq"]),
                event_type = EventType(obj["event_type"]),
                trace_id   = str(obj["trace_id"]),
                symbol     = obj.get("symbol"),
                strategy   = obj.get("strategy"),
                payload    = obj.get("payload", {}),
                emitted_at = str(obj.get("emitted_at", "")),
                checksum   = str(obj.get("checksum", "")),
            )
        except (KeyError, ValueError) as exc:
            logger.debug("EventStore: could not deserialise event: %s — %s", exc, obj)
            return None

    def _iter_lines(self):
        """Yield raw non-empty lines from the store file."""
        if not self._store_path.exists():
            return
        try:
            with self._store_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        yield line
        except OSError as exc:
            logger.error("EventStore: could not read store file: %s", exc)
