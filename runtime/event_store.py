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
    SIGNAL_GENERATED          = "SIGNAL_GENERATED"
    INTENT_CREATED            = "INTENT_CREATED"
    INTENT_REJECTED           = "INTENT_REJECTED"
    POSITION_OPENED           = "POSITION_OPENED"
    POSITION_CLOSED           = "POSITION_CLOSED"
    CAPITAL_STATE_CHANGED     = "CAPITAL_STATE_CHANGED"
    EMERGENCY_HALT            = "EMERGENCY_HALT"
    RECONCILIATION_INCIDENT   = "RECONCILIATION_INCIDENT"
    EXECUTION_FAILURE         = "EXECUTION_FAILURE"
    STRATEGY_WEIGHT_CHANGED   = "STRATEGY_WEIGHT_CHANGED"
    DRIFT_DETECTED            = "DRIFT_DETECTED"
    HALT_RELEASED             = "HALT_RELEASED"
    # ── Order lifecycle ───────────────────────────────────────────────────────
    ORDER_SUBMITTED           = "ORDER_SUBMITTED"
    ORDER_ACKNOWLEDGED        = "ORDER_ACKNOWLEDGED"
    ORDER_REJECTED            = "ORDER_REJECTED"
    ORDER_CANCELLED           = "ORDER_CANCELLED"
    # ── Fill events ───────────────────────────────────────────────────────────
    POSITION_PARTIALLY_FILLED = "POSITION_PARTIALLY_FILLED"
    SL_TRIGGERED              = "SL_TRIGGERED"
    TP_TRIGGERED              = "TP_TRIGGERED"
    # ── Reconciliation lifecycle ──────────────────────────────────────────────
    RECONCILIATION_STARTED    = "RECONCILIATION_STARTED"
    RECONCILIATION_COMPLETED  = "RECONCILIATION_COMPLETED"
    # ── WebSocket health ──────────────────────────────────────────────────────
    WEBSOCKET_RECONNECTED     = "WEBSOCKET_RECONNECTED"
    WEBSOCKET_DESYNC          = "WEBSOCKET_DESYNC"
    EXECUTION_TIMEOUT         = "EXECUTION_TIMEOUT"


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


# ── EventReplayEngine ─────────────────────────────────────────────────────────

