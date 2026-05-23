"""Exchange drift detection engine for OpenClaw.

Monitors temporal divergence between the bot's local state and the exchange:
stale prices, stale balances, frozen exchange data, websocket desyncs, and
position desyncs.  Designed to run continuously from the scan loop or the
ContinuousReconciliationScheduler.

The structural mismatch concern (ghost / orphan positions) is handled by
reconciliation.py.  This module focuses on *temporal* divergence — data that
was once correct but has gone stale or frozen over time.

Integration points
------------------
- ``DriftDetector.detect_all()`` → called by ContinuousReconciliationScheduler
- ``DriftDetector.should_halt_entries()`` → checked before _open_position()
- ``DriftDetector.update_price/balance/position()`` → called from scan loop

Persistence
-----------
Drift events are appended to data/drift_events.jsonl (fcntl-locked).
File is rotated when it exceeds 1000 lines.
"""
from __future__ import annotations

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
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.runtime.drift_detector")

_DRIFT_JSONL     = Path("data/drift_events.jsonl")
_MAX_LINES       = 1000
_FREEZE_SAMPLES  = 5   # consecutive identical prices → frozen

# ── Prometheus (optional) ─────────────────────────────────────────────────────

try:
    from runtime.metrics import get_registry as _get_registry  # type: ignore[import]
    _metrics = _get_registry()
except Exception:  # noqa: BLE001
    _metrics = None


# ── Enums ─────────────────────────────────────────────────────────────────────

class DriftSeverity(str, Enum):
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"


class DriftType(str, Enum):
    STALE_PRICE             = "STALE_PRICE"
    STALE_BALANCE           = "STALE_BALANCE"
    STALE_ORDER_STATE       = "STALE_ORDER_STATE"
    FROZEN_EXCHANGE_DATA    = "FROZEN_EXCHANGE_DATA"
    WEBSOCKET_DESYNC        = "WEBSOCKET_DESYNC"
    DUPLICATE_FILL          = "DUPLICATE_FILL"
    OUT_OF_ORDER_EVENT      = "OUT_OF_ORDER_EVENT"
    MISSING_EXECUTION_EVENT = "MISSING_EXECUTION_EVENT"
    POSITION_DESYNC         = "POSITION_DESYNC"


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class DriftEvent:
    """Record of a single detected drift condition."""

    event_type:     str                        # DriftType value
    severity:       str                        # DriftSeverity value
    symbol:         str                        # affected symbol, or "" for global events
    description:    str

    local_value:    Any  = None
    exchange_value: Any  = None

    detected_at:    float = field(default_factory=time.time)   # epoch seconds
    resolved_at:    float = 0.0
    resolved:       bool  = False


# ── Detector ──────────────────────────────────────────────────────────────────

