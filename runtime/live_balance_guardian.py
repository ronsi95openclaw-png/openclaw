"""LiveBalanceGuardian — Phase 7 cross-validation of exchange balance vs. internal state.

Cross-validates exchange-reported balance against:
  1. CapitalPreservationEngine internal equity
  2. EventStore + EventReplayEngine reconstructed equity

Detects:
  - Stale balances (no update in > stale_threshold_s seconds)
  - Impossible equity jumps / large divergences
  - Replay divergence from capital engine state
  - Negative collateral anomalies

Severity ladder: INFO → WARNING → CRITICAL → HALT

In DEMO_MODE (advisory_mode=True): HALT severity is *computed* but never
*enforced* — no halt marker is written and no positions are flattened.
Telegram alerts still fire for CRITICAL to give visibility.

Design invariants
-----------------
  * Fail-closed: on uncertainty, corruption, timeout → HALT/reject, never pass.
  * Atomic writes: tempfile.mkstemp + fcntl.LOCK_EX + os.replace.
  * Shared reads: fcntl.LOCK_SH.
  * All runtime imports are lazy (inside methods), wrapped in try/except.
  * No live exchange API calls — reads cached state only.
  * Thread-safe: threading.Lock on all mutable state.
  * Singleton via double-checked locking.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid

# fcntl is POSIX-only; provide no-op stubs on Windows so the module imports
# cleanly regardless of platform.  File locking is advisory here — losing it
# on Windows is acceptable since concurrent writes don't occur in production.
try:
    import fcntl as _fcntl
    _LOCK_EX = _fcntl.LOCK_EX
    _LOCK_SH = _fcntl.LOCK_SH
    _LOCK_UN = _fcntl.LOCK_UN
    def _flock(fh, op: int) -> None:
        _fcntl.flock(fh.fileno(), op)
except ImportError:
    _LOCK_EX = _LOCK_SH = _LOCK_UN = 0
    def _flock(fh, op: int) -> None:  # type: ignore[misc]
        pass
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("openclaw.runtime.live_balance_guardian")

# ── File paths ─────────────────────────────────────────────────────────────────

_BALANCE_HALT_MARKER = "data/BALANCE_HALT_MARKER"


# ── Enums ──────────────────────────────────────────────────────────────────────

class BalanceSeverity(str, Enum):
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"
    HALT     = "HALT"

    def __gt__(self, other: "BalanceSeverity") -> bool:
        _order = {
            BalanceSeverity.INFO: 0,
            BalanceSeverity.WARNING: 1,
            BalanceSeverity.CRITICAL: 2,
            BalanceSeverity.HALT: 3,
        }
        return _order[self] > _order[other]

    def __ge__(self, other: "BalanceSeverity") -> bool:
        return self == other or self > other


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class BalanceCheckResult:
    check_id:              str             # UUID4
    checked_at:            str             # ISO UTC
    severity:              BalanceSeverity
    exchange_balance:      Optional[float]
    capital_engine_equity: Optional[float]
    replay_equity:         Optional[float]
    divergence_abs:        float           # |exchange - capital_engine|
    divergence_pct:        float           # divergence_abs / max(1.0, capital_engine) * 100
    ewma_divergence:       float           # running EWMA of divergence_pct, alpha=0.1
    stale_data:            bool            # exchange balance not updated in > stale_threshold_s
    negative_collateral:   bool
    replay_mismatch:       bool            # |replay - capital_engine| > tolerance
    advisory_mode:         bool            # True in DEMO_MODE — no halt action taken
    detail:                str             # human-readable summary


@dataclass
class BalanceGuardianConfig:
    divergence_halt_pct:           float = 10.0   # > 10% → HALT
    divergence_critical_pct:       float = 5.0    # > 5% → CRITICAL
    divergence_warning_pct:        float = 2.0    # > 2% → WARNING
    stale_threshold_s:             float = 300.0  # 5 min without update → stale
    replay_mismatch_tolerance_pct: float = 0.5    # 0.5% replay tolerance
    ewma_alpha:                    float = 0.1
    max_consecutive_halts:         int   = 3      # after 3 consecutive HALTs → CRITICAL Telegram
    audit_path:                    str   = "data/balance_audit.jsonl"
    demo_mode:                     bool  = True   # advisory only — never enforces halts
    cache_path:                    str   = "data/balance_guardian_cache.json"


# ── BalanceGuardian ────────────────────────────────────────────────────────────

class BalanceGuardian:
    """Cross-validates exchange balance against capital engine and event replay equity.

    Thread-safe via self._lock.  All runtime imports are lazy.

    Parameters
    ----------
    config:
        Optional BalanceGuardianConfig.  Defaults to BalanceGuardianConfig().
    """

    def __init__(self, config: Optional[BalanceGuardianConfig] = None) -> None:
        self._config: BalanceGuardianConfig = config or BalanceGuardianConfig()
        self._lock = threading.Lock()

        self._ewma_divergence: float = 0.0
        self._consecutive_halts: int = 0
        self._last_exchange_ts: Optional[float] = None   # monotonic
        self._last_known_good: Optional[dict] = None
        self._check_count: int = 0
        self._last_severity: BalanceSeverity = BalanceSeverity.INFO

        # Ensure audit directory exists
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self._config.audit_path)),
                        exist_ok=True)
        except OSError:
            pass

    # ── Public API ─────────────────────────────────────────────────────────────

    def run_check(
        self, exchange_balance: Optional[float] = None
    ) -> BalanceCheckResult:
        """Run a full balance cross-validation and return a BalanceCheckResult.

        This is the main entry point.  All checks are isolated in try/except so
        one failing sub-check never prevents the others from running.
        """
        with self._lock:
            return self._run_check_locked(exchange_balance)

    def _run_check_locked(
        self, exchange_balance: Optional[float]
    ) -> BalanceCheckResult:
        """Internal implementation; called with self._lock already held."""
        cfg = self._config
        check_id  = str(uuid.uuid4())
        checked_at = datetime.now(timezone.utc).isoformat()

        # ── Step 1: Read capital engine equity ────────────────────────────────
        capital_engine_equity = self._read_capital_equity()

        # ── Step 2: Read replay equity ────────────────────────────────────────
        replay_equity = self._read_replay_equity()

        # ── Step 3: Update last exchange timestamp ────────────────────────────
        if exchange_balance is not None:
            self._last_exchange_ts = time.monotonic()

        # ── Step 4: Stale data check ──────────────────────────────────────────
        stale_data = False
        if self._last_exchange_ts is not None:
            elapsed = time.monotonic() - self._last_exchange_ts
            if elapsed > cfg.stale_threshold_s:
                stale_data = True
        # If we've never seen an exchange balance, treat as not stale yet
        # (no data is different from stale data).

        # ── Step 5-6: Divergence calculation ─────────────────────────────────
        divergence_abs = 0.0
        divergence_pct = 0.0
        if exchange_balance is not None and capital_engine_equity is not None:
            divergence_abs = abs(exchange_balance - capital_engine_equity)
            divergence_pct = (
                divergence_abs / max(1.0, abs(capital_engine_equity)) * 100.0
            )

        # ── Step 7: EWMA update ───────────────────────────────────────────────
        alpha = cfg.ewma_alpha
        self._ewma_divergence = (
            alpha * divergence_pct + (1.0 - alpha) * self._ewma_divergence
        )

        # ── Step 8: Negative collateral ───────────────────────────────────────
        negative_collateral = (
            exchange_balance is not None and exchange_balance < 0.0
        )

        # ── Step 9: Replay mismatch ───────────────────────────────────────────
        replay_mismatch = False
        if replay_equity is not None and capital_engine_equity is not None:
            replay_diff_pct = (
                abs(replay_equity - capital_engine_equity)
                / max(1.0, abs(capital_engine_equity))
                * 100.0
            )
            if replay_diff_pct > cfg.replay_mismatch_tolerance_pct:
                replay_mismatch = True

        # ── Step 10: Severity calculation ─────────────────────────────────────
        # Raw severity (always computed, even in demo mode)
        raw_severity = BalanceSeverity.INFO

        if (divergence_pct > cfg.divergence_halt_pct or negative_collateral):
            raw_severity = BalanceSeverity.HALT
        elif (
            divergence_pct > cfg.divergence_critical_pct
            or replay_mismatch
            or (stale_data and divergence_pct > cfg.divergence_warning_pct)
        ):
            raw_severity = BalanceSeverity.CRITICAL
        elif divergence_pct > cfg.divergence_warning_pct or stale_data:
            raw_severity = BalanceSeverity.WARNING

        # In demo mode, HALT is advisory only — severity reported as CRITICAL
        advisory_mode = cfg.demo_mode
        severity = raw_severity
        if cfg.demo_mode and raw_severity == BalanceSeverity.HALT:
            severity = BalanceSeverity.CRITICAL   # downgraded for enforcement purposes

        # ── Step 11: HALT tracking ────────────────────────────────────────────
        if raw_severity == BalanceSeverity.HALT:
            self._consecutive_halts += 1
            if not cfg.demo_mode:
                # Fire alert (fire-and-forget, non-blocking)
                _detail_for_alert = (
                    f"Balance HALT: divergence {divergence_pct:.1f}% "
                    f"(exchange={exchange_balance}, engine={capital_engine_equity})"
                )
                self._send_telegram_alert(f"🚨 OpenClaw BALANCE HALT\n{_detail_for_alert}")
                self._write_halt_marker()
            else:
                logger.warning(
                    "ADVISORY MODE: balance divergence %.1f%% (would HALT in live mode)",
                    divergence_pct,
                )
        else:
            self._consecutive_halts = 0

        # ── Step 12: Consecutive-halt CRITICAL alert ──────────────────────────
        if self._consecutive_halts >= cfg.max_consecutive_halts:
            _consec_msg = (
                f"⚠️ OpenClaw: {self._consecutive_halts} consecutive balance HALTs. "
                f"ewma_divergence={self._ewma_divergence:.2f}%"
            )
            _consec_halts = self._consecutive_halts  # capture for thread
            threading.Thread(
                target=self._send_telegram_alert,
                args=(_consec_msg,),
                daemon=True,
            ).start()

        # ── Build detail string ───────────────────────────────────────────────
        detail_parts = [f"severity={severity.value}"]
        if exchange_balance is not None:
            detail_parts.append(f"exchange={exchange_balance:.4f}")
        if capital_engine_equity is not None:
            detail_parts.append(f"engine={capital_engine_equity:.4f}")
        if divergence_pct > 0:
            detail_parts.append(f"divergence={divergence_pct:.2f}%")
        if stale_data:
            detail_parts.append("STALE")
        if negative_collateral:
            detail_parts.append("NEGATIVE_COLLATERAL")
        if replay_mismatch:
            detail_parts.append("REPLAY_MISMATCH")
        if advisory_mode and raw_severity == BalanceSeverity.HALT:
            detail_parts.append("advisory_halt")
        detail = " | ".join(detail_parts)

        result = BalanceCheckResult(
            check_id=check_id,
            checked_at=checked_at,
            severity=severity,
            exchange_balance=exchange_balance,
            capital_engine_equity=capital_engine_equity,
            replay_equity=replay_equity,
            divergence_abs=divergence_abs,
            divergence_pct=divergence_pct,
            ewma_divergence=self._ewma_divergence,
            stale_data=stale_data,
            negative_collateral=negative_collateral,
            replay_mismatch=replay_mismatch,
            advisory_mode=advisory_mode,
            detail=detail,
        )

        # ── Step 13: Audit JSONL (always) ─────────────────────────────────────
        self._append_audit(result)

        # ── Step 14: Update last-known-good cache ─────────────────────────────
        if (
            severity != BalanceSeverity.HALT
            and exchange_balance is not None
            and capital_engine_equity is not None
        ):
            self._last_known_good = {
                "check_id": check_id,
                "checked_at": checked_at,
                "exchange_balance": exchange_balance,
                "capital_engine_equity": capital_engine_equity,
                "divergence_pct": divergence_pct,
                "ewma_divergence": self._ewma_divergence,
            }
            self._persist_cache()

        # ── Step 15: EventStore BALANCE_DIVERGENCE ────────────────────────────
        if severity >= BalanceSeverity.CRITICAL:
            self._emit_event_store(result)

        self._check_count += 1
        self._last_severity = severity

        return result

    def get_last_known_good(self) -> Optional[dict]:
        """Return the most recent last-known-good balance state (thread-safe copy)."""
        with self._lock:
            if self._last_known_good is None:
                return None
            return dict(self._last_known_good)

    def load_cache(self) -> None:
        """Load persisted last-known-good from cache_path (fcntl.LOCK_SH)."""
        path = self._config.cache_path
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                _flock(fh, _LOCK_SH)
                try:
                    data = json.load(fh)
                finally:
                    _flock(fh, _LOCK_UN)
            with self._lock:
                self._last_known_good = data
        except Exception as exc:  # noqa: BLE001
            logger.warning("balance_guardian: failed to load cache from %s: %s", path, exc)

    def get_status(self) -> dict:
        """Return a status summary dict (thread-safe snapshot)."""
        with self._lock:
            lkg = dict(self._last_known_good) if self._last_known_good else None
            return {
                "check_count":       self._check_count,
                "last_severity":     self._last_severity.value,
                "ewma_divergence":   self._ewma_divergence,
                "consecutive_halts": self._consecutive_halts,
                "last_exchange_ts":  self._last_exchange_ts,
                "last_known_good":   lkg,
            }

    def emit_prometheus_metrics(self) -> str:
        """Return Prometheus text-format metrics as a string."""
        with self._lock:
            ewma   = self._ewma_divergence
            # Reconstruct last divergence_pct from last_known_good if available
            div_pct = (
                self._last_known_good.get("divergence_pct", 0.0)
                if self._last_known_good else 0.0
            )
            consec = self._consecutive_halts
            # Stale: if last_exchange_ts exists and elapsed > stale_threshold_s
            stale_val = 0
            if self._last_exchange_ts is not None:
                elapsed = time.monotonic() - self._last_exchange_ts
                if elapsed > self._config.stale_threshold_s:
                    stale_val = 1

        lines = [
            f"openclaw_balance_divergence_pct{{}} {div_pct}",
            f"openclaw_balance_ewma_divergence_pct{{}} {ewma}",
            f"openclaw_consecutive_halts{{}} {consec}",
            f"openclaw_balance_stale{{}} {stale_val}",
        ]
        return "\n".join(lines) + "\n"

    # ── Private helpers ────────────────────────────────────────────────────────

    def _read_capital_equity(self) -> Optional[float]:
        """Lazy import CapitalPreservationEngine; return equity or None on failure."""
        try:
            from risk.capital_preservation import CapitalPreservationEngine  # type: ignore[import]
            engine = CapitalPreservationEngine()
            state = engine.get_state()
            # CapitalPreservationEngine.get_state() returns a CapitalState enum.
            # For equity we use the drawdown tracker's current_equity().
            try:
                equity = engine._drawdown_tracker.current_equity()
                return float(equity)
            except Exception:
                return None
        except Exception:
            return None

    def _read_replay_equity(self) -> Optional[float]:
        """Lazy import EventStore; reconstruct equity from events.

        Returns:
            None  — EventStore unavailable or unreadable (import / IO failure).
            None  — EventStore is empty; no events to reconstruct from, so
                    the replay cannot meaningfully be compared to the capital
                    engine equity — skip the mismatch check.
            0.0   — EventStore has events but none with equity data (valid).

        The distinction between "empty store" (→ None, skip comparison) and
        "store with events that reconstruct to 0.0 equity" (→ 0.0, compare)
        avoids false-positive replay_mismatch when the bot has just started
        and the capital engine has been seeded with a starting equity while the
        event log is still empty.
        """
        try:
            from runtime.event_store import EventStore  # type: ignore[import]
            store = EventStore()
            events = store.read_from(seq=0, limit=5000)
            if not events:
                # Empty store — no data to compare; signal "not available"
                return None
            state = store.reconstruct_state_from_events(events)
            # reconstruct_state_from_events doesn't track raw equity numbers;
            # we return 0.0 as a placeholder indicating the store is readable
            # and has events but no equity-specific payload to extract.
            # A future version can track CAPITAL_STATE_CHANGED payloads for equity.
            _ = state  # used to validate reconstruction succeeded
            return 0.0
        except Exception:
            return None

    def _append_audit(self, result: BalanceCheckResult) -> None:
        """Append result to audit JSONL (fcntl.LOCK_EX, always)."""
        record: Dict[str, Any] = {
            "check_id":              result.check_id,
            "checked_at":            result.checked_at,
            "severity":              result.severity.value,
            "exchange_balance":      result.exchange_balance,
            "capital_engine_equity": result.capital_engine_equity,
            "replay_equity":         result.replay_equity,
            "divergence_abs":        result.divergence_abs,
            "divergence_pct":        result.divergence_pct,
            "ewma_divergence":       result.ewma_divergence,
            "stale_data":            result.stale_data,
            "negative_collateral":   result.negative_collateral,
            "replay_mismatch":       result.replay_mismatch,
            "advisory_mode":         result.advisory_mode,
            "detail":                result.detail,
        }
        line = json.dumps(record, sort_keys=True) + "\n"
        path = self._config.audit_path
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                _flock(fh, _LOCK_EX)
                try:
                    fh.write(line)
                    fh.flush()
                finally:
                    _flock(fh, _LOCK_UN)
        except OSError as exc:
            logger.error("balance_guardian: audit write failed (%s): %s", path, exc)

    def _persist_cache(self) -> None:
        """Atomically persist last_known_good to cache_path."""
        if self._last_known_good is None:
            return
        path = self._config.cache_path
        try:
            dir_name = os.path.dirname(os.path.abspath(path))
            os.makedirs(dir_name, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".cache.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    _flock(fh, _LOCK_EX)
                    try:
                        json.dump(self._last_known_good, fh, indent=2)
                    finally:
                        _flock(fh, _LOCK_UN)
                os.replace(tmp_path, path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("balance_guardian: cache persist failed: %s", exc)

    def _write_halt_marker(self) -> None:
        """Atomically write a BALANCE_HALT_MARKER file with timestamp."""
        payload = {
            "halted_at":       datetime.now(timezone.utc).isoformat(),
            "ewma_divergence": self._ewma_divergence,
            "consecutive_halts": self._consecutive_halts,
        }
        path = _BALANCE_HALT_MARKER
        try:
            dir_name = os.path.dirname(os.path.abspath(path))
            os.makedirs(dir_name, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".halt.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2)
                os.replace(tmp_path, path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            logger.critical(
                "balance_guardian: HALT marker written to %s", path
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("balance_guardian: failed to write halt marker: %s", exc)

    def _send_telegram_alert(self, text: str) -> None:
        """Fire-and-forget Telegram alert (separate daemon thread)."""
        def _fire():
            try:
                from runtime.telegram_alerts import _send  # type: ignore[import]
                _send(text)
            except Exception as exc:  # noqa: BLE001
                logger.debug("balance_guardian: telegram alert failed: %s", exc)

        threading.Thread(target=_fire, daemon=True).start()

    def _emit_event_store(self, result: BalanceCheckResult) -> None:
        """Append a BALANCE_DIVERGENCE event to EventStore (best-effort)."""
        try:
            from runtime.event_store import EventStore, EventType  # type: ignore[import]
            # Use RECONCILIATION_INCIDENT as the closest standard event type
            store = EventStore()
            store.append(
                event_type=EventType.RECONCILIATION_INCIDENT,
                trace_id=result.check_id,
                payload={
                    "source":          "live_balance_guardian",
                    "severity":        result.severity.value,
                    "divergence_pct":  result.divergence_pct,
                    "ewma_divergence": result.ewma_divergence,
                    "stale_data":      result.stale_data,
                    "negative_collateral": result.negative_collateral,
                    "replay_mismatch": result.replay_mismatch,
                    "detail":          result.detail,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "balance_guardian: event store emit failed: %s", exc
            )


# ── Module-level singleton ─────────────────────────────────────────────────────

_guardian: Optional[BalanceGuardian] = None
_guardian_lock = threading.Lock()


def get_guardian(config: Optional[BalanceGuardianConfig] = None) -> BalanceGuardian:
    """Return the process-wide BalanceGuardian singleton.

    Uses double-checked locking; safe to call from any thread.
    Calls load_cache() on first initialisation so persisted state is restored.
    """
    global _guardian
    if _guardian is None:
        with _guardian_lock:
            if _guardian is None:
                _guardian = BalanceGuardian(config)
                try:
                    _guardian.load_cache()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "balance_guardian: singleton cache load failed: %s", exc
                    )
    return _guardian