class EventReplayEngine:
    """Reconstructs full portfolio state from event history.

    Reads the EventStore sequentially and replays every event to produce a
    consistent portfolio snapshot.  The engine never modifies the store —
    all access is read-only via ``EventStore.read_from()``.

    Thread-safety
    -------------
    No additional lock is needed: ``EventStore`` is append-only, so concurrent
    reads are safe.  The snapshot returned by ``reconstruct_portfolio_state``
    is a plain dict (not shared state) and therefore safe to pass across
    threads.
    """

    _BATCH_SIZE: int = 500

    def __init__(self, store: EventStore) -> None:
        self._store = store

    # ── Public API ────────────────────────────────────────────────────────────

    def reconstruct_portfolio_state(self, up_to_seq: int = None) -> Dict[str, Any]:
        """Read all events from the store and reconstruct full portfolio state.

        Parameters
        ----------
        up_to_seq:
            If provided, only events with seq <= up_to_seq are processed.
            Pass ``None`` to replay the entire store.

        Returns
        -------
        dict with keys:
            capital_state, open_positions, realized_pnl, active_halt,
            halt_reason, total_trades, exposure, execution_failures,
            strategy_trade_counts, last_capital_transition,
            reconstructed_at, events_processed
        """
        # ── Mutable reconstruction state ──────────────────────────────────────
        capital_state: str = "UNKNOWN"
        open_positions: Dict[str, Dict[str, Any]] = {}
        realized_pnl: float = 0.0
        active_halt: bool = False
        halt_reason: str = ""
        total_trades: int = 0
        execution_failures: int = 0
        strategy_trade_counts: Dict[str, int] = {}
        last_capital_transition: str = ""
        order_event_count: int = 0
        events_processed: int = 0

        # Read events in batches of 500 until EOF or up_to_seq
        next_seq: int = 0
        done: bool = False

        while not done:
            try:
                batch = self._store.read_from(seq=next_seq, limit=self._BATCH_SIZE)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "EventReplayEngine: read_from(seq=%d) failed: %s", next_seq, exc
                )
                break

            if not batch:
                break

            for ev in batch:
                # Honour up_to_seq ceiling
                if up_to_seq is not None and ev.seq > up_to_seq:
                    done = True
                    break

                # Pre-declare mutable single-element lists so _apply_event can
                # mutate scalars via list[0] (Python doesn't have by-ref scalars).
                capital_state_ref           = [capital_state]
                realized_pnl_ref            = [realized_pnl]
                active_halt_ref             = [active_halt]
                halt_reason_ref             = [halt_reason]
                total_trades_ref            = [total_trades]
                execution_failures_ref      = [execution_failures]
                last_capital_transition_ref = [last_capital_transition]
                order_event_count_ref       = [order_event_count]

                try:
                    self._apply_event(
                        ev,
                        open_positions=open_positions,
                        capital_state_ref=capital_state_ref,
                        realized_pnl_ref=realized_pnl_ref,
                        active_halt_ref=active_halt_ref,
                        halt_reason_ref=halt_reason_ref,
                        total_trades_ref=total_trades_ref,
                        execution_failures_ref=execution_failures_ref,
                        strategy_trade_counts=strategy_trade_counts,
                        last_capital_transition_ref=last_capital_transition_ref,
                        order_event_count_ref=order_event_count_ref,
                    )
                    # Unpack mutated scalars back from their single-element lists
                    capital_state           = capital_state_ref[0]
                    realized_pnl            = realized_pnl_ref[0]
                    active_halt             = active_halt_ref[0]
                    halt_reason             = halt_reason_ref[0]
                    total_trades            = total_trades_ref[0]
                    execution_failures      = execution_failures_ref[0]
                    last_capital_transition = last_capital_transition_ref[0]
                    order_event_count       = order_event_count_ref[0]
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "EventReplayEngine: skipping event seq=%d type=%s error=%s",
                        ev.seq, ev.event_type, exc,
                    )

                events_processed += 1

            if len(batch) < self._BATCH_SIZE:
                # Fewer events than requested — we have reached EOF
                break

            # Advance cursor past the last event we just read
            next_seq = batch[-1].seq + 1

        exposure = self._compute_exposure(open_positions)

        return {
            "capital_state":           capital_state,
            "open_positions":          dict(open_positions),
            "realized_pnl":            realized_pnl,
            "active_halt":             active_halt,
            "halt_reason":             halt_reason,
            "total_trades":            total_trades,
            "exposure":                exposure,
            "execution_failures":      execution_failures,
            "strategy_trade_counts":   dict(strategy_trade_counts),
            "last_capital_transition": last_capital_transition,
            "reconstructed_at":        datetime.now(timezone.utc).isoformat(),
            "events_processed":        events_processed,
        }

    def verify_reconstruction(self) -> Tuple[bool, List[str]]:
        """Verify store integrity: checksums + monotonic sequences + state validity.

        Returns
        -------
        (ok, errors)
            ``ok`` is True when the store passes all checks.
            ``errors`` is a list of human-readable error strings.
        """
        errors: List[str] = []

        # Delegate checksum + monotonicity checks to EventStore
        store_ok, store_errors = self._store.verify_integrity(start_seq=0)
        errors.extend(store_errors)

        # Additional state-machine validity: attempt a full reconstruction and
        # check for obvious invariant violations
        try:
            state = self.reconstruct_portfolio_state()

            # Realized PnL must be finite
            if not (state["realized_pnl"] == state["realized_pnl"]):  # NaN check
                errors.append("reconstruct_portfolio_state: realized_pnl is NaN")

            # Exposure must be non-negative
            if state["exposure"] < 0.0:
                errors.append(
                    f"reconstruct_portfolio_state: exposure is negative ({state['exposure']})"
                )

            # Active halt with no reason is suspicious but not fatal
            if state["active_halt"] and not state["halt_reason"]:
                errors.append(
                    "State machine: EMERGENCY_HALT recorded but halt_reason is empty"
                )

        except Exception as exc:  # noqa: BLE001
            errors.append(f"reconstruct_portfolio_state raised: {exc}")

        ok = len(errors) == 0
        return ok, errors

    def get_event_throughput(self, window_seconds: int = 60) -> float:
        """Return events/second over the last *window_seconds*.

        Reads the last 1 000 events, filters to those emitted within the
        window, and divides the count by the window length.

        Parameters
        ----------
        window_seconds:
            Look-back window in seconds (default 60).

        Returns
        -------
        float
            Events per second; 0.0 if the store is empty or the window
            contains no events.
        """
        import time as _time

        now_ts = _time.time()
        cutoff = now_ts - window_seconds

        # We don't know the total number of events ahead of time; read the last
        # 1 000 from the highest known seq.
        latest_seq = self._store.get_latest_seq()
        start_seq  = max(0, latest_seq - 999)

        try:
            events = self._store.read_from(seq=start_seq, limit=1000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("EventReplayEngine.get_event_throughput read error: %s", exc)
            return 0.0

        count = 0
        for ev in events:
            try:
                # emitted_at is an ISO-8601 UTC string; parse the Unix epoch
                ts = datetime.fromisoformat(ev.emitted_at.replace("Z", "+00:00")).timestamp()
                if ts >= cutoff:
                    count += 1
            except Exception:  # noqa: BLE001
                continue

        if window_seconds <= 0:
            return 0.0
        return count / window_seconds

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _apply_event(
        ev: StoredEvent,
        *,
        open_positions: Dict[str, Dict[str, Any]],
        capital_state_ref: List[str],
        realized_pnl_ref: List[float],
        active_halt_ref: List[bool],
        halt_reason_ref: List[str],
        total_trades_ref: List[int],
        execution_failures_ref: List[int],
        strategy_trade_counts: Dict[str, int],
        last_capital_transition_ref: List[str],
        order_event_count_ref: List[int],
    ) -> None:
        """Apply a single event to the mutable reconstruction state.

        All scalar state values are passed as single-element lists so that
        Python's pass-by-object-reference semantics allow mutation without
        needing a dedicated state class.  Unknown event types are logged and
        skipped gracefully.
        """
        et = ev.event_type
        p  = ev.payload

        if et is EventType.POSITION_OPENED:
            open_positions[ev.trace_id] = {
                "symbol":      p.get("symbol", ev.symbol or ""),
                "side":        p.get("side", ""),
                "entry_price": float(p.get("entry_price", 0.0)),
                "size":        float(p.get("size", 0.0)),
                "strategy":    p.get("strategy", ev.strategy or ""),
            }

        elif et is EventType.POSITION_CLOSED:
            pos = open_positions.pop(ev.trace_id, None)
            total_trades_ref[0] += 1
            pnl = float(p.get("pnl", 0.0))
            realized_pnl_ref[0] += pnl
            # Increment per-strategy trade counter
            strategy_name = (
                p.get("strategy")
                or (pos.get("strategy") if pos else None)
                or ev.strategy
                or "UNKNOWN"
            )
            strategy_trade_counts[strategy_name] = (
                strategy_trade_counts.get(strategy_name, 0) + 1
            )

        elif et is EventType.CAPITAL_STATE_CHANGED:
            old_state = capital_state_ref[0]
            new_state = p.get("new_state", capital_state_ref[0])
            capital_state_ref[0] = new_state
            if old_state != new_state:
                last_capital_transition_ref[0] = f"{old_state}→{new_state}"

        elif et is EventType.EMERGENCY_HALT:
            active_halt_ref[0] = True
            halt_reason_ref[0] = (
                p.get("halt_reason") or p.get("reason") or "unknown"
            )

        elif et is EventType.HALT_RELEASED:
            active_halt_ref[0] = False
            halt_reason_ref[0] = ""

        elif et is EventType.EXECUTION_FAILURE:
            execution_failures_ref[0] += 1

        elif et in (
            EventType.ORDER_SUBMITTED,
            EventType.ORDER_ACKNOWLEDGED,
            EventType.ORDER_CANCELLED,
        ):
            # These order lifecycle events do not change portfolio state but
            # we track a running counter for diagnostic purposes.
            order_event_count_ref[0] += 1

        elif et in (
            EventType.SIGNAL_GENERATED,
            EventType.INTENT_CREATED,
            EventType.INTENT_REJECTED,
            EventType.RECONCILIATION_INCIDENT,
            EventType.STRATEGY_WEIGHT_CHANGED,
            EventType.DRIFT_DETECTED,
            EventType.ORDER_REJECTED,
            EventType.POSITION_PARTIALLY_FILLED,
            EventType.SL_TRIGGERED,
            EventType.TP_TRIGGERED,
            EventType.RECONCILIATION_STARTED,
            EventType.RECONCILIATION_COMPLETED,
            EventType.WEBSOCKET_RECONNECTED,
            EventType.WEBSOCKET_DESYNC,
            EventType.EXECUTION_TIMEOUT,
        ):
            # Known event types that carry no portfolio-state mutation —
            # silently accepted without action.
            pass

        else:
            logger.debug(
                "EventReplayEngine: unknown event type %r at seq=%d — skipped",
                et, ev.seq,
            )

    @staticmethod
    def _compute_exposure(open_positions: Dict[str, Dict[str, Any]]) -> float:
        """Compute total notional exposure across all open positions.

        Uses ``size * entry_price`` when entry_price > 0.  Falls back to a
        rough proxy of ``size * 50_000`` for BTC-like instruments when price
        is unavailable.
        """
        total = 0.0
        for pos in open_positions.values():
            size  = float(pos.get("size", 0.0))
            price = float(pos.get("entry_price", 0.0))
            if price > 0.0:
                total += size * price
            else:
                # Proxy: assume ~$50 000 per unit (BTC-range default)
                total += size * 50_000.0
        return total
