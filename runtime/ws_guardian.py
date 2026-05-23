"""WebSocket Guardian subsystem for OpenClaw.

Monitors the health of the WebSocket / real-time data feed by aggregating
health signals fed into it by the scan loop.  The guardian does **not**
manage the WebSocket connection itself — it only tracks observed evidence
(heartbeats, message sequences, reconnect outcomes, desync events) and
exposes a gate that the bot can query before opening positions.

Integration points (in trading/cryptocom_bot.py)
-------------------------------------------------
* ``__init__``:     ``self._ws_guardian = get_guardian()``
* scan loop:        ``self._ws_guardian.record_heartbeat()`` after a live price fetch
* candle received:  ``self._ws_guardian.record_message(seq=<seq>)``
* reconnect:        ``self._ws_guardian.record_reconnect(success=<bool>)``
* _open_position:   ``if self._ws_guardian.should_halt_entries(): return``
* /api/diagnostics: ``self._ws_guardian.get_status_dict()``

Module-level singleton
----------------------
``get_guardian()`` returns a lazily-initialised ``WSGuardian`` with default
parameters.  The same instance is returned on every call.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("openclaw.runtime.ws_guardian")

# ── Optional integration imports (all graceful) ───────────────────────────────

try:
    from runtime.metrics import get_registry as _get_registry  # type: ignore[import]
    _metrics = _get_registry()
except Exception:  # noqa: BLE001
    _metrics = None

try:
    from runtime.event_store import EventStore as _EventStore, EventType as _EventType  # type: ignore[import]
    _event_store: Optional[_EventStore] = _EventStore()
except Exception:  # noqa: BLE001
    _event_store = None

try:
    from runtime.telegram_alerts import _send as _telegram_send  # type: ignore[import]
    _telegram_available = True
except Exception:  # noqa: BLE001
    _telegram_available = False


# ── Enums & dataclasses ───────────────────────────────────────────────────────

class HeartbeatStatus(str, Enum):
    """Coarse health classification based on heartbeat age."""
    HEALTHY = "HEALTHY"
    STALE   = "STALE"
    DEAD    = "DEAD"


@dataclass
class WSHealthScore:
    """Point-in-time WebSocket health assessment."""
    score:                  float            # 0.0 (dead) → 1.0 (perfect)
    heartbeat_status:       HeartbeatStatus
    last_heartbeat_age_s:   float            # seconds since last heartbeat
    sequence_gaps_detected: int              # cumulative gap count
    reconnect_count:        int              # lifetime reconnect attempts
    consecutive_failures:   int
    message_rate_per_min:   float            # messages in last 60 s × 60
    last_score_ts:          str              # ISO-8601 UTC


# ── WSGuardian ────────────────────────────────────────────────────────────────

class WSGuardian:
    """Monitors WebSocket feed health and gates position entry.

    All public methods are thread-safe via a single ``threading.Lock``.

    Parameters
    ----------
    heartbeat_timeout_s:
        Age (seconds) after which the feed is considered STALE.
    dead_timeout_s:
        Age (seconds) after which the feed is considered DEAD.
    max_reconnect_attempts:
        Upper bound used for backoff computation (informational).
    reconnect_backoff_base_s:
        Base for exponential backoff: delay = min(base^attempts, 300 s).
    """

    # How many recent message timestamps to keep for rate calculation
    _MSG_WINDOW_SAMPLES: int = 500

    def __init__(
        self,
        heartbeat_timeout_s: float = 30.0,
        dead_timeout_s: float = 90.0,
        max_reconnect_attempts: int = 10,
        reconnect_backoff_base_s: float = 2.0,
    ) -> None:
        self._heartbeat_timeout_s    = heartbeat_timeout_s
        self._dead_timeout_s         = dead_timeout_s
        self._max_reconnect_attempts = max_reconnect_attempts
        self._backoff_base           = reconnect_backoff_base_s

        self._lock = threading.Lock()

        # ── Heartbeat state ───────────────────────────────────────────────────
        self._last_heartbeat_ts: float = time.time()  # epoch seconds

        # ── Message / sequence state ──────────────────────────────────────────
        self._last_seq: Optional[int]         = None
        self._sequence_gaps: int              = 0
        self._msg_timestamps: Deque[float]    = deque(maxlen=self._MSG_WINDOW_SAMPLES)

        # ── Reconnect state ───────────────────────────────────────────────────
        self._reconnect_count: int      = 0
        self._consecutive_failures: int = 0

        # ── Desync state ──────────────────────────────────────────────────────
        self._desync_count: int = 0

        # ── Previous DEAD status (for transition detection) ────────────────────
        self._was_dead: bool = False

        logger.info(
            "WSGuardian initialised — hb_timeout=%ss dead_timeout=%ss backoff_base=%s",
            heartbeat_timeout_s, dead_timeout_s, reconnect_backoff_base_s,
        )

    # ── Public write API ──────────────────────────────────────────────────────

    def record_heartbeat(self, ts_ms: Optional[float] = None) -> None:
        """Record a heartbeat event (call whenever live price data is fetched).

        Parameters
        ----------
        ts_ms:
            Epoch timestamp in **milliseconds**.  Defaults to ``time.time() * 1000``.
        """
        ts_s = (ts_ms / 1000.0) if ts_ms is not None else time.time()
        with self._lock:
            self._last_heartbeat_ts = ts_s
        logger.debug("WSGuardian: heartbeat recorded at %.3f", ts_s)

    def record_message(
        self,
        seq: Optional[int] = None,
        ts_ms: Optional[float] = None,
    ) -> None:
        """Record an incoming message (call per candle or tick received).

        Parameters
        ----------
        seq:
            Optional sequence number from the exchange.  If provided, any
            gap vs. the previous sequence number is counted.
        ts_ms:
            Epoch timestamp in milliseconds.  Defaults to now.
        """
        ts_s = (ts_ms / 1000.0) if ts_ms is not None else time.time()
        with self._lock:
            self._msg_timestamps.append(ts_s)

            if seq is not None:
                if self._last_seq is not None and seq != self._last_seq + 1:
                    gap = abs(seq - (self._last_seq + 1))
                    self._sequence_gaps += gap
                    logger.warning(
                        "WSGuardian: sequence gap detected — expected %d got %d (gap=%d)",
                        self._last_seq + 1, seq, gap,
                    )
                self._last_seq = seq

    def record_reconnect(self, success: bool) -> None:
        """Record the outcome of a reconnect attempt.

        Parameters
        ----------
        success:
            True if the reconnect succeeded; False if it failed.
        """
        with self._lock:
            self._reconnect_count += 1
            if success:
                self._consecutive_failures = 0
                logger.info(
                    "WSGuardian: reconnect succeeded (total reconnects=%d)",
                    self._reconnect_count,
                )
            else:
                self._consecutive_failures += 1
                logger.warning(
                    "WSGuardian: reconnect failed (consecutive_failures=%d)",
                    self._consecutive_failures,
                )

            # Emit event store event
            self._emit_event_unlocked(
                _EventType.WEBSOCKET_RECONNECTED if success else _EventType.WEBSOCKET_DESYNC,
                payload={"success": success, "reconnect_count": self._reconnect_count},
            )

    def record_desync(self, description: str) -> None:
        """Record a desync event (call when data integrity problems are found).

        Parameters
        ----------
        description:
            Human-readable description of the desync (for logging / events).
        """
        with self._lock:
            self._desync_count += 1
            logger.warning("WSGuardian: desync recorded — %s", description)

            # Prometheus
            if _metrics is not None:
                try:
                    _metrics.record_exchange_error("ws_desync")
                except Exception:  # noqa: BLE001
                    pass

            # Event store
            self._emit_event_unlocked(
                _EventType.WEBSOCKET_DESYNC,
                payload={"description": description, "desync_count": self._desync_count},
            )

    # ── Public read API ───────────────────────────────────────────────────────

    def get_health_score(self) -> WSHealthScore:
        """Compute and return a full health score snapshot.

        Scoring rules
        -------------
        * Base score = 1.0
        * heartbeat age > heartbeat_timeout_s  → −0.3
        * heartbeat age > dead_timeout_s       → −0.5 additional
        * each sequence gap                    → −0.1 (max −0.3)
        * each consecutive failure             → −0.05 (max −0.2)
        * Clamped to [0.0, 1.0]
        """
        with self._lock:
            now            = time.time()
            hb_age         = now - self._last_heartbeat_ts
            gaps           = self._sequence_gaps
            consec_fails   = self._consecutive_failures
            reconnect_cnt  = self._reconnect_count
            rate           = self._message_rate_unlocked(now)
            status         = self._heartbeat_status_unlocked(hb_age)

        score = 1.0

        # Heartbeat age penalty
        if hb_age > self._heartbeat_timeout_s:
            score -= 0.3
        if hb_age > self._dead_timeout_s:
            score -= 0.5  # additional; total −0.8 for DEAD

        # Sequence gap penalty (cap at −0.3)
        gap_penalty = min(gaps * 0.1, 0.3)
        score -= gap_penalty

        # Consecutive failure penalty (cap at −0.2)
        failure_penalty = min(consec_fails * 0.05, 0.2)
        score -= failure_penalty

        # Clamp
        score = max(0.0, min(1.0, score))

        score_ts = datetime.now(timezone.utc).isoformat()

        health_score = WSHealthScore(
            score                  = round(score, 4),
            heartbeat_status       = status,
            last_heartbeat_age_s   = round(hb_age, 3),
            sequence_gaps_detected = gaps,
            reconnect_count        = reconnect_cnt,
            consecutive_failures   = consec_fails,
            message_rate_per_min   = round(rate, 2),
            last_score_ts          = score_ts,
        )

        # Check for DEAD status transition and fire side-effects
        self._check_dead_transition(health_score)

        return health_score

    def get_heartbeat_status(self) -> HeartbeatStatus:
        """Return the coarse heartbeat status based on last heartbeat age."""
        with self._lock:
            hb_age = time.time() - self._last_heartbeat_ts
            return self._heartbeat_status_unlocked(hb_age)

    def should_halt_entries(self) -> bool:
        """Return True if position entry should be blocked.

        Halts entries when the health score < 0.4 or the status is DEAD.
        Also logs a CRITICAL warning and nudges the drift detector if halting.
        """
        score_obj = self.get_health_score()
        halting = score_obj.score < 0.4 or score_obj.heartbeat_status is HeartbeatStatus.DEAD
        if halting:
            logger.critical(
                "WSGuardian: halting entries — score=%.4f status=%s",
                score_obj.score, score_obj.heartbeat_status.value,
            )
            # Nudge drift detector (no-op if not available; let detector handle)
            try:
                from runtime.drift_detector import get_detector  # type: ignore[import]
                det = get_detector()
                # Pass a sentinel price of 0 to signal feed outage
                det.update_price("WS_GUARDIAN", 0.0)
            except Exception:  # noqa: BLE001
                pass

        return halting

    def get_next_reconnect_delay(self) -> float:
        """Return the next exponential backoff delay in seconds.

        Formula: ``min(base ^ reconnect_count, 300.0)``
        """
        with self._lock:
            count = self._reconnect_count
        delay = min(math.pow(self._backoff_base, count), 300.0)
        return round(delay, 2)

    def reset_reconnect_count(self) -> None:
        """Reset the reconnect counter — call after a confirmed stable reconnect."""
        with self._lock:
            self._reconnect_count       = 0
            self._consecutive_failures  = 0
        logger.info("WSGuardian: reconnect count reset")

    def get_status_dict(self) -> Dict[str, object]:
        """Return a full diagnostic snapshot suitable for /api/diagnostics.

        The returned dict is a plain Python mapping of JSON-serialisable values.
        """
        hs = self.get_health_score()
        return {
            "score":                  hs.score,
            "heartbeat_status":       hs.heartbeat_status.value,
            "last_heartbeat_age_s":   hs.last_heartbeat_age_s,
            "sequence_gaps_detected": hs.sequence_gaps_detected,
            "reconnect_count":        hs.reconnect_count,
            "consecutive_failures":   hs.consecutive_failures,
            "message_rate_per_min":   hs.message_rate_per_min,
            "last_score_ts":          hs.last_score_ts,
            "should_halt_entries":    self.should_halt_entries(),
            "next_reconnect_delay_s": self.get_next_reconnect_delay(),
            "desync_count":           self._desync_count,
            "heartbeat_timeout_s":    self._heartbeat_timeout_s,
            "dead_timeout_s":         self._dead_timeout_s,
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _heartbeat_status_unlocked(self, hb_age: float) -> HeartbeatStatus:
        """Classify heartbeat status — caller must hold ``_lock``."""
        if hb_age < self._heartbeat_timeout_s:
            return HeartbeatStatus.HEALTHY
        if hb_age < self._dead_timeout_s:
            return HeartbeatStatus.STALE
        return HeartbeatStatus.DEAD

    def _message_rate_unlocked(self, now: float) -> float:
        """Compute messages/minute over the last 60 s — caller must hold ``_lock``."""
        window_start = now - 60.0
        count = sum(1 for ts in self._msg_timestamps if ts >= window_start)
        return float(count)  # already per-minute (60 s window)

    def _check_dead_transition(self, hs: WSHealthScore) -> None:
        """Fire Telegram alert on the first transition into DEAD status."""
        is_dead = hs.heartbeat_status is HeartbeatStatus.DEAD
        with self._lock:
            was_dead        = self._was_dead
            self._was_dead  = is_dead

        if is_dead and not was_dead:
            logger.critical(
                "WSGuardian: feed transitioned to DEAD "
                "(age=%.1fs score=%.4f)",
                hs.last_heartbeat_age_s, hs.score,
            )
            if _telegram_available:
                try:
                    _telegram_send(
                        f"🔴 <b>WS Feed DEAD</b>\n"
                        f"Heartbeat age: {hs.last_heartbeat_age_s:.1f}s\n"
                        f"Health score:  {hs.score:.4f}\n"
                        f"Entries halted until feed recovers."
                    )
                except Exception:  # noqa: BLE001
                    pass

    def _emit_event_unlocked(
        self,
        event_type: "_EventType",  # type: ignore[name-defined]
        payload: Dict[str, object],
    ) -> None:
        """Emit an event to the EventStore — best-effort, never raises."""
        if _event_store is None:
            return
        try:
            _event_store.append(
                event_type=event_type,
                trace_id="ws_guardian",
                payload=payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("WSGuardian: event store emit failed: %s", exc)


# ── Module-level singleton ────────────────────────────────────────────────────

_guardian: Optional[WSGuardian] = None
_guardian_lock = threading.Lock()


def get_guardian() -> WSGuardian:
    """Return the module-level WSGuardian singleton (lazy init with defaults).

    Thread-safe; safe to call from multiple threads simultaneously.
    """
    global _guardian
    if _guardian is None:
        with _guardian_lock:
            if _guardian is None:
                _guardian = WSGuardian()
    return _guardian
