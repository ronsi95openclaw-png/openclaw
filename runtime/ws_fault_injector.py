"""Deterministic WebSocket fault injector for OpenClaw chaos testing.

Injects realistic WS faults (packet loss, duplication, reordering, stale
heartbeats, sequence gaps, malformed payloads) into a message stream to
exercise WSGuardian recovery paths.

All randomness is seeded for deterministic replay.  The injector does NOT
import WSGuardian at module level to avoid circular-import risk; guardian
objects are passed as parameters to integration methods.

Thread-safety
-------------
All public methods are thread-safe via threading.Lock.
The singleton is created under double-checked locking.

Module singleton
----------------
    from runtime.ws_fault_injector import get_injector
    injector = get_injector(seed=42)
    messages = injector.inject({"type": "trade", "seq": 1}, seq=1)
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Deque, Dict, List, Optional

import random as _random_module

logger = logging.getLogger("openclaw.runtime.ws_fault_injector")


# ── Fault type registry ───────────────────────────────────────────────────────

class FaultType(str, Enum):
    PACKET_LOSS       = "PACKET_LOSS"
    PACKET_DUPLICATION= "PACKET_DUPLICATION"
    PACKET_REORDERING = "PACKET_REORDERING"
    STALE_HEARTBEAT   = "STALE_HEARTBEAT"
    DELAYED_RECONNECT = "DELAYED_RECONNECT"
    FRAGMENTED_FRAME  = "FRAGMENTED_FRAME"
    MALFORMED_PAYLOAD = "MALFORMED_PAYLOAD"
    SEQUENCE_GAP      = "SEQUENCE_GAP"


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class FaultEvent:
    """Record of a single injected fault."""
    fault_id:         str       # UUID4
    fault_type:       FaultType
    injected_at:      str       # ISO-8601 UTC timestamp
    seed:             int
    parameters:       Dict      # fault-specific configuration
    emitted_to_store: bool = False


@dataclass
class FaultInjectionConfig:
    """Tunable parameters for the WSFaultInjector."""
    packet_loss_rate:    float = 0.05   # fraction of messages dropped
    duplication_rate:    float = 0.02
    reorder_window:      int   = 5      # max messages buffered for reordering
    stale_heartbeat_delay_s: float = 30.0
    reconnect_delay_s:   float = 5.0
    sequence_gap_size:   int   = 10
    malformed_rate:      float = 0.01
    max_injection_rate:  float = 0.20   # bounded: max fraction of messages to fault


# ── WSFaultInjector ───────────────────────────────────────────────────────────

class WSFaultInjector:
    """Deterministic WebSocket fault injector.

    Parameters
    ----------
    seed:
        RNG seed for deterministic fault scheduling.
    config:
        Fault injection configuration.  Defaults to FaultInjectionConfig().
    """

    # Rolling window for rate-limiting fault injection
    _RATE_WINDOW: int = 20

    def __init__(
        self,
        seed: int = 42,
        config: Optional[FaultInjectionConfig] = None,
    ) -> None:
        self._seed   = seed
        self._rng    = _random_module.Random(seed)
        self._config = config or FaultInjectionConfig()
        self._lock   = threading.Lock()

        # State
        self._events: List[FaultEvent]           = []
        self._reorder_buffer: Deque[dict]        = deque()
        self._rate_window: Deque[bool]           = deque(maxlen=self._RATE_WINDOW)
        # Track internal virtual sequence counter for SEQUENCE_GAP
        self._internal_seq: int = 0
        self._total_messages: int = 0
        self._total_injected: int = 0
        # Per-type counts
        self._type_counts: Dict[FaultType, int] = {ft: 0 for ft in FaultType}

    # ── Core inject ───────────────────────────────────────────────────────────

    def inject(self, message: dict, seq: int) -> List[dict]:
        """Apply fault injection to a single incoming WS message.

        Parameters
        ----------
        message:
            Incoming WebSocket message dictionary.
        seq:
            Sequence number of this message.

        Returns
        -------
        List of 0+ messages to deliver downstream.
        0 = dropped, 1 = normal/malformed, 2+ = duplicated or reordered result.
        """
        with self._lock:
            return self._inject_unlocked(message, seq)

    def _inject_unlocked(self, message: dict, seq: int) -> List[dict]:
        """Internal inject logic.  Caller must hold self._lock."""
        rng     = self._rng
        config  = self._config
        self._total_messages += 1
        self._internal_seq   += 1

        # Check whether we are already at the injection rate limit
        rate_limited = self._is_rate_limited()

        # ── Priority order: MALFORMED > PACKET_LOSS > DUPLICATION > REORDER > HEARTBEAT

        # MALFORMED (highest priority)
        if not rate_limited and rng.random() < config.malformed_rate:
            malformed = {
                "type": "MALFORMED",
                "data": "???corrupted???",
                "seq": seq,
            }
            self._record_fault(FaultType.MALFORMED_PAYLOAD, {
                "original_seq": seq, "malformed_data": malformed["data"]
            })
            self._rate_window.append(True)
            return [malformed]

        # PACKET_LOSS
        if not rate_limited and rng.random() < config.packet_loss_rate:
            self._record_fault(FaultType.PACKET_LOSS, {"dropped_seq": seq})
            self._rate_window.append(True)
            return []

        # PACKET_DUPLICATION
        if not rate_limited and rng.random() < config.duplication_rate:
            self._record_fault(FaultType.PACKET_DUPLICATION, {
                "seq": seq, "duplicate_count": 2
            })
            self._rate_window.append(True)
            return [message, message.copy()]

        # PACKET_REORDERING
        if (
            not rate_limited
            and len(self._reorder_buffer) < config.reorder_window
            and rng.random() < 0.05  # base reorder probability
        ):
            # Buffer this message and return an earlier one if buffered
            self._reorder_buffer.append(message.copy())
            self._record_fault(FaultType.PACKET_REORDERING, {
                "buffered_seq": seq, "buffer_len": len(self._reorder_buffer)
            })
            self._rate_window.append(True)
            if len(self._reorder_buffer) >= 2:
                return [self._reorder_buffer.popleft()]
            return []

        # Flush buffer entry if one exists (no fault this message)
        if self._reorder_buffer:
            buffered = self._reorder_buffer.popleft()
            self._rate_window.append(False)
            return [buffered, message]

        self._rate_window.append(False)
        return [message]

    def _is_rate_limited(self) -> bool:
        """True if the rolling injection rate already exceeds max_injection_rate.

        Strict `>` comparison so that max_injection_rate=1.0 means 100% of
        messages may be faulted (i.e., no ceiling applies).
        """
        if not self._rate_window:
            return False
        faulted = sum(1 for x in self._rate_window if x)
        return (faulted / len(self._rate_window)) > self._config.max_injection_rate

    def _record_fault(self, fault_type: FaultType, parameters: dict) -> None:
        """Create and store a FaultEvent.  Caller holds self._lock."""
        event = FaultEvent(
            fault_id=str(uuid.uuid4()),
            fault_type=fault_type,
            injected_at=datetime.now(timezone.utc).isoformat(),
            seed=self._seed,
            parameters=parameters,
            emitted_to_store=False,
        )
        self._events.append(event)
        self._type_counts[fault_type] += 1
        self._total_injected += 1

    # ── WSGuardian integration helpers ────────────────────────────────────────

    def inject_stale_heartbeat(self, guardian: object) -> FaultEvent:
        """Simulate a stale heartbeat by calling guardian.record_reconnect(False) 3×.

        Parameters
        ----------
        guardian:
            A WSGuardian-compatible object with record_reconnect(success: bool).

        Returns
        -------
        FaultEvent describing the injection.
        """
        for _ in range(3):
            try:
                guardian.record_reconnect(success=False)  # type: ignore[attr-defined]
            except Exception:
                pass

        with self._lock:
            event = FaultEvent(
                fault_id=str(uuid.uuid4()),
                fault_type=FaultType.STALE_HEARTBEAT,
                injected_at=datetime.now(timezone.utc).isoformat(),
                seed=self._seed,
                parameters={
                    "delay_s": self._config.stale_heartbeat_delay_s,
                    "simulated_failures": 3,
                },
                emitted_to_store=False,
            )
            self._events.append(event)
            self._type_counts[FaultType.STALE_HEARTBEAT] += 1
            self._total_injected += 1

        return event

    def inject_sequence_gap(
        self,
        guardian: object,
        gap_size: Optional[int] = None,
    ) -> FaultEvent:
        """Simulate a WS sequence gap by feeding guardian non-consecutive seq numbers.

        Parameters
        ----------
        guardian:
            A WSGuardian-compatible object with record_message(seq: int).
        gap_size:
            How many sequence numbers to skip.  Defaults to config.sequence_gap_size.

        Returns
        -------
        FaultEvent describing the injection.
        """
        if gap_size is None:
            gap_size = self._config.sequence_gap_size

        with self._lock:
            base_seq = self._internal_seq
            self._internal_seq += gap_size  # skip

        # Feed guardian: one normal seq, then the gapped seq
        try:
            guardian.record_message(seq=base_seq)           # type: ignore[attr-defined]
            guardian.record_message(seq=base_seq + gap_size + 1)  # gap
        except Exception:
            pass

        with self._lock:
            event = FaultEvent(
                fault_id=str(uuid.uuid4()),
                fault_type=FaultType.SEQUENCE_GAP,
                injected_at=datetime.now(timezone.utc).isoformat(),
                seed=self._seed,
                parameters={
                    "base_seq": base_seq,
                    "gap_size": gap_size,
                    "resumed_seq": base_seq + gap_size + 1,
                },
                emitted_to_store=False,
            )
            self._events.append(event)
            self._type_counts[FaultType.SEQUENCE_GAP] += 1
            self._total_injected += 1

        return event

    # ── Inspection API ────────────────────────────────────────────────────────

    def get_events(self) -> List[FaultEvent]:
        """Return a snapshot of all injected fault events (thread-safe copy)."""
        with self._lock:
            return list(self._events)

    def get_stats(self) -> dict:
        """Return injection statistics.

        Returns
        -------
        dict with keys:
            counts_by_type, total_injected, total_messages_processed,
            injection_rate.
        """
        with self._lock:
            rate = (
                self._total_injected / self._total_messages
                if self._total_messages > 0
                else 0.0
            )
            return {
                "counts_by_type": {
                    ft.value: self._type_counts[ft] for ft in FaultType
                },
                "total_injected": self._total_injected,
                "total_messages_processed": self._total_messages,
                "injection_rate": rate,
            }

    def reset(self) -> None:
        """Clear all event history and reset to initial deterministic state."""
        with self._lock:
            self._events.clear()
            self._reorder_buffer.clear()
            self._rate_window.clear()
            self._internal_seq    = 0
            self._total_messages  = 0
            self._total_injected  = 0
            self._type_counts     = {ft: 0 for ft in FaultType}
            self._rng             = _random_module.Random(self._seed)

    # ── EventStore emission ───────────────────────────────────────────────────

    def emit_chaos_events_to_store(self) -> int:
        """Emit all un-emitted FaultEvents to the EventStore.

        Uses EventType.RECONCILIATION_COMPLETED as the closest available type.

        Returns
        -------
        Number of events successfully emitted.
        """
        emitted = 0
        try:
            from runtime.event_store import get_store, EventType  # noqa: PLC0415
            store = get_store()
            with self._lock:
                pending = [e for e in self._events if not e.emitted_to_store]

            for event in pending:
                try:
                    store.append(
                        event_type=EventType.RECONCILIATION_COMPLETED,
                        payload={
                            "source": "ws_fault_injector",
                            "fault_id": event.fault_id,
                            "fault_type": event.fault_type.value,
                            "injected_at": event.injected_at,
                            "parameters": event.parameters,
                        },
                    )
                    with self._lock:
                        event.emitted_to_store = True
                    emitted += 1
                except Exception:
                    logger.debug(
                        "ws_fault_injector: failed to emit event %s",
                        event.fault_id, exc_info=True
                    )
        except Exception:
            logger.debug(
                "ws_fault_injector: event store unavailable", exc_info=True
            )

        return emitted


# ── Module singleton ──────────────────────────────────────────────────────────

_instance: Optional[WSFaultInjector] = None
_instance_lock = threading.Lock()


def get_injector(seed: int = 42) -> WSFaultInjector:
    """Return the module-level singleton WSFaultInjector.

    Double-checked locking ensures exactly one instance is created.
    """
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = WSFaultInjector(seed=seed)
    return _instance