class DriftDetector:
    """Temporal drift detection engine.

    Parameters
    ----------
    stale_price_warn_s :
        Seconds before a price update is considered WARNING-level stale (default 60).
    stale_price_crit_s :
        Seconds before a price update is considered CRITICAL-level stale (default 120).
    stale_balance_warn_s :
        Seconds before balance is WARNING stale (default 120).
    stale_balance_crit_s :
        Seconds before balance is CRITICAL stale (default 300).
    frozen_samples :
        Number of consecutive identical price samples before FROZEN is raised (default 5).
    ws_desync_warn_s :
        Seconds since last WS event before WARNING (default 30).
    ws_desync_crit_s :
        Seconds since last WS event before CRITICAL (default 90).
    """

    def __init__(
        self,
        stale_price_warn_s:    float = 60.0,
        stale_price_crit_s:    float = 120.0,
        stale_balance_warn_s:  float = 120.0,
        stale_balance_crit_s:  float = 300.0,
        frozen_samples:        int   = _FREEZE_SAMPLES,
        ws_desync_warn_s:      float = 30.0,
        ws_desync_crit_s:      float = 90.0,
    ) -> None:
        self._lock = threading.Lock()

        # Thresholds
        self._stale_price_warn_s   = stale_price_warn_s
        self._stale_price_crit_s   = stale_price_crit_s
        self._stale_balance_warn_s = stale_balance_warn_s
        self._stale_balance_crit_s = stale_balance_crit_s
        self._frozen_samples       = frozen_samples
        self._ws_desync_warn_s     = ws_desync_warn_s
        self._ws_desync_crit_s     = ws_desync_crit_s

        # State tracked per symbol
        self._price_last_ts:      Dict[str, float] = {}   # symbol → epoch s
        self._price_last_value:   Dict[str, float] = {}   # symbol → price
        self._price_freeze_count: Dict[str, int]   = {}   # symbol → consecutive same count

        # Balance state
        self._balance_last_ts:    float = 0.0
        self._balance_last_value: float = 0.0

        # Local positions snapshot (symbol → position dict)
        self._local_positions:    Dict[str, Any] = {}

        # Last websocket event timestamp
        self._last_ws_ts: float = time.time()

        # Active drift events (keyed by (event_type, symbol))
        self._active_events: Dict[tuple, DriftEvent] = {}

        _DRIFT_JSONL.parent.mkdir(parents=True, exist_ok=True)

    # ── Update methods (called from scan loop) ────────────────────────────────

    def update_price(self, symbol: str, price: float, ts_ms: float) -> None:
        """Record the latest received market price for *symbol*.

        Parameters
        ----------
        symbol :
            Instrument symbol, e.g. "BTCUSD-PERP" or "BTC_USDT".
        price :
            Latest mid/last price.
        ts_ms :
            Timestamp in milliseconds (epoch).  Converted to seconds internally.
        """
        ts_s = ts_ms / 1000.0 if ts_ms > 1e9 else ts_ms

        with self._lock:
            prev = self._price_last_value.get(symbol)
            if prev is not None and abs(prev - price) < 1e-10:
                self._price_freeze_count[symbol] = (
                    self._price_freeze_count.get(symbol, 0) + 1
                )
            else:
                self._price_freeze_count[symbol] = 0

            self._price_last_value[symbol] = price
            self._price_last_ts[symbol]    = ts_s
            self._last_ws_ts               = time.time()

    def update_balance(self, balance: float, ts_ms: float) -> None:
        """Record the latest received account balance.

        Parameters
        ----------
        balance :
            Total equity / available balance in USD.
        ts_ms :
            Timestamp in milliseconds (epoch).
        """
        ts_s = ts_ms / 1000.0 if ts_ms > 1e9 else ts_ms
        with self._lock:
            self._balance_last_value = balance
            self._balance_last_ts    = ts_s
            self._last_ws_ts         = time.time()

    def update_position(self, symbol: str, local_pos: Any) -> None:
        """Record the bot's local position snapshot for *symbol*.

        Parameters
        ----------
        symbol :
            Instrument symbol.
        local_pos :
            The position dict (or None / empty dict to clear the position).
        """
        with self._lock:
            if local_pos:
                self._local_positions[symbol] = local_pos
            else:
                self._local_positions.pop(symbol, None)

    def notify_ws_event(self) -> None:
        """Signal that a websocket event was just received (heartbeat, trade, etc.)."""
        with self._lock:
            self._last_ws_ts = time.time()

    # ── Staleness checks ──────────────────────────────────────────────────────

    def check_price_staleness(
        self,
        symbol: str,
        max_age_seconds: float = 60.0,
    ) -> Optional[DriftEvent]:
        """Return a DriftEvent if the last price update for *symbol* is stale.

        Returns None if the price is fresh or if no price has been received yet
        (we cannot know staleness without a baseline).
        """
        with self._lock:
            last_ts = self._price_last_ts.get(symbol)

        if last_ts is None:
            return None  # never received — structural, not temporal

        age = time.time() - last_ts
        if age < max_age_seconds:
            return None

        severity = (
            DriftSeverity.CRITICAL
            if age > self._stale_price_crit_s
            else DriftSeverity.WARNING
        )
        return DriftEvent(
            event_type   = DriftType.STALE_PRICE.value,
            severity     = severity.value,
            symbol       = symbol,
            description  = f"Price for {symbol} is {age:.0f}s stale (threshold {max_age_seconds}s)",
            local_value  = f"last_ts={last_ts:.0f}",
            exchange_value = f"age={age:.0f}s",
        )

    def check_balance_staleness(
        self,
        max_age_seconds: float = 120.0,
    ) -> Optional[DriftEvent]:
        """Return a DriftEvent if the balance hasn't been updated recently."""
        with self._lock:
            last_ts = self._balance_last_ts

        if last_ts == 0.0:
            return None  # no baseline yet

        age = time.time() - last_ts
        if age < max_age_seconds:
            return None

        severity = (
            DriftSeverity.CRITICAL
            if age > self._stale_balance_crit_s
            else DriftSeverity.WARNING
        )
        return DriftEvent(
            event_type   = DriftType.STALE_BALANCE.value,
            severity     = severity.value,
            symbol       = "",
            description  = f"Account balance is {age:.0f}s stale (threshold {max_age_seconds}s)",
            local_value  = f"last_ts={last_ts:.0f}",
            exchange_value = f"age={age:.0f}s",
        )

    def check_exchange_frozen(
        self,
        symbol: str,
        max_age_seconds: float = 30.0,
    ) -> Optional[DriftEvent]:
        """Return a DriftEvent if the price for *symbol* appears frozen.

        Frozen is detected either by:
        1. *frozen_samples* consecutive identical price readings, or
        2. No price update received in *max_age_seconds*.
        """
        with self._lock:
            freeze_count = self._price_freeze_count.get(symbol, 0)
            last_ts      = self._price_last_ts.get(symbol)
            last_price   = self._price_last_value.get(symbol)

        if freeze_count >= self._frozen_samples:
            return DriftEvent(
                event_type   = DriftType.FROZEN_EXCHANGE_DATA.value,
                severity     = DriftSeverity.WARNING.value,
                symbol       = symbol,
                description  = (
                    f"{symbol} price unchanged for {freeze_count} consecutive "
                    f"samples (threshold {self._frozen_samples})"
                ),
                local_value  = f"consecutive_same={freeze_count}",
                exchange_value = f"price={last_price}",
            )

        if last_ts is not None:
            age = time.time() - last_ts
            if age > max_age_seconds:
                return DriftEvent(
                    event_type   = DriftType.FROZEN_EXCHANGE_DATA.value,
                    severity     = DriftSeverity.WARNING.value,
                    symbol       = symbol,
                    description  = (
                        f"{symbol} has not received a price update in {age:.0f}s"
                    ),
                    local_value  = f"last_ts={last_ts:.0f}",
                    exchange_value = f"age={age:.0f}s",
                )

        return None

    def check_websocket_desync(self) -> Optional[DriftEvent]:
        """Return a DriftEvent if no websocket event has been received recently."""
        with self._lock:
            last_ws = self._last_ws_ts

        age = time.time() - last_ws
        if age < self._ws_desync_warn_s:
            return None

        severity = (
            DriftSeverity.CRITICAL
            if age > self._ws_desync_crit_s
            else DriftSeverity.WARNING
        )
        return DriftEvent(
            event_type   = DriftType.WEBSOCKET_DESYNC.value,
            severity     = severity.value,
            symbol       = "",
            description  = f"No websocket event received for {age:.0f}s",
            local_value  = f"last_ws_ts={last_ws:.0f}",
            exchange_value = f"age={age:.0f}s",
        )

    # ── Aggregate detection ───────────────────────────────────────────────────

    def detect_all(
        self,
        local_positions: Dict[str, Any],
        current_prices:  Dict[str, float],
    ) -> List[DriftEvent]:
        """Run all drift checks and return newly raised events.

        Parameters
        ----------
        local_positions :
            Mapping of symbol → position dict (current bot view).
        current_prices :
            Mapping of symbol → current price (latest known to the bot).

        Side effects
        ------------
        - Updates self._local_positions from *local_positions*.
        - Raises / resolves events in self._active_events.
        - Persists newly raised events to drift_events.jsonl.
        - Emits Prometheus metrics.
        """
        # Sync local position snapshots
        for sym, pos in local_positions.items():
            self.update_position(sym, pos)

        new_events: List[DriftEvent] = []
        detected_keys: set = set()

        # ── Price staleness for each known symbol ──────────────────────────
        all_symbols = set(current_prices.keys()) | set(self._price_last_ts.keys())
        for sym in all_symbols:
            evt = self.check_price_staleness(sym, self._stale_price_warn_s)
            if evt:
                key = (DriftType.STALE_PRICE.value, sym)
                detected_keys.add(key)
                if self._register_event(key, evt):
                    new_events.append(evt)

        # ── Balance staleness ──────────────────────────────────────────────
        bal_evt = self.check_balance_staleness(self._stale_balance_warn_s)
        if bal_evt:
            key = (DriftType.STALE_BALANCE.value, "")
            detected_keys.add(key)
            if self._register_event(key, bal_evt):
                new_events.append(bal_evt)

        # ── Frozen data per symbol ────────────────────────────────────────
        for sym in all_symbols:
            evt = self.check_exchange_frozen(sym)
            if evt:
                key = (DriftType.FROZEN_EXCHANGE_DATA.value, sym)
                detected_keys.add(key)
                if self._register_event(key, evt):
                    new_events.append(evt)

        # ── WebSocket desync ──────────────────────────────────────────────
        ws_evt = self.check_websocket_desync()
        if ws_evt:
            key = (DriftType.WEBSOCKET_DESYNC.value, "")
            detected_keys.add(key)
            if self._register_event(key, ws_evt):
                new_events.append(ws_evt)

        # ── Position desync: local position without recent price ──────────
        with self._lock:
            local_pos_copy = dict(self._local_positions)

        for sym, pos in local_pos_copy.items():
            if sym not in self._price_last_ts:
                evt = DriftEvent(
                    event_type   = DriftType.POSITION_DESYNC.value,
                    severity     = DriftSeverity.WARNING.value,
                    symbol       = sym,
                    description  = (
                        f"Open position for {sym} but no price data received"
                    ),
                    local_value  = pos,
                    exchange_value = None,
                )
                key = (DriftType.POSITION_DESYNC.value, sym)
                detected_keys.add(key)
                if self._register_event(key, evt):
                    new_events.append(evt)

        # ── Auto-resolve events whose condition is no longer true ─────────
        self._auto_resolve(detected_keys)

        # ── Persist new events ────────────────────────────────────────────
        for evt in new_events:
            self._persist_event(evt)
            logger.warning(
                "[DRIFT %s] %s — %s",
                evt.severity, evt.event_type, evt.description,
            )

        # ── Prometheus counters ───────────────────────────────────────────
        self._emit_metrics()

        return new_events

    # ── Query API ─────────────────────────────────────────────────────────────

    def get_active_drift_events(self) -> List[DriftEvent]:
        """Return all unresolved drift events (snapshot, newest detected first)."""
        with self._lock:
            events = [e for e in self._active_events.values() if not e.resolved]
        return sorted(events, key=lambda e: e.detected_at, reverse=True)

    def resolve_event(self, event_type: str, symbol: str = "") -> None:
        """Mark a drift event as resolved.

        Parameters
        ----------
        event_type :
            DriftType value string, e.g. "STALE_PRICE".
        symbol :
            Symbol the event relates to (empty string for global events).
        """
        key = (event_type, symbol)
        with self._lock:
            evt = self._active_events.get(key)
            if evt and not evt.resolved:
                evt.resolved    = True
                evt.resolved_at = time.time()
                logger.info(
                    "[DRIFT RESOLVED] %s / %s after %.0fs",
                    event_type, symbol or "global",
                    evt.resolved_at - evt.detected_at,
                )

    def should_halt_entries(self) -> bool:
        """Return True if any active CRITICAL drift event is present.

        Called by CryptoComBot before _open_position().
        """
        with self._lock:
            for evt in self._active_events.values():
                if not evt.resolved and evt.severity == DriftSeverity.CRITICAL.value:
                    return True
        return False

    def get_drift_summary(self) -> Dict[str, Any]:
        """Return a compact summary dict suitable for logging or the dashboard.

        Returns
        -------
        dict with keys: total, critical, warning, info, oldest_event_age_s
        """
        with self._lock:
            active = [e for e in self._active_events.values() if not e.resolved]

        now = time.time()
        critical = sum(1 for e in active if e.severity == DriftSeverity.CRITICAL.value)
        warning  = sum(1 for e in active if e.severity == DriftSeverity.WARNING.value)
        info     = sum(1 for e in active if e.severity == DriftSeverity.INFO.value)
        oldest   = max((now - e.detected_at for e in active), default=0.0)

        return {
            "total":              len(active),
            "critical":           critical,
            "warning":            warning,
            "info":               info,
            "oldest_event_age_s": round(oldest, 1),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _register_event(self, key: tuple, evt: DriftEvent) -> bool:
        """Register a new drift event.  Returns True if this is a new event."""
        with self._lock:
            existing = self._active_events.get(key)
            if existing and not existing.resolved:
                # Update severity in-place (condition may have worsened)
                existing.severity = evt.severity
                return False
            # New event or previously resolved event re-occurring
            self._active_events[key] = evt
        return True

    def _auto_resolve(self, currently_detected_keys: set) -> None:
        """Resolve active events whose condition is no longer detected."""
        now = time.time()
        with self._lock:
            for key, evt in self._active_events.items():
                if not evt.resolved and key not in currently_detected_keys:
                    evt.resolved    = True
                    evt.resolved_at = now

    def _persist_event(self, evt: DriftEvent) -> None:
        """Append one JSON line to data/drift_events.jsonl (fcntl-locked).

        Rotates the file if it exceeds _MAX_LINES.
        """
        payload = json.dumps(asdict(evt), default=str)
        try:
            with _DRIFT_JSONL.open("a", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    fh.write(payload + "\n")
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError as exc:
            logger.warning("_persist_event: cannot write to %s — %s",
                           _DRIFT_JSONL, exc)
            return

        self._rotate_if_needed()

    def _rotate_if_needed(self) -> None:
        """Rotate drift_events.jsonl if it exceeds _MAX_LINES.

        Keeps the most recent _MAX_LINES // 2 lines after rotation.
        """
        try:
            if not _DRIFT_JSONL.exists():
                return
            with _DRIFT_JSONL.open("r", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_SH)
                try:
                    lines = fh.readlines()
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)

            if len(lines) <= _MAX_LINES:
                return

            keep = lines[-((_MAX_LINES) // 2):]

            fd, tmp_path = tempfile.mkstemp(
                dir    = _DRIFT_JSONL.parent,
                prefix = ".tmp_drift_",
                suffix = ".jsonl",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.writelines(keep)
                os.replace(tmp_path, _DRIFT_JSONL)
                logger.info(
                    "_rotate_if_needed: rotated %s — kept %d/%d lines",
                    _DRIFT_JSONL, len(keep), len(lines),
                )
            except Exception:  # noqa: BLE001
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

        except OSError as exc:
            logger.warning("_rotate_if_needed: rotation failed — %s", exc)

    def _emit_metrics(self) -> None:
        """Push drift event counts to Prometheus if registry is available."""
        if _metrics is None:
            return
        try:
            summary = self.get_drift_summary()
            if summary["critical"] > 0:
                _metrics.set_reconciliation_mismatches(
                    summary["total"]
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("_emit_metrics error: %s", exc)
