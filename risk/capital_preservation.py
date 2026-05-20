"""Capital Preservation Engine — Phase 15.

Implements a capital state machine with automatic threshold-based transitions.
This module is AUTHORITATIVE over capital preservation decisions.

AI SAFETY CONTRACT:
- This module NEVER calls exchange APIs directly.
- It NEVER bypasses the kill switch.
- It only changes internal state (flags, scalars, cooldown bools).
- Execution authority remains with ExecutionManager.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.risk.capital_preservation")


# ── State enum ────────────────────────────────────────────────────────────────

class CapitalState(Enum):
    """Capital preservation state machine states.

    Transitions are AUTOMATIC and may only decrease severity:
        SAFE → DEFENSIVE → CRITICAL → EMERGENCY_HALT

    Recovery from HALT requires explicit governance approval.
    Recovery from CRITICAL/lower requires manual_reset() by an operator.
    DEFENSIVE → SAFE is the only auto-recovery path (on equity recovery).
    """
    SAFE           = "SAFE"
    DEFENSIVE      = "DEFENSIVE"
    CRITICAL       = "CRITICAL"
    EMERGENCY_HALT = "EMERGENCY_HALT"

    def severity(self) -> int:
        """Higher value = more severe."""
        return {
            CapitalState.SAFE:           0,
            CapitalState.DEFENSIVE:      1,
            CapitalState.CRITICAL:       2,
            CapitalState.EMERGENCY_HALT: 3,
        }[self]


# ── Rolling drawdown tracker ──────────────────────────────────────────────────

class RollingDrawdownTracker:
    """Tracks equity peaks and drawdowns over daily, weekly, and monthly windows.

    All timestamps are UTC. Equity samples are stored with their UTC datetime
    so that the correct window boundaries can be computed on each update.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Deques of (datetime_utc, equity) tuples, unbounded but pruned on read.
        self._samples: deque = deque()

        # All-time peak (never reset without explicit governance action).
        self._alltime_peak: float = 0.0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def record(self, equity: float, ts: Optional[datetime] = None) -> None:
        """Record a new equity sample."""
        if ts is None:
            ts = datetime.now(timezone.utc)
        with self._lock:
            self._samples.append((ts, equity))
            if equity > self._alltime_peak:
                self._alltime_peak = equity
            self._prune()

    def daily_drawdown(self, as_of: Optional[datetime] = None) -> float:
        """Return the maximum drawdown fraction within the current UTC day."""
        return self._window_drawdown(hours=24, as_of=as_of)

    def weekly_drawdown(self, as_of: Optional[datetime] = None) -> float:
        """Return the maximum drawdown fraction within the past 7 days."""
        return self._window_drawdown(hours=7 * 24, as_of=as_of)

    def monthly_drawdown(self, as_of: Optional[datetime] = None) -> float:
        """Return the maximum drawdown fraction within the past 30 days."""
        return self._window_drawdown(hours=30 * 24, as_of=as_of)

    def alltime_peak(self) -> float:
        with self._lock:
            return self._alltime_peak

    def current_equity(self) -> float:
        """Return the most recently recorded equity, or 0.0 if no samples."""
        with self._lock:
            if self._samples:
                return self._samples[-1][1]
            return 0.0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _window_drawdown(self, hours: int, as_of: Optional[datetime] = None) -> float:
        """Compute the drawdown from the peak within the rolling window."""
        if as_of is None:
            as_of = datetime.now(timezone.utc)

        from datetime import timedelta
        cutoff = as_of - timedelta(hours=hours)

        with self._lock:
            window = [(ts, eq) for ts, eq in self._samples if ts >= cutoff]

        if not window:
            return 0.0

        peak = max(eq for _, eq in window)
        trough = min(eq for _, eq in window)

        if peak <= 0:
            return 0.0

        return (peak - trough) / peak

    def _prune(self) -> None:
        """Remove samples older than 31 days to bound memory usage."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=31)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


# ── Loss streak tracker ───────────────────────────────────────────────────────

class LossStreakTracker:
    """Counts consecutive losses; resets to zero on any win."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._streak: int = 0
        self._total_losses: int = 0
        self._total_wins: int = 0

    def record_trade(self, pnl: float) -> None:
        """Record a completed trade PnL. Positive = win, negative = loss."""
        with self._lock:
            if pnl > 0:
                self._streak = 0
                self._total_wins += 1
            else:
                self._streak += 1
                self._total_losses += 1

    @property
    def streak(self) -> int:
        with self._lock:
            return self._streak

    @property
    def total_losses(self) -> int:
        with self._lock:
            return self._total_losses

    @property
    def total_wins(self) -> int:
        with self._lock:
            return self._total_wins

    def reset(self) -> None:
        with self._lock:
            self._streak = 0


