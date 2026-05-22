"""Prometheus metrics module for OpenClaw.

All metric objects are created at import time.  If prometheus_client is not
installed (or the import fails for any reason), every object is replaced by a
no-op stub so callers never need to guard against ImportError.

Usage
-----
    from runtime.metrics import get_registry, start_http_server

    m = get_registry()
    m.update_capital_state("SAFE", 0.5)
    m.record_trade_opened("EMA_CROSS", "BTCUSD-PERP", "long")

    # Start scrape endpoint (idempotent; logs error if port in use)
    start_http_server(port=9090)

Thread safety
-------------
All MetricsRegistry methods delegate to prometheus_client objects which are
themselves thread-safe.  The singleton accessor get_registry() is guarded by a
module-level lock.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger("openclaw.runtime.metrics")

# ── prometheus_client import (graceful fallback) ──────────────────────────────

_PROMETHEUS_AVAILABLE = False

try:
    from prometheus_client import (  # type: ignore[import]
        Counter,
        Gauge,
        Histogram,
        start_http_server as _prom_start_http_server,
        CollectorRegistry,
        REGISTRY as _DEFAULT_REGISTRY,
    )
    _PROMETHEUS_AVAILABLE = True
    logger.debug("prometheus_client loaded successfully")
except Exception as _prom_import_err:  # noqa: BLE001
    logger.info(
        "prometheus_client unavailable (%s) — using no-op stubs",
        _prom_import_err,
    )


# ── No-op stubs (used when prometheus_client is absent) ──────────────────────

class _NoOpMetric:
    """Drop-in stub for any prometheus_client metric object."""

    def labels(self, **_kwargs) -> "_NoOpMetric":  # noqa: ANN001
        return self

    def set(self, _value) -> None:  # noqa: ANN001
        pass

    def inc(self, _amount: float = 1) -> None:
        pass

    def dec(self, _amount: float = 1) -> None:
        pass

    def observe(self, _value: float) -> None:
        pass

    # context-manager support for Histogram.time()
    def time(self):  # noqa: ANN201
        return _NoOpContextManager()

    def __call__(self, *args, **kwargs):  # noqa: ANN204
        return _NoOpContextManager()


class _NoOpContextManager:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass


def _make_gauge(name: str, doc: str, labelnames=()) -> object:
    if _PROMETHEUS_AVAILABLE:
        return Gauge(name, doc, labelnames)  # type: ignore[return-value]
    return _NoOpMetric()


def _make_counter(name: str, doc: str, labelnames=()) -> object:
    if _PROMETHEUS_AVAILABLE:
        return Counter(name, doc, labelnames)  # type: ignore[return-value]
    return _NoOpMetric()


def _make_histogram(
    name: str,
    doc: str,
    labelnames=(),
    buckets=None,
) -> object:
    if _PROMETHEUS_AVAILABLE:
        kwargs = {} if buckets is None else {"buckets": buckets}
        return Histogram(name, doc, labelnames, **kwargs)  # type: ignore[return-value]
    return _NoOpMetric()


# ── Metric definitions ────────────────────────────────────────────────────────
# All metrics are module-level singletons created once at import time.

_capital_state = _make_gauge(
    "openclaw_capital_state",
    "Capital preservation state (0/1 active flag per state label)",
    labelnames=["state"],
)

_open_positions = _make_gauge(
    "openclaw_open_positions",
    "Number of currently open positions",
)

_total_pnl = _make_gauge(
    "openclaw_total_pnl",
    "Running total PnL in USD",
)

_daily_drawdown_pct = _make_gauge(
    "openclaw_daily_drawdown_pct",
    "Daily drawdown percentage (0–100)",
)

_scan_duration_seconds = _make_histogram(
    "openclaw_scan_duration_seconds",
    "Duration of each 30-second scan loop iteration in seconds",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

_intent_approved_total = _make_counter(
    "openclaw_intent_approved_total",
    "Total number of trading intents approved by the IntentPipeline",
    labelnames=["strategy", "symbol"],
)

_intent_rejected_total = _make_counter(
    "openclaw_intent_rejected_total",
    "Total number of trading intents rejected by the IntentPipeline",
    labelnames=["strategy", "reason"],
)

_trade_opened_total = _make_counter(
    "openclaw_trade_opened_total",
    "Total number of trades opened",
    labelnames=["strategy", "symbol", "side"],
)

_trade_closed_total = _make_counter(
    "openclaw_trade_closed_total",
    "Total number of trades closed",
    labelnames=["strategy", "outcome"],
)

_capital_transitions_total = _make_counter(
    "openclaw_capital_transitions_total",
    "Total number of capital state machine transitions",
    labelnames=["from_state", "to_state"],
)

_exchange_errors_total = _make_counter(
    "openclaw_exchange_errors_total",
    "Total number of exchange API errors",
    labelnames=["operation"],
)

_websocket_connections = _make_gauge(
    "openclaw_websocket_connections",
    "Number of active WebSocket connections to the dashboard",
)

_reconciliation_mismatches = _make_gauge(
    "openclaw_reconciliation_mismatches",
    "Number of unresolved position reconciliation mismatches",
)


# ── MetricsRegistry ───────────────────────────────────────────────────────────

class MetricsRegistry:
    """Thin facade over the module-level Prometheus metric objects.

    All methods are safe to call even when prometheus_client is unavailable;
    in that case every call is a no-op with no exception raised.

    Obtain the singleton via ``get_registry()``.
    """

    # ── Capital / drawdown ────────────────────────────────────────────────────

    def update_capital_state(self, state_name: str, drawdown_pct: float) -> None:
        """Set the active capital state flag and update daily drawdown.

        Resets all known state gauges to 0, then sets state_name → 1.
        Known states: SAFE, DEFENSIVE, CRITICAL, EMERGENCY_HALT.

        Parameters
        ----------
        state_name :
            The active CapitalState name (e.g. "SAFE").
        drawdown_pct :
            Current daily drawdown percentage (e.g. 3.7 for 3.7 %).
        """
        _known_states = ("SAFE", "DEFENSIVE", "CRITICAL", "EMERGENCY_HALT")
        try:
            for s in _known_states:
                _capital_state.labels(state=s).set(0)
            _capital_state.labels(state=state_name).set(1)
            _daily_drawdown_pct.set(drawdown_pct)
        except Exception as exc:  # noqa: BLE001
            logger.debug("metrics.update_capital_state error: %s", exc)

    # ── Positions / PnL ───────────────────────────────────────────────────────

    def update_positions(self, count: int) -> None:
        """Set the open-positions gauge to *count*."""
        try:
            _open_positions.set(count)
        except Exception as exc:  # noqa: BLE001
            logger.debug("metrics.update_positions error: %s", exc)

    def update_pnl(self, usd: float) -> None:
        """Set the running total PnL gauge to *usd*."""
        try:
            _total_pnl.set(usd)
        except Exception as exc:  # noqa: BLE001
            logger.debug("metrics.update_pnl error: %s", exc)

    # ── Scan timing ───────────────────────────────────────────────────────────

    def record_scan_duration(self, seconds: float) -> None:
        """Record a single scan-loop duration observation."""
        try:
            _scan_duration_seconds.observe(seconds)
        except Exception as exc:  # noqa: BLE001
            logger.debug("metrics.record_scan_duration error: %s", exc)

    # ── Intent pipeline ───────────────────────────────────────────────────────

    def record_intent(
        self,
        approved: bool,
        strategy: str,
        symbol: str,
        reason: str = "",
    ) -> None:
        """Increment either the approved or rejected intent counter.

        Parameters
        ----------
        approved :
            True → increment openclaw_intent_approved_total{strategy, symbol}.
            False → increment openclaw_intent_rejected_total{strategy, reason}.
        strategy :
            Strategy name label (e.g. "EMA_CROSS").
        symbol :
            Trading symbol label (e.g. "BTCUSD-PERP").
        reason :
            Rejection reason label — only used when approved=False.
        """
        try:
            if approved:
                _intent_approved_total.labels(
                    strategy=strategy, symbol=symbol
                ).inc()
            else:
                _intent_rejected_total.labels(
                    strategy=strategy, reason=reason
                ).inc()
        except Exception as exc:  # noqa: BLE001
            logger.debug("metrics.record_intent error: %s", exc)

    # ── Trades ────────────────────────────────────────────────────────────────

    def record_trade_opened(
        self, strategy: str, symbol: str, side: str
    ) -> None:
        """Increment the trade-opened counter.

        Parameters
        ----------
        strategy : e.g. "EMA_CROSS"
        symbol   : e.g. "BTCUSD-PERP"
        side     : "long" or "short"
        """
        try:
            _trade_opened_total.labels(
                strategy=strategy, symbol=symbol, side=side
            ).inc()
        except Exception as exc:  # noqa: BLE001
            logger.debug("metrics.record_trade_opened error: %s", exc)

    def record_trade_closed(self, strategy: str, outcome: str) -> None:
        """Increment the trade-closed counter.

        Parameters
        ----------
        strategy : e.g. "EMA_CROSS"
        outcome  : "win", "loss", or "breakeven"
        """
        try:
            _trade_closed_total.labels(
                strategy=strategy, outcome=outcome
            ).inc()
        except Exception as exc:  # noqa: BLE001
            logger.debug("metrics.record_trade_closed error: %s", exc)

    # ── Capital state machine ─────────────────────────────────────────────────

    def record_capital_transition(
        self, from_state: str, to_state: str
    ) -> None:
        """Increment the capital state transition counter."""
        try:
            _capital_transitions_total.labels(
                from_state=from_state, to_state=to_state
            ).inc()
        except Exception as exc:  # noqa: BLE001
            logger.debug("metrics.record_capital_transition error: %s", exc)

    # ── Exchange errors ───────────────────────────────────────────────────────

    def record_exchange_error(self, operation: str) -> None:
        """Increment the exchange-error counter for a given operation.

        Parameters
        ----------
        operation : e.g. "place_order", "fetch_balance", "cancel_order"
        """
        try:
            _exchange_errors_total.labels(operation=operation).inc()
        except Exception as exc:  # noqa: BLE001
            logger.debug("metrics.record_exchange_error error: %s", exc)

    # ── WebSocket ─────────────────────────────────────────────────────────────

    def set_websocket_count(self, n: int) -> None:
        """Set the active WebSocket connection gauge to *n*."""
        try:
            _websocket_connections.set(n)
        except Exception as exc:  # noqa: BLE001
            logger.debug("metrics.set_websocket_count error: %s", exc)

    # ── Reconciliation ────────────────────────────────────────────────────────

    def set_reconciliation_mismatches(self, n: int) -> None:
        """Set the reconciliation-mismatches gauge to *n*."""
        try:
            _reconciliation_mismatches.set(n)
        except Exception as exc:  # noqa: BLE001
            logger.debug("metrics.set_reconciliation_mismatches error: %s", exc)


# ── Singleton accessor ────────────────────────────────────────────────────────

_registry_instance: Optional[MetricsRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> MetricsRegistry:
    """Return the process-wide MetricsRegistry singleton.

    Thread-safe; safe to call from any thread or at import time.
    """
    global _registry_instance
    if _registry_instance is None:
        with _registry_lock:
            if _registry_instance is None:
                _registry_instance = MetricsRegistry()
    return _registry_instance


# ── HTTP server ───────────────────────────────────────────────────────────────

def start_http_server(port: int = 9090) -> bool:
    """Start the Prometheus HTTP scrape endpoint on *port*.

    Returns True on success.  Logs an error and returns False when:
      - prometheus_client is not installed
      - the port is already in use
      - any other OSError occurs during bind

    The function is intentionally idempotent — calling it multiple times on
    the same port will log an error on the second call and return False rather
    than raising.

    Parameters
    ----------
    port :
        TCP port to listen on (default 9090).
    """
    if not _PROMETHEUS_AVAILABLE:
        logger.error(
            "Cannot start Prometheus HTTP server: "
            "prometheus_client is not installed"
        )
        return False

    try:
        _prom_start_http_server(port)
        logger.info("Prometheus metrics server started on port %d", port)
        return True
    except OSError as exc:
        logger.error(
            "Cannot start Prometheus HTTP server on port %d: %s",
            port, exc,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Unexpected error starting Prometheus HTTP server on port %d: %s",
            port, exc,
        )
        return False
