"""Trading Intent Pipeline.

An Intent is the validated, schema-checked output of strategy logic.
It travels through:
  1. Schema validation (required fields, bounds)
  2. Regime compatibility check (forbidden regimes)
  3. Capital preservation check (state machine scalar)
  4. Staleness check (expires_at)
  5. Duplicate detection

Only intents that pass ALL gates reach the execution layer.

AI SAFETY: This module is authoritative over AI-generated outputs.
It may REJECT any intent regardless of AI confidence score.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("openclaw.runtime.intent_pipeline")


# ── Intent dataclass ──────────────────────────────────────────────────────────

@dataclass
class TradingIntent:
    symbol:              str
    strategy:            str
    action:              str       # "long" | "short" | "close"
    confidence:          float     # 0.0–1.0 (normalized)
    leverage_requested:  float     # must be <= risk engine cap
    size_pct:            float     # % of capital (0.0–100.0)
    sl_pct:              float
    tp_pct:              float
    regime_label:        str
    source:              str       # what generated this intent
    trace_id:            str       = field(default_factory=lambda: str(uuid.uuid4()))
    intent_id:           str       = field(default_factory=lambda: str(uuid.uuid4()))
    generated_at:        datetime  = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at:          datetime  = field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(seconds=90))
    metadata:            Dict[str, Any] = field(default_factory=dict)


@dataclass
class IntentVerdict:
    intent_id:  str
    approved:   bool
    reason:     str    # "" if approved, rejection reason if not
    risk_scalar: float  # 0.0–1.0 applied to size_pct
    adjusted_size_pct: float


# ── Validation gates ──────────────────────────────────────────────────────────

class IntentPipeline:
    """Validates and gates TradingIntents through all safety checks.

    Instantiate once per runtime session. Thread-safe dedup uses a
    (symbol, strategy, action) key with per-intent TTL expiry, so the
    same setup cannot fire twice within its expiry window.
    """

    MAX_LEVERAGE   = 5.0
    MAX_SIZE_PCT   = 4.0
    MAX_SL_PCT     = 10.0
    MIN_CONFIDENCE = 0.50

    def __init__(self,
                 capital_engine=None,    # CapitalPreservationEngine | None
                 regime_compat=None,     # module with is_strategy_compatible() | None
                 max_dedup_window: int = 1000):  # kept for API compat, unused
        self._capital   = capital_engine
        self._compat    = regime_compat
        # key=(symbol,strategy,action) → expiry datetime; evicted lazily
        self._dedup: Dict[Tuple[str, str, str], datetime] = {}
        self._dedup_lock = threading.Lock()   # atomic check-and-record

    def validate(self, intent: TradingIntent) -> IntentVerdict:
        """Run all gates. Returns IntentVerdict with approved=True/False."""

        # Gate 1: Schema / bounds
        ok, reason = self._validate_schema(intent)
        if not ok:
            logger.warning("INTENT REJECTED [schema] %s: %s", intent.intent_id, reason)
            return IntentVerdict(intent.intent_id, False, reason, 0.0, 0.0)

        # Gate 2: Staleness
        if datetime.now(timezone.utc) > intent.expires_at:
            reason = f"Intent expired at {intent.expires_at.isoformat()}"
            logger.warning("INTENT REJECTED [stale] %s", intent.intent_id)
            return IntentVerdict(intent.intent_id, False, reason, 0.0, 0.0)

        # Gate 3: Deduplication — atomic check-and-record under lock
        dedup_key = (intent.symbol, intent.strategy, intent.action)
        now = datetime.now(timezone.utc)
        with self._dedup_lock:
            if dedup_key in self._dedup and self._dedup[dedup_key] > now:
                reason = (f"Duplicate signal {intent.symbol}/{intent.strategy}/{intent.action} "
                          f"already approved within TTL")
                logger.info("INTENT REJECTED [duplicate] %s: %s", intent.intent_id, reason)
                return IntentVerdict(intent.intent_id, False, reason, 0.0, 0.0)
            # Reserve the slot immediately so concurrent signals can't both pass
            self._dedup[dedup_key] = now + timedelta(seconds=90)

        # Gate 4: Regime compatibility
        if self._compat is not None:
            ok, reason = self._check_regime(intent)
            if not ok:
                logger.info("INTENT REJECTED [regime] %s: %s", intent.intent_id, reason)
                return IntentVerdict(intent.intent_id, False, reason, 0.0, 0.0)

        # Gate 5: Capital preservation scalar
        risk_scalar = 1.0
        if self._capital is not None:
            risk_scalar = self._capital.get_risk_scalar()
            if risk_scalar == 0.0:
                reason = f"Capital engine HALT (state={self._capital.get_state().name})"
                logger.warning("INTENT REJECTED [capital_halt] %s", intent.intent_id)
                return IntentVerdict(intent.intent_id, False, reason, 0.0, 0.0)

        # All gates passed
        adjusted_size = min(intent.size_pct * risk_scalar, self.MAX_SIZE_PCT)
        self._record_seen(intent)

        logger.info(
            "INTENT APPROVED %s  %s %s  size=%.2f%%  scalar=%.2f",
            intent.intent_id, intent.action, intent.symbol, adjusted_size, risk_scalar,
        )
        return IntentVerdict(intent.intent_id, True, "", risk_scalar, adjusted_size)

    # ── Internal gates ────────────────────────────────────────────────────────

    def _validate_schema(self, intent: TradingIntent) -> Tuple[bool, str]:
        if intent.action not in ("long", "short", "close"):
            return False, f"Invalid action '{intent.action}'"
        if not (0.0 <= intent.confidence <= 1.0):
            return False, f"Confidence {intent.confidence} out of [0,1]"
        if intent.confidence < self.MIN_CONFIDENCE:
            return False, f"Confidence {intent.confidence:.2f} < min {self.MIN_CONFIDENCE}"
        if intent.leverage_requested > self.MAX_LEVERAGE:
            return False, f"Leverage {intent.leverage_requested}x > max {self.MAX_LEVERAGE}x"
        if intent.size_pct > self.MAX_SIZE_PCT:
            return False, f"size_pct {intent.size_pct:.1f}% > max {self.MAX_SIZE_PCT}%"
        if intent.sl_pct > self.MAX_SL_PCT or intent.sl_pct <= 0:
            return False, f"sl_pct {intent.sl_pct} out of (0, {self.MAX_SL_PCT}]"
        if intent.tp_pct <= 0:
            return False, f"tp_pct {intent.tp_pct} must be positive"
        if not intent.symbol or ("-" not in intent.symbol and "_" not in intent.symbol):
            return False, f"Invalid symbol '{intent.symbol}'"
        return True, ""

    def _check_regime(self, intent: TradingIntent) -> Tuple[bool, str]:
        try:
            compatible = self._compat.is_strategy_compatible(
                intent.strategy, intent.regime_label
            )
            if not compatible:
                reason = self._compat.get_incompatibility_reason(
                    intent.strategy, intent.regime_label
                )
                return False, reason
        except Exception as exc:
            # Deny-by-default: a broken classifier must not allow forbidden strategies
            logger.warning("Regime compat check failed: %s — blocking intent (fail-safe)", exc)
            return False, f"Regime check unavailable — blocking {intent.strategy} as fail-safe"
        return True, ""

    def _record_seen(self, intent: TradingIntent) -> None:
        # Slot already reserved in Gate 3; just refresh expiry and evict old entries
        now = datetime.now(timezone.utc)
        with self._dedup_lock:
            key = (intent.symbol, intent.strategy, intent.action)
            self._dedup[key] = now + timedelta(seconds=90)
            # Evict expired entries; hard-cap at 10k to prevent unbounded growth
            expired = [k for k, exp in self._dedup.items() if exp <= now]
            for k in expired:
                del self._dedup[k]
            if len(self._dedup) > 10_000:
                oldest = sorted(self._dedup.items(), key=lambda x: x[1])[:5_000]
                self._dedup = dict(oldest)