# ── Capital preservation engine ───────────────────────────────────────────────

class CapitalPreservationEngine:
    """Monitors capital drawdown and loss streaks; manages the capital state machine.

    The engine ONLY changes internal state.  It never calls exchange APIs and
    never bypasses the kill switch.  ExecutionManager reads get_risk_scalar(),
    get_state(), and should_flatten_all() to make execution decisions.

    Thread-safe: all public methods acquire self._lock before mutating state.
    """

    # ── Defaults ──────────────────────────────────────────────────────────
    DEFAULT_THRESHOLDS: Dict[str, float] = {
        "daily_dd_limit":          0.05,   # 5%  → DEFENSIVE
        "weekly_dd_limit":         0.10,   # 10% → CRITICAL
        "monthly_dd_limit":        0.20,   # 20% → EMERGENCY_HALT
        "loss_streak_defensive":   4,      # 4 consecutive losses → DEFENSIVE
        "loss_streak_critical":    7,      # 7 consecutive losses → CRITICAL
        "max_correlated_exposure": 0.40,   # 40% of portfolio in correlated assets
        "weekend_risk_scalar":     0.50,   # 50% risk during weekend window
    }

    def __init__(self, thresholds: Optional[Dict[str, float]] = None) -> None:
        self._lock = threading.Lock()

        # Merge caller-provided thresholds over defaults.
        self._thresholds: Dict[str, float] = dict(self.DEFAULT_THRESHOLDS)
        if thresholds:
            self._thresholds.update(thresholds)

        self._state: CapitalState = CapitalState.SAFE
        self._drawdown_tracker = RollingDrawdownTracker()
        self._loss_streak_tracker = LossStreakTracker()

        # Immutable append-only operator reset log (in-memory only here;
        # governance module writes to disk).
        self._reset_log: List[Dict[str, Any]] = []

        # Flag set when equity recovers after DEFENSIVE: allows auto-recovery.
        self._defensive_recovery_equity: Optional[float] = None

        logger.info("CapitalPreservationEngine initialised with thresholds: %s",
                    self._thresholds)

    # ── Core update method ────────────────────────────────────────────────

    def update(self, current_equity: float,
               trade_pnl: Optional[float] = None) -> None:
        """Update rolling windows and check thresholds; transition states as needed.

        Args:
            current_equity: Current portfolio equity in USD.
            trade_pnl:      Optional PnL of the most recently closed trade.
                            Positive = win, negative = loss.
        """
        self._drawdown_tracker.record(current_equity)

        if trade_pnl is not None:
            self._loss_streak_tracker.record_trade(trade_pnl)

        with self._lock:
            self._evaluate_transitions(current_equity)

    # ── State accessors ───────────────────────────────────────────────────

    def get_state(self) -> CapitalState:
        with self._lock:
            return self._state

    def get_risk_scalar(self) -> float:
        """Return position-size multiplier: 0.0 (HALT) → 1.0 (SAFE).

        Also applies weekend risk reduction if the weekend window is active.
        """
        with self._lock:
            base = self._state_risk_scalar(self._state)

        # Weekend reduction applies on top of state scalar.
        if self.is_weekend_risk_reduction_active():
            base *= self._thresholds["weekend_risk_scalar"]

        return base

    def should_flatten_all(self) -> bool:
        """Return True if all open positions must be closed immediately."""
        with self._lock:
            if self._state == CapitalState.EMERGENCY_HALT:
                return True

            # Also flatten if monthly drawdown threshold is breached (belt +
            # braces: transition logic should have already set EMERGENCY_HALT).
            monthly_dd = self._drawdown_tracker.monthly_drawdown()
            return monthly_dd >= self._thresholds["monthly_dd_limit"]

    def get_leverage_cap(self, base_leverage: float) -> float:
        """Scale down leverage based on current capital state.

        Returns:
            Adjusted leverage cap.  0.0 in EMERGENCY_HALT.
        """
        with self._lock:
            state = self._state

        scalars = {
            CapitalState.SAFE:           1.0,
            CapitalState.DEFENSIVE:      0.5,
            CapitalState.CRITICAL:       0.2,
            CapitalState.EMERGENCY_HALT: 0.0,
        }
        return base_leverage * scalars[state]

    def check_correlated_exposure(self, positions: List[Dict]) -> bool:
        """Return True if current correlated exposure exceeds the configured max.

        Each position dict must contain at minimum:
            - "notional": float   — absolute USD notional of position
            - "correlated": bool  — whether this position is correlated to the
                                    candidate new position

        Args:
            positions: List of currently open position dicts.

        Returns:
            True  → adding a new correlated position would breach the limit.
            False → exposure is within limits.
        """
        total_notional = sum(abs(p.get("notional", 0.0)) for p in positions)
        correlated_notional = sum(
            abs(p.get("notional", 0.0))
            for p in positions
            if p.get("correlated", False)
        )

        if total_notional <= 0:
            return False

        ratio = correlated_notional / total_notional
        return ratio >= self._thresholds["max_correlated_exposure"]

    def is_weekend_risk_reduction_active(self) -> bool:
        """Return True if the current UTC time is in the weekend risk window.

        Weekend window: Friday 20:00 UTC → Monday 08:00 UTC.
        """
        now = datetime.now(timezone.utc)
        weekday = now.weekday()   # Monday=0, …, Friday=4, Saturday=5, Sunday=6
        hour = now.hour + now.minute / 60.0

        # Saturday and Sunday are always in the window.
        if weekday in (5, 6):
            return True

        # Friday from 20:00 onwards.
        if weekday == 4 and hour >= 20.0:
            return True

        # Monday up to but not including 08:00.
        if weekday == 0 and hour < 8.0:
            return True

        return False

    def manual_reset(self, operator_id: str) -> None:
        """Allow a human operator to reset from CRITICAL (or lower) to DEFENSIVE.

        EMERGENCY_HALT may NOT be reset here — that requires governance approval
        via the GovernanceEngine / EmergencyControls pathway.

        The reset is logged with an immutable append entry.
        """
        with self._lock:
            if self._state == CapitalState.EMERGENCY_HALT:
                raise PermissionError(
                    "EMERGENCY_HALT cannot be reset via manual_reset(). "
                    "Use EmergencyControls.request_halt_release() and obtain "
                    "ADMIN approval through the governance pipeline."
                )

            if self._state == CapitalState.SAFE:
                logger.info("manual_reset: already SAFE — no action taken "
                            "(operator=%s)", operator_id)
                return

            previous_state = self._state
            self._state = CapitalState.DEFENSIVE

            entry: Dict[str, Any] = {
                "ts":              datetime.now(timezone.utc).isoformat(),
                "operator_id":     operator_id,
                "previous_state":  previous_state.value,
                "new_state":       CapitalState.DEFENSIVE.value,
                "action":          "manual_reset",
            }
            self._reset_log.append(entry)

        logger.warning(
            "CapitalPreservationEngine manual_reset: %s → DEFENSIVE "
            "(operator=%s)",
            previous_state.value,
            operator_id,
        )

    def get_status_dict(self) -> Dict[str, Any]:
        """Return a dashboard-friendly status snapshot."""
        dd_tracker = self._drawdown_tracker
        streak_tracker = self._loss_streak_tracker

        with self._lock:
            state = self._state

        return {
            "state":                   state.value,
            "risk_scalar":             self.get_risk_scalar(),
            "should_flatten":          self.should_flatten_all(),
            "weekend_risk_active":     self.is_weekend_risk_reduction_active(),
            "daily_drawdown_pct":      round(dd_tracker.daily_drawdown() * 100, 4),
            "weekly_drawdown_pct":     round(dd_tracker.weekly_drawdown() * 100, 4),
            "monthly_drawdown_pct":    round(dd_tracker.monthly_drawdown() * 100, 4),
            "alltime_peak":            dd_tracker.alltime_peak(),
            "current_equity":          dd_tracker.current_equity(),
            "loss_streak":             streak_tracker.streak,
            "total_losses":            streak_tracker.total_losses,
            "total_wins":              streak_tracker.total_wins,
            "thresholds":              dict(self._thresholds),
            "reset_log_entries":       len(self._reset_log),
        }

    # ── Internals ─────────────────────────────────────────────────────────

    def _evaluate_transitions(self, current_equity: float) -> None:
        """Check all thresholds and update state accordingly.

        Called with self._lock held.  Reads from drawdown tracker and loss
        streak tracker (which have their own internal locks).
        """
        # Fetch current metrics (calls acquire their own locks — OK because
        # self._lock and tracker locks are always taken in the same order and
        # tracker methods never call back into this engine).
        daily_dd   = self._drawdown_tracker.daily_drawdown()
        weekly_dd  = self._drawdown_tracker.weekly_drawdown()
        monthly_dd = self._drawdown_tracker.monthly_drawdown()
        streak     = self._loss_streak_tracker.streak

        th = self._thresholds

        # Determine the worst state warranted by thresholds.
        target_state = CapitalState.SAFE

        if (daily_dd >= th["daily_dd_limit"] or
                streak >= th["loss_streak_defensive"]):
            target_state = CapitalState.DEFENSIVE

        if (weekly_dd >= th["weekly_dd_limit"] or
                streak >= th["loss_streak_critical"]):
            target_state = CapitalState.CRITICAL

        if monthly_dd >= th["monthly_dd_limit"]:
            target_state = CapitalState.EMERGENCY_HALT

        # State may only move to equal or higher severity.
        if target_state.severity() > self._state.severity():
            old_state = self._state
            self._state = target_state
            logger.warning(
                "CapitalState transition: %s → %s "
                "(daily_dd=%.4f, weekly_dd=%.4f, monthly_dd=%.4f, streak=%d)",
                old_state.value, target_state.value,
                daily_dd, weekly_dd, monthly_dd, streak,
            )
            return

        # Auto-recovery: DEFENSIVE → SAFE is the only auto-recovery path.
        # Conditions for recovery:
        #   1. Current state is exactly DEFENSIVE (not CRITICAL/HALT — those require
        #      manual intervention).
        #   2. target_state (derived from live thresholds) is SAFE — meaning ALL
        #      triggering conditions (drawdown AND streak) have cleared.
        #   3. Equity has recovered to within the daily_dd_limit of the all-time peak.
        if self._state == CapitalState.DEFENSIVE and target_state == CapitalState.SAFE:
            peak = self._drawdown_tracker.alltime_peak()
            # Equity must recover to within the daily_dd_limit of all-time peak.
            if peak > 0 and current_equity >= peak * (1.0 - th["daily_dd_limit"]):
                self._state = CapitalState.SAFE
                logger.info(
                    "CapitalState auto-recovery: DEFENSIVE → SAFE "
                    "(equity=%.2f, peak=%.2f, daily_dd=%.4f)",
                    current_equity, peak, daily_dd,
                )

    @staticmethod
    def _state_risk_scalar(state: CapitalState) -> float:
        scalars = {
            CapitalState.SAFE:           1.0,
            CapitalState.DEFENSIVE:      0.5,
            CapitalState.CRITICAL:       0.2,
            CapitalState.EMERGENCY_HALT: 0.0,
        }
        return scalars[state]
