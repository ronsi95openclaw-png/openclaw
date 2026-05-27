"""Exchange reconciliation engine — authoritative restart recovery.

On every bot startup, this engine fetches live exchange state and compares
it against the persisted local bot state. Exchange is source-of-truth.

AI SAFETY CONTRACT:
- NEVER assumes local memory is correct after a crash or restart.
- NEVER reopens positions based on local state alone.
- NEVER continues trading if CRITICAL unresolved mismatches exist.
- Ghost positions (local only) are flagged and removed from tracking.
- Orphan positions (exchange only) are logged and trigger HALT.
- All reconciliation actions are appended to data/reconciliation.jsonl.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("openclaw.runtime.reconciliation")

_LOG_FILE = Path("data/reconciliation.jsonl")
_PERP_INSTRUMENT = {
    "BTC_USDT": "BTCUSD-PERP",
    "ETH_USDT": "ETHUSD-PERP",
    "SOL_USDT": "SOLUSD-PERP",
}
_SYMBOL_FROM_PERP = {v: k for k, v in _PERP_INSTRUMENT.items()}
# Acceptable size deviation between local and exchange (1%)
_SIZE_TOLERANCE = 0.01


# ── Enums ─────────────────────────────────────────────────────────────────────

class MismatchSeverity(Enum):
    INFO     = "INFO"      # Informational only — no action required
    WARNING  = "WARNING"   # Recoverable — log and continue
    CRITICAL = "CRITICAL"  # Unrecoverable — halt trading


class MismatchType(Enum):
    GHOST_POSITION    = "GHOST_POSITION"     # local tracks position, exchange has none
    ORPHAN_POSITION   = "ORPHAN_POSITION"    # exchange has position, local doesn't know
    SIZE_MISMATCH     = "SIZE_MISMATCH"      # both sides have position but sizes differ
    SIDE_MISMATCH     = "SIDE_MISMATCH"      # same symbol but opposite sides
    MISSING_SL_ORDER  = "MISSING_SL_ORDER"   # local has sl_order_id, not found on exchange
    MISSING_TP_ORDER  = "MISSING_TP_ORDER"   # local has tp_order_id, not found on exchange
    BALANCE_MISMATCH  = "BALANCE_MISMATCH"   # local balance differs from exchange equity
    EXCHANGE_TIMEOUT  = "EXCHANGE_TIMEOUT"   # could not reach exchange
    CORRUPT_STATE     = "CORRUPT_STATE"      # persisted state is malformed


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class ReconciliationMismatch:
    mismatch_type:  MismatchType
    severity:       MismatchSeverity
    symbol:         str
    description:    str
    local_value:    Any   = None
    exchange_value: Any   = None
    resolved:       bool  = False
    resolution:     str   = ""


@dataclass
class ReconciliationReport:
    ts:              str
    demo_mode:       bool
    passed:          bool
    halt_required:   bool
    exchange_reachable: bool
    local_positions:    int
    exchange_positions: int
    mismatches:      List[ReconciliationMismatch] = field(default_factory=list)
    resolved_count:  int = 0
    critical_count:  int = 0
    warning_count:   int = 0
    duration_ms:     float = 0.0
    notes:           str = ""

    def summary(self) -> str:
        status = "PASS" if self.passed else ("HALT" if self.halt_required else "WARN")
        return (
            f"[RECONCILIATION {status}] "
            f"local={self.local_positions} exchange={self.exchange_positions} "
            f"critical={self.critical_count} warnings={self.warning_count} "
            f"resolved={self.resolved_count} "
            f"duration={self.duration_ms:.0f}ms"
        )


# ── Engine ────────────────────────────────────────────────────────────────────

class ReconciliationEngine:
    """Compares local bot state against live exchange state on startup.

    Usage (called from CryptoComBot.__init__ after _load_state):
        engine = ReconciliationEngine(demo_mode=self.state.demo_mode)
        report = engine.reconcile(local_positions=self.state.open_positions,
                                   local_balance=self.state.balance)
        if report.halt_required:
            # do not start trading
        else:
            # apply reconciled positions back to self.state.open_positions
            self.state.open_positions = engine.authoritative_positions
    """

    def __init__(self, demo_mode: bool = True) -> None:
        self._demo_mode    = demo_mode
        self._lock         = threading.Lock()
        self.authoritative_positions: List[Dict[str, Any]] = []
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────────

    def reconcile(
        self,
        local_positions: List[Dict[str, Any]],
        local_balance:   float = 0.0,
    ) -> ReconciliationReport:
        """Run full reconciliation. Returns ReconciliationReport.

        In demo mode, skips exchange fetch and validates local state integrity only.
        In live mode, fetches exchange state and compares against local state.
        """
        t0 = time.monotonic()
        ts = datetime.now(timezone.utc).isoformat()
        mismatches: List[ReconciliationMismatch] = []

        if self._demo_mode:
            report = self._reconcile_demo(local_positions, ts, mismatches)
        else:
            report = self._reconcile_live(local_positions, local_balance, ts, mismatches)

        report.duration_ms = (time.monotonic() - t0) * 1000
        report.critical_count = sum(1 for m in mismatches if m.severity == MismatchSeverity.CRITICAL)
        report.warning_count  = sum(1 for m in mismatches if m.severity == MismatchSeverity.WARNING)
        report.resolved_count = sum(1 for m in mismatches if m.resolved)

        self._log_report(report)
        logger.info("%s", report.summary())
        return report

    # ── Demo mode reconciliation ───────────────────────────────────────────────

    def _reconcile_demo(
        self,
        local_positions: List[Dict[str, Any]],
        ts: str,
        mismatches: List[ReconciliationMismatch],
    ) -> ReconciliationReport:
        """In demo mode: validate local state integrity only (no exchange calls)."""
        valid_positions = []
        required_keys = {"id", "symbol", "strategy", "side", "entry_price",
                         "size", "sl_price", "tp_price"}

        for i, pos in enumerate(local_positions):
            missing = required_keys - set(pos.keys())
            if missing:
                mismatches.append(ReconciliationMismatch(
                    mismatch_type  = MismatchType.CORRUPT_STATE,
                    severity       = MismatchSeverity.WARNING,
                    symbol         = pos.get("symbol", f"pos[{i}]"),
                    description    = f"Position missing required keys: {missing}",
                    local_value    = list(pos.keys()),
                    resolved       = True,
                    resolution     = "Removed invalid position from tracking",
                ))
                continue

            # Sanity check numeric fields
            for field_name in ("entry_price", "size", "sl_price", "tp_price"):
                val = pos.get(field_name, 0)
                try:
                    fval = float(val)
                    if fval <= 0:
                        raise ValueError(f"{field_name}={val} is non-positive")
                except (TypeError, ValueError) as e:
                    mismatches.append(ReconciliationMismatch(
                        mismatch_type  = MismatchType.CORRUPT_STATE,
                        severity       = MismatchSeverity.WARNING,
                        symbol         = pos.get("symbol", ""),
                        description    = f"Invalid {field_name}: {e}",
                        local_value    = val,
                        resolved       = True,
                        resolution     = "Removed invalid position from tracking",
                    ))
                    pos = None
                    break

            if pos is not None:
                valid_positions.append(pos)

        self.authoritative_positions = valid_positions
        halt = any(m.severity == MismatchSeverity.CRITICAL and not m.resolved
                   for m in mismatches)
        return ReconciliationReport(
            ts                 = ts,
            demo_mode          = True,
            passed             = len(mismatches) == 0,
            halt_required      = halt,
            exchange_reachable = False,   # no exchange in demo
            local_positions    = len(local_positions),
            exchange_positions = 0,
            mismatches         = mismatches,
            notes              = "Demo mode — no exchange fetch performed",
        )

    # ── Live mode reconciliation ───────────────────────────────────────────────

    def _reconcile_live(
        self,
        local_positions:  List[Dict[str, Any]],
        local_balance:    float,
        ts:               str,
        mismatches:       List[ReconciliationMismatch],
    ) -> ReconciliationReport:
        """Full live reconciliation: fetch exchange state, compare, rebuild."""

        # Step 1 — Fetch exchange state
        exchange_positions, open_orders, exchange_balance, reachable = \
            self._fetch_exchange_state()

        if not reachable:
            mismatches.append(ReconciliationMismatch(
                mismatch_type = MismatchType.EXCHANGE_TIMEOUT,
                severity      = MismatchSeverity.CRITICAL,
                symbol        = "ALL",
                description   = "Could not reach exchange during reconciliation",
                resolved      = False,
                resolution    = "",
            ))
            return ReconciliationReport(
                ts                 = ts,
                demo_mode          = False,
                passed             = False,
                halt_required      = True,
                exchange_reachable = False,
                local_positions    = len(local_positions),
                exchange_positions = 0,
                mismatches         = mismatches,
                notes              = "Exchange unreachable — trading halted pending reconnect",
            )

        # Step 2 — Index exchange positions by instrument+side
        ex_index: Dict[Tuple[str, str], Dict] = {}
        for ep in exchange_positions:
            instr  = ep.get("instrument_name", "")
            side   = ep.get("side", "").upper()   # BUY=long, SELL=short
            qty    = float(ep.get("quantity", ep.get("qty", 0)))
            if qty > 0:
                ex_index[(instr, side)] = ep

        # Step 3 — Index open orders by order_id
        order_ids = {o.get("order_id", "") for o in open_orders}

        # Step 4 — Index local positions by instrument+side
        local_index: Dict[Tuple[str, str], Dict] = {}
        for lp in local_positions:
            symbol = lp.get("symbol", "")
            instr  = _PERP_INSTRUMENT.get(symbol, symbol)
            side   = "BUY" if lp.get("side", "").lower() == "long" else "SELL"
            local_index[(instr, side)] = lp

        # Step 5 — Ghost positions (local only)
        for (instr, side), lp in local_index.items():
            if (instr, side) not in ex_index:
                sym = _SYMBOL_FROM_PERP.get(instr, instr)
                mismatches.append(ReconciliationMismatch(
                    mismatch_type = MismatchType.GHOST_POSITION,
                    severity      = MismatchSeverity.WARNING,
                    symbol        = sym,
                    description   = (
                        f"Local state tracks {side} {instr} but exchange has no such position. "
                        "Position likely closed on exchange without bot knowing."
                    ),
                    local_value   = lp.get("size"),
                    exchange_value= 0,
                    resolved      = True,
                    resolution    = "Removed ghost position from local tracking",
                ))

        # Step 6 — Orphan positions (exchange only)
        for (instr, side), ep in ex_index.items():
            if (instr, side) not in local_index:
                sym = _SYMBOL_FROM_PERP.get(instr, instr)
                qty = float(ep.get("quantity", ep.get("qty", 0)))
                mismatches.append(ReconciliationMismatch(
                    mismatch_type = MismatchType.ORPHAN_POSITION,
                    severity      = MismatchSeverity.CRITICAL,
                    symbol        = sym,
                    description   = (
                        f"Exchange has {side} {instr} qty={qty:.6f} "
                        "but local state has no record — untracked live position."
                    ),
                    local_value   = None,
                    exchange_value= qty,
                    resolved      = False,
                    resolution    = "",
                ))

        # Step 7 — Size/side mismatches for positions that exist on both sides
        reconciled_positions: List[Dict[str, Any]] = []
        for (instr, side), lp in local_index.items():
            ep = ex_index.get((instr, side))
            if ep is None:
                continue  # ghost — handled above, skip

            ex_qty    = float(ep.get("quantity", ep.get("qty", 0)))
            local_qty = float(lp.get("size", 0))
            sym       = _SYMBOL_FROM_PERP.get(instr, instr)

            if local_qty > 0 and abs(ex_qty - local_qty) / local_qty > _SIZE_TOLERANCE:
                mismatches.append(ReconciliationMismatch(
                    mismatch_type  = MismatchType.SIZE_MISMATCH,
                    severity       = MismatchSeverity.WARNING,
                    symbol         = sym,
                    description    = (
                        f"Size mismatch {sym} {side}: "
                        f"local={local_qty:.6f} exchange={ex_qty:.6f} "
                        f"({abs(ex_qty - local_qty)/local_qty*100:.2f}% deviation)"
                    ),
                    local_value    = local_qty,
                    exchange_value = ex_qty,
                    resolved       = True,
                    resolution     = "Updated local size to match exchange (exchange is authoritative)",
                ))
                lp = dict(lp)
                lp["size"] = ex_qty

            # Step 8 — Verify SL/TP orders still exist on exchange
            sl_id = lp.get("sl_order_id", "")
            tp_id = lp.get("tp_order_id", "")
            if sl_id and sl_id not in order_ids:
                mismatches.append(ReconciliationMismatch(
                    mismatch_type  = MismatchType.MISSING_SL_ORDER,
                    severity       = MismatchSeverity.CRITICAL,
                    symbol         = sym,
                    description    = f"SL order {sl_id} not found on exchange — position unprotected",
                    local_value    = sl_id,
                    exchange_value = None,
                    resolved       = False,
                ))
            if tp_id and tp_id not in order_ids:
                mismatches.append(ReconciliationMismatch(
                    mismatch_type  = MismatchType.MISSING_TP_ORDER,
                    severity       = MismatchSeverity.WARNING,
                    symbol         = sym,
                    description    = f"TP order {tp_id} not found on exchange — no take-profit protection",
                    local_value    = tp_id,
                    exchange_value = None,
                    resolved       = True,
                    resolution     = "TP order missing — position still has SL protection",
                ))

            reconciled_positions.append(lp)

        # Step 9 — Balance check (WARNING only — balance can differ legitimately)
        if exchange_balance > 0 and local_balance > 0:
            balance_diff_pct = abs(exchange_balance - local_balance) / max(local_balance, 1.0)
            if balance_diff_pct > 0.10:
                mismatches.append(ReconciliationMismatch(
                    mismatch_type  = MismatchType.BALANCE_MISMATCH,
                    severity       = MismatchSeverity.WARNING,
                    symbol         = "BALANCE",
                    description    = (
                        f"Balance mismatch: local={local_balance:.2f} "
                        f"exchange={exchange_balance:.2f} "
                        f"({balance_diff_pct*100:.1f}% deviation)"
                    ),
                    local_value    = local_balance,
                    exchange_value = exchange_balance,
                    resolved       = True,
                    resolution     = "Exchange balance will be used for next risk calculation",
                ))

        # Authoritative positions = only the reconciled ones (ghosts dropped)
        self.authoritative_positions = reconciled_positions

        has_critical = any(
            m.severity == MismatchSeverity.CRITICAL and not m.resolved
            for m in mismatches
        )

        return ReconciliationReport(
            ts                 = ts,
            demo_mode          = False,
            passed             = len(mismatches) == 0,
            halt_required      = has_critical,
            exchange_reachable = True,
            local_positions    = len(local_positions),
            exchange_positions = len(exchange_positions),
            mismatches         = mismatches,
            notes              = (
                "HALT: unresolved critical mismatches — manual review required"
                if has_critical else ""
            ),
        )

    def _fetch_exchange_state(
        self,
    ) -> Tuple[List[Dict], List[Dict], float, bool]:
        """Fetch positions, open orders, and balance from exchange.

        Returns (positions, open_orders, equity, reachable).
        """
        try:
            from trading.exchange import get_positions, get_open_orders, get_derivatives_balance
            positions  = get_positions()
            orders     = get_open_orders()
            bal        = get_derivatives_balance()
            equity     = float(bal.get("equity", bal.get("total", 0))) if bal else 0.0
            return positions, orders, equity, True
        except EnvironmentError:
            # API keys not set — expected in dev/test environments
            logger.warning("ReconciliationEngine: exchange keys not set, skipping live fetch")
            return [], [], 0.0, False
        except Exception as exc:
            logger.error("ReconciliationEngine: exchange fetch failed: %s", exc)
            return [], [], 0.0, False

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log_report(self, report: ReconciliationReport) -> None:
        """Append reconciliation report to the immutable log."""
        record = {
            "ts":                  report.ts,
            "demo_mode":           report.demo_mode,
            "passed":              report.passed,
            "halt_required":       report.halt_required,
            "exchange_reachable":  report.exchange_reachable,
            "local_positions":     report.local_positions,
            "exchange_positions":  report.exchange_positions,
            "critical_count":      report.critical_count,
            "warning_count":       report.warning_count,
            "resolved_count":      report.resolved_count,
            "duration_ms":         round(report.duration_ms, 1),
            "notes":               report.notes,
            "mismatches": [
                {
                    "type":     m.mismatch_type.value,
                    "severity": m.severity.value,
                    "symbol":   m.symbol,
                    "desc":     m.description,
                    "resolved": m.resolved,
                    "resolution": m.resolution,
                }
                for m in report.mismatches
            ],
        }
        line = json.dumps(record, default=str) + "\n"
        try:
            try:
                import fcntl as _fcntl
                with open(_LOG_FILE, "a", encoding="utf-8") as fh:
                    _fcntl.flock(fh, _fcntl.LOCK_EX)
                    try:
                        fh.write(line)
                    finally:
                        _fcntl.flock(fh, _fcntl.LOCK_UN)
            except ImportError:
                with open(_LOG_FILE, "a", encoding="utf-8") as fh:
                    fh.write(line)
        except Exception as exc:
            logger.error("ReconciliationEngine: log write failed: %s", exc)


# ── Convenience builder ────────────────────────────────────────────────────────

def reconcile_on_startup(
    local_positions: List[Dict[str, Any]],
    local_balance:   float,
    demo_mode:       bool = True,
) -> ReconciliationReport:
    """Run reconciliation and return the report. Called from CryptoComBot.__init__."""
    engine = ReconciliationEngine(demo_mode=demo_mode)
    return engine.reconcile(
        local_positions=local_positions,
        local_balance=local_balance,
    )


# ── Continuous reconciliation scheduler ───────────────────────────────────────

class ContinuousReconciliationScheduler:
    """Background daemon thread that reconciles bot state against the exchange
    every `interval_seconds` (default 300s / 5 min).

    Design rules:
    - Runs in a daemon thread — never blocks the scan loop.
    - Cooldown enforced: will not run again within `cooldown_seconds` of the
      last successful run (prevents hammering the exchange on repeated failures).
    - On CRITICAL mismatch: sets internal flag; `should_halt_entries()` returns True.
    - On consecutive failures (exchange unreachable): escalates to WARNING, then CRITICAL.
    - All results logged to data/reconciliation.jsonl (via ReconciliationEngine).
    - Thread-safe: uses threading.Lock for all shared state.
    """

    _CONSECUTIVE_FAIL_WARN  = 3   # warn after N consecutive exchange-unreachable
    _CONSECUTIVE_FAIL_HALT  = 5   # halt entries after N consecutive failures

    def __init__(
        self,
        interval_seconds: int  = 300,
        cooldown_seconds: int  = 60,
        demo_mode: bool        = False,
    ) -> None:
        self._interval     = interval_seconds
        self._cooldown     = cooldown_seconds
        self._demo_mode    = demo_mode
        self._lock         = threading.Lock()
        self._stop_event   = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # State shared with the bot (read by should_halt_entries())
        self._halt_entries: bool              = False
        self._last_run_ts:  float             = 0.0
        self._last_report:  Optional[ReconciliationReport] = None
        self._consecutive_failures: int       = 0
        self._total_runs:   int               = 0
        self._total_halts:  int               = 0

        # Callback: bot passes a lambda that returns (positions, balance)
        self._state_provider: Optional[Any]  = None

    def set_state_provider(self, provider) -> None:
        """Register a callable() → (List[dict], float) that returns current bot state."""
        self._state_provider = provider

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="recon-scheduler",
        )
        self._thread.start()
        logger.info(
            "ContinuousReconciliationScheduler started — interval=%ds demo=%s",
            self._interval, self._demo_mode,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("ContinuousReconciliationScheduler stopped")

    def should_halt_entries(self) -> bool:
        """Returns True if the last reconciliation found unresolved CRITICAL mismatches."""
        with self._lock:
            return self._halt_entries

    def get_last_report(self) -> Optional[ReconciliationReport]:
        with self._lock:
            return self._last_report

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running":               self._thread is not None and self._thread.is_alive(),
                "halt_entries":          self._halt_entries,
                "last_run_ts":           self._last_run_ts,
                "consecutive_failures":  self._consecutive_failures,
                "total_runs":            self._total_runs,
                "total_halts":           self._total_halts,
                "interval_seconds":      self._interval,
            }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            # Wait for interval (but wake immediately on first iteration if
            # it's been more than interval since the last run)
            now = time.time()
            with self._lock:
                since_last = now - self._last_run_ts
            wait = max(0, self._interval - since_last)
            self._stop_event.wait(wait)
            if self._stop_event.is_set():
                break
            self._run_one()

    def _run_one(self) -> None:
        """Execute a single reconciliation cycle."""
        now = time.time()
        with self._lock:
            # Enforce cooldown: never run within cooldown_seconds of last run
            if now - self._last_run_ts < self._cooldown:
                return

        positions, balance = self._get_bot_state()
        engine = ReconciliationEngine(demo_mode=self._demo_mode)
        report = engine.reconcile(local_positions=positions, local_balance=balance)

        with self._lock:
            self._last_run_ts = time.time()
            self._last_report = report
            self._total_runs += 1

            if not report.exchange_reachable and not self._demo_mode:
                self._consecutive_failures += 1
                logger.warning(
                    "[Reconciliation] Exchange unreachable — consecutive failures: %d",
                    self._consecutive_failures,
                )
                if self._consecutive_failures >= self._CONSECUTIVE_FAIL_HALT:
                    if not self._halt_entries:
                        self._halt_entries = True
                        self._total_halts += 1
                        logger.critical(
                            "[Reconciliation] %d consecutive exchange-unreachable — "
                            "halting new entries",
                            self._consecutive_failures,
                        )
                        self._alert_telegram(
                            f"RECON HALT: exchange unreachable x{self._consecutive_failures}"
                        )
            else:
                # Successful contact — reset failure counter
                if self._consecutive_failures > 0:
                    logger.info(
                        "[Reconciliation] Exchange reachable again after %d failures",
                        self._consecutive_failures,
                    )
                self._consecutive_failures = 0

                if report.halt_required:
                    if not self._halt_entries:
                        self._halt_entries = True
                        self._total_halts += 1
                        logger.critical(
                            "[Reconciliation] HALT required: %s", report.summary()
                        )
                        self._alert_telegram(
                            f"RECON HALT: {report.summary()}"
                        )
                elif report.passed:
                    # Clear halt only if reconciliation fully passes
                    if self._halt_entries:
                        logger.info(
                            "[Reconciliation] Clearing halt — reconciliation passed"
                        )
                    self._halt_entries = False

            self._emit_metrics(report)
        logger.debug("[Reconciliation] cycle complete: %s", report.summary())

    def _get_bot_state(self) -> tuple:
        """Get current positions and balance from bot state provider."""
        if self._state_provider is not None:
            try:
                result = self._state_provider()
                if isinstance(result, (list, tuple)) and len(result) == 2:
                    return result[0], result[1]
            except Exception as exc:
                logger.warning("[Reconciliation] state_provider error: %s", exc)
        return [], 0.0

    def _emit_metrics(self, report: ReconciliationReport) -> None:
        try:
            from runtime.metrics import get_registry
            reg = get_registry()
            reg.set_reconciliation_mismatches(
                critical=report.critical_count,
                warning=report.warning_count,
            )
        except Exception:
            pass

    def _alert_telegram(self, message: str) -> None:
        try:
            from runtime.telegram_alerts import send_alert
            send_alert(f"[OPENCLAW RECON] {message}")
        except Exception:
            pass

