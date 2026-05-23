"""Dynamic scan interval engine.

Determines the bot scan interval based on current market regime from
IntentPipeline's last classification.

Rules:
- TRENDING_BULL / TRENDING_BEAR / MOMENTUM_BULL / MOMENTUM_BEAR /
  VOL_EXPANSION / NEWS_SPIKE → 15s (fast)
- RANGING / MEAN_REVERTING / VOL_COMPRESSION / UNKNOWN → 60s (slow)
- LIQUIDITY_DROUGHT → 60s (slow)
- No regime data → 60s fallback (fail closed)

Bounds: interval is always in [15, 60] seconds.
Debounce: interval change requires 2 consecutive matching regimes before applying.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("openclaw.runtime.scan_interval_engine")

# ── Regime classification sets ────────────────────────────────────────────────

_FAST_REGIMES = frozenset({
    "TRENDING_BULL",
    "TRENDING_BEAR",
    "MOMENTUM_BULL",
    "MOMENTUM_BEAR",
    "VOL_EXPANSION",
    "NEWS_SPIKE",
})

_SLOW_REGIMES = frozenset({
    "RANGING",
    "MEAN_REVERTING",
    "VOL_COMPRESSION",
    "UNKNOWN",
    "LIQUIDITY_DROUGHT",
})


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class IntervalTransition:
    ts: str                  # ISO8601
    trace_id: str            # UUID4
    old_interval: int
    new_interval: int
    regime: str
    source: str              # "intent_pipeline" | "position_labels" | "fallback"
    consecutive_count: int


# ── Engine ────────────────────────────────────────────────────────────────────


class ScanIntervalEngine:
    """Computes and debounces the bot scan interval based on market regime.

    Thread-safe.  Fail-closed: exceptions swallowed after logging.
    """

    def __init__(
        self,
        min_interval: int = 15,
        max_interval: int = 60,
        default_interval: int = 60,
        debounce_count: int = 2,
        audit_path: str = "data/scan_interval_audit.jsonl",
    ) -> None:
        self._min_interval     = min_interval
        self._max_interval     = max_interval
        self._default_interval = max(min_interval, min(max_interval, default_interval))
        self._debounce_count   = debounce_count
        self._audit_path       = audit_path

        self._lock              = threading.Lock()
        self._current_interval  = self._default_interval
        self._pending_interval  = self._default_interval
        self._consecutive_count = 0
        self._last_transition: Optional[IntervalTransition] = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def compute_interval(
        self,
        regime: Optional[str],
        position_regimes: "set[str]",
    ) -> int:
        """Compute new interval with debounce. Returns current effective interval.

        Does NOT write to audit log — call apply() to commit the change.
        """
        try:
            # Derive target interval from regime signals
            if regime is not None:
                raw_interval = self._regime_to_interval(regime)
            elif position_regimes:
                # If any position is in a fast regime → fast; else slow
                if position_regimes & _FAST_REGIMES:
                    raw_interval = self._min_interval
                else:
                    raw_interval = self._max_interval
            else:
                # No signal at all → fail closed (slow)
                raw_interval = self._default_interval

            with self._lock:
                if raw_interval == self._pending_interval:
                    self._consecutive_count += 1
                else:
                    self._pending_interval  = raw_interval
                    self._consecutive_count = 1

                if self._consecutive_count >= self._debounce_count:
                    return raw_interval
                else:
                    return self._current_interval

        except Exception as exc:
            logger.debug("ScanIntervalEngine.compute_interval error (non-fatal): %s", exc)
            return self._current_interval

    def apply(
        self,
        new_interval: int,
        old_interval: int,
        regime: str,
        source: str,
    ) -> Optional[IntervalTransition]:
        """Commit interval change. Write audit record. Returns transition or None if no change."""
        try:
            if new_interval == old_interval:
                return None

            transition = IntervalTransition(
                ts=datetime.now(timezone.utc).isoformat(),
                trace_id=str(uuid.uuid4()),
                old_interval=old_interval,
                new_interval=new_interval,
                regime=regime,
                source=source,
                consecutive_count=self._consecutive_count,
            )

            with self._lock:
                self._current_interval  = new_interval
                self._last_transition   = transition
                self._consecutive_count = 0

            self._write_audit(transition)
            return transition

        except Exception as exc:
            logger.debug("ScanIntervalEngine.apply error (non-fatal): %s", exc)
            return None

    def get_current_interval(self) -> int:
        with self._lock:
            return self._current_interval

    def get_last_transition(self) -> Optional[IntervalTransition]:
        with self._lock:
            return self._last_transition

    def get_status(self) -> dict:
        """Return engine telemetry for the dashboard."""
        with self._lock:
            lt = self._last_transition
        return {
            "current_interval":  self._current_interval,
            "pending_interval":  self._pending_interval,
            "consecutive_count": self._consecutive_count,
            "debounce_count":    self._debounce_count,
            "min_interval":      self._min_interval,
            "max_interval":      self._max_interval,
            "last_transition": {
                "ts":          lt.ts,
                "old_interval": lt.old_interval,
                "new_interval": lt.new_interval,
                "regime":      lt.regime,
                "source":      lt.source,
            } if lt else None,
        }

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _regime_to_interval(self, regime: str) -> int:
        """Map a regime string to a scan interval, clamped to [min, max]."""
        if regime in _FAST_REGIMES:
            raw = self._min_interval   # 15s
        else:
            raw = self._max_interval   # 60s (includes SLOW_REGIMES + anything unknown)
        return max(self._min_interval, min(self._max_interval, raw))

    def _write_audit(self, transition: IntervalTransition) -> None:
        """Append an audit record atomically with fcntl.LOCK_EX."""
        try:
            record = {
                "ts":               transition.ts,
                "trace_id":         transition.trace_id,
                "old_interval":     transition.old_interval,
                "new_interval":     transition.new_interval,
                "regime":           transition.regime,
                "source":           transition.source,
                "consecutive_count": transition.consecutive_count,
            }
            path = self._audit_path
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    fh.write(json.dumps(record) + "\n")
                    fh.flush()
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
        except Exception as exc:
            logger.debug("ScanIntervalEngine audit write failed (non-fatal): %s", exc)


# ── Module singleton ──────────────────────────────────────────────────────────

_engine: Optional[ScanIntervalEngine] = None
_engine_lock = threading.Lock()


def get_scan_engine() -> ScanIntervalEngine:
    """Return the module-level ScanIntervalEngine singleton (double-checked locking)."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = ScanIntervalEngine()
    return _engine
