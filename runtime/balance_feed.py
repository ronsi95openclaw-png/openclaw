"""BalanceFeedDaemon — periodic exchange balance feed for LiveBalanceGuardian.

Runs as a background daemon thread. Fetches real Crypto.com derivatives wallet
balance (via trading/exchange.get_derivatives_balance()) every `interval_s` seconds
and passes it to LiveBalanceGuardian.run_check(exchange_balance=equity).

In DEMO_MODE: runs fully but guardian severity is advisory only (HALT → CRITICAL).
On exchange fetch failure: passes None to guardian (guardian detects stale eventually).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("openclaw.runtime.balance_feed")


# ── Status dataclass ───────────────────────────────────────────────────────────

@dataclass
class BalanceFeedStatus:
    running: bool
    last_fetch_ts: Optional[str]       # ISO8601 or None
    last_equity: Optional[float]       # last successfully fetched equity
    consecutive_failures: int          # reset to 0 on success
    total_checks: int                  # monotonically increasing
    last_error: Optional[str]          # last exception message or None
    demo_mode: bool


# ── BalanceFeedDaemon ──────────────────────────────────────────────────────────

class BalanceFeedDaemon:
    """Periodic background daemon that fetches exchange balance and feeds it
    to LiveBalanceGuardian.

    Thread-safe via self._lock.  All exchange and guardian imports are lazy.
    The daemon thread is a Python daemon thread — it will not prevent process
    exit.

    Parameters
    ----------
    interval_s:
        How many seconds to wait between successive fetch+check cycles.
    demo_mode:
        When True the guardian operates in advisory mode (HALT → CRITICAL).
        This flag is passed to LiveBalanceGuardian.run_check indirectly via
        the guardian's own config; here it is also stored for status reporting
        and HALT-logging suppression.
    max_consecutive_failures:
        Number of consecutive fetch failures before an ERROR is logged.
    """

    def __init__(
        self,
        interval_s: float = 30.0,
        demo_mode: bool = True,
        max_consecutive_failures: int = 5,
    ) -> None:
        self._interval_s = interval_s
        self._demo_mode = demo_mode
        self._max_consecutive_failures = max_consecutive_failures

        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self._status = BalanceFeedStatus(
            running=False,
            last_fetch_ts=None,
            last_equity=None,
            consecutive_failures=0,
            total_checks=0,
            last_error=None,
            demo_mode=demo_mode,
        )
        self._thread: Optional[threading.Thread] = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the daemon thread if not already running.

        Guard: double-check _thread is None before starting (prevents double-start).
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.debug("balance_feed: daemon already running; ignoring start()")
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="BalanceFeedDaemon",
                daemon=True,
            )
            self._status.running = True
            self._thread.start()
            logger.info(
                "balance_feed: daemon started (interval=%.1fs demo_mode=%s)",
                self._interval_s,
                self._demo_mode,
            )

    def stop(self, timeout_s: float = 5.0) -> None:
        """Signal stop, join thread with timeout, clear thread reference."""
        self._stop_event.set()
        thread: Optional[threading.Thread] = None
        with self._lock:
            thread = self._thread

        if thread is not None:
            thread.join(timeout=timeout_s)

        with self._lock:
            self._thread = None
            self._status.running = False

        logger.info("balance_feed: daemon stopped")

    def is_running(self) -> bool:
        """Return True if the daemon thread is alive."""
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def get_status(self) -> BalanceFeedStatus:
        """Return a thread-safe copy of the current status."""
        with self._lock:
            return BalanceFeedStatus(
                running=self._status.running,
                last_fetch_ts=self._status.last_fetch_ts,
                last_equity=self._status.last_equity,
                consecutive_failures=self._status.consecutive_failures,
                total_checks=self._status.total_checks,
                last_error=self._status.last_error,
                demo_mode=self._status.demo_mode,
            )

    def force_check(self) -> None:
        """Run _fetch_and_check() immediately in the calling thread.

        Useful for testing or manual triggering without waiting for the next
        scheduled cycle.
        """
        self._fetch_and_check()

    # ── Internal loop ──────────────────────────────────────────────────────────

    def _run(self) -> None:
        """Main daemon loop.

        Runs _fetch_and_check() then sleeps interval_s.  Sleeps in 1-second
        increments so the stop event is detected promptly.  On any unhandled
        exception, logs CRITICAL and continues — the daemon must never crash.
        """
        logger.debug("balance_feed: daemon thread entering main loop")
        while not self._stop_event.is_set():
            try:
                self._fetch_and_check()
            except Exception as exc:  # noqa: BLE001
                logger.critical(
                    "balance_feed: unhandled exception in main loop: %s", exc,
                    exc_info=True,
                )

            # Sleep in 1-second slices so we respond to stop quickly
            elapsed = 0.0
            while elapsed < self._interval_s and not self._stop_event.is_set():
                time.sleep(1.0)
                elapsed += 1.0

        logger.debug("balance_feed: daemon thread exiting")

    def _fetch_and_check(self) -> None:
        """Single fetch-and-check cycle.

        1. Fetch equity from exchange (lazy import).
        2. Pass to guardian.run_check(exchange_balance=equity or None).
        3. Update _status fields under lock.
        4. On HALT severity in live mode: log CRITICAL.
        5. On max_consecutive_failures+: log ERROR with context.
        """
        equity = self._fetch_equity()

        # --- Update status ---------------------------------------------------
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._status.total_checks += 1
            if equity is not None:
                self._status.consecutive_failures = 0
                self._status.last_equity = equity
                self._status.last_fetch_ts = now_iso
                self._status.last_error = None
            else:
                self._status.consecutive_failures += 1
            consecutive_failures = self._status.consecutive_failures

        # Log sustained failure
        if equity is None and consecutive_failures >= self._max_consecutive_failures:
            logger.error(
                "balance_feed: %d consecutive fetch failures "
                "(demo_mode=%s, interval=%.1fs)",
                consecutive_failures,
                self._demo_mode,
                self._interval_s,
            )

        # --- Feed guardian ---------------------------------------------------
        try:
            from runtime.live_balance_guardian import get_guardian  # noqa: PLC0415
            guardian = get_guardian()
            result = guardian.run_check(exchange_balance=equity)

            # In live mode, a HALT result warrants a CRITICAL log
            if not self._demo_mode:
                try:
                    from runtime.live_balance_guardian import BalanceSeverity  # noqa: PLC0415
                    if result.severity == BalanceSeverity.HALT:
                        logger.critical(
                            "balance_feed: guardian returned HALT "
                            "(exchange_equity=%.4f divergence=%.2f%%)",
                            equity if equity is not None else float("nan"),
                            result.divergence_pct,
                        )
                except Exception:  # noqa: BLE001
                    pass

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "balance_feed: guardian.run_check failed: %s", exc
            )

    def _fetch_equity(self) -> Optional[float]:
        """Fetch derivatives wallet equity from Crypto.com exchange.

        Uses lazy import of trading.exchange.get_derivatives_balance.
        Returns float(equity) on success, None on any failure.
        Logs WARNING on failure.
        """
        try:
            from trading.exchange import get_derivatives_balance  # noqa: PLC0415
            data = get_derivatives_balance()
            if not data or "equity" not in data:
                raise ValueError(f"Unexpected response from get_derivatives_balance: {data!r}")
            equity = float(data["equity"])
            logger.debug("balance_feed: fetched equity=%.4f", equity)
            return equity
        except Exception as exc:  # noqa: BLE001
            err_msg = str(exc)
            logger.warning("balance_feed: _fetch_equity failed: %s", err_msg)
            with self._lock:
                self._status.last_error = err_msg
            return None


# ── Process-level singleton ────────────────────────────────────────────────────

_daemon_instance: Optional[BalanceFeedDaemon] = None
_daemon_lock = threading.Lock()


def get_balance_feed_daemon(
    interval_s: float = 30.0,
    demo_mode: bool = True,
) -> BalanceFeedDaemon:
    """Return the process-singleton BalanceFeedDaemon (create if needed).

    Uses double-checked locking to avoid race conditions.
    The daemon is *not* automatically started; call .start() explicitly.
    """
    global _daemon_instance
    if _daemon_instance is None:
        with _daemon_lock:
            if _daemon_instance is None:
                _daemon_instance = BalanceFeedDaemon(
                    interval_s=interval_s,
                    demo_mode=demo_mode,
                )
    return _daemon_instance
