"""Prometheus metrics registry for OpenClaw.

All Prometheus counters, gauges, and histograms live here.
Import this module to access metrics — never re-create them in other modules.

Usage:
    from core.metrics import (
        TRADES_TOTAL, TRADE_PNL, STRATEGY_SHARPE,
        REGIME_GAUGE, SLIPPAGE_HISTOGRAM, ...
    )

    # Increment counter
    TRADES_TOTAL.labels(strategy="EMA_CROSS", outcome="win").inc()

    # Set gauge
    STRATEGY_SHARPE.labels(strategy="EMA_CROSS").set(1.42)

    # Observe histogram
    SLIPPAGE_HISTOGRAM.labels(venue="blofin").observe(4.5)  # bps

Start metrics server:
    from core.metrics import start_metrics_server
    start_metrics_server(port=9090)
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger("openclaw.metrics")

# ── Guard against missing prometheus_client ───────────────────────────────────

try:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        Summary,
        start_http_server,
        REGISTRY,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    logger.warning("prometheus_client not installed — metrics will be no-ops")

    # Stub classes for graceful degradation
    class _Stub:
        def labels(self, **_kw) -> "_Stub": return self
        def inc(self, _v=1): pass
        def dec(self, _v=1): pass
        def set(self, _v):   pass
        def observe(self, _v): pass

    Counter   = lambda *a, **kw: _Stub()  # noqa: E731
    Gauge     = lambda *a, **kw: _Stub()  # noqa: E731
    Histogram = lambda *a, **kw: _Stub()  # noqa: E731
    Summary   = lambda *a, **kw: _Stub()  # noqa: E731
    def start_http_server(_port: int): pass  # noqa: E301


# ── Trading performance ───────────────────────────────────────────────────────

TRADES_TOTAL = Counter(
    "openclaw_trades_total",
    "Total number of completed trades",
    ["strategy", "symbol", "side", "outcome"],  # outcome: win/loss/unknown
)

TRADE_PNL = Histogram(
    "openclaw_trade_pnl_usd",
    "Per-trade net P&L in USD",
    ["strategy", "symbol"],
    buckets=[-100, -50, -20, -10, -5, -2, 0, 2, 5, 10, 20, 50, 100, 200],
)

OPEN_POSITIONS = Gauge(
    "openclaw_open_positions",
    "Number of currently open positions",
    ["symbol"],
)

TOTAL_PNL = Gauge(
    "openclaw_total_pnl_usd",
    "Total realized P&L in USD",
)

UNREALIZED_PNL = Gauge(
    "openclaw_unrealized_pnl_usd",
    "Total unrealized P&L across open positions",
)

PORTFOLIO_BALANCE = Gauge(
    "openclaw_portfolio_balance_usd",
    "Current portfolio balance in USD",
)

# ── Risk-adjusted metrics (updated by analytics engine) ──────────────────────

STRATEGY_SHARPE = Gauge(
    "openclaw_strategy_sharpe",
    "Rolling Sharpe ratio per strategy (last N trades)",
    ["strategy"],
)

STRATEGY_SORTINO = Gauge(
    "openclaw_strategy_sortino",
    "Rolling Sortino ratio per strategy",
    ["strategy"],
)

STRATEGY_WIN_RATE = Gauge(
    "openclaw_strategy_win_rate",
    "Win rate per strategy (0.0–1.0)",
    ["strategy"],
)

STRATEGY_WEIGHT = Gauge(
    "openclaw_strategy_weight",
    "Current adaptive weight per strategy (0.2–2.0)",
    ["strategy"],
)

STRATEGY_EXPECTANCY = Gauge(
    "openclaw_strategy_expectancy_usd",
    "Expected P&L per trade for each strategy in USD",
    ["strategy"],
)

STRATEGY_PROFIT_FACTOR = Gauge(
    "openclaw_strategy_profit_factor",
    "Profit factor per strategy",
    ["strategy"],
)

# ── Drawdown ──────────────────────────────────────────────────────────────────

CURRENT_DRAWDOWN = Gauge(
    "openclaw_current_drawdown_pct",
    "Current drawdown from peak as a percentage (0–100)",
)

MAX_DRAWDOWN = Gauge(
    "openclaw_max_drawdown_pct",
    "All-time maximum drawdown percentage",
)

DRAWDOWN_DURATION = Gauge(
    "openclaw_drawdown_duration_trades",
    "Number of trades since last equity peak",
)

# ── Monte Carlo projections (updated after each MC run) ──────────────────────

MC_RUIN_PROBABILITY = Gauge(
    "openclaw_mc_ruin_probability",
    "Monte Carlo estimated probability of ruin (0.0–1.0)",
)

MC_EXPECTED_RETURN_P50 = Gauge(
    "openclaw_mc_expected_return_p50_pct",
    "Monte Carlo median expected return percentage",
)

MC_MAX_DD_P95 = Gauge(
    "openclaw_mc_max_drawdown_p95_pct",
    "Monte Carlo 95th percentile max drawdown",
)

MC_SIMULATIONS_TOTAL = Counter(
    "openclaw_mc_simulations_total",
    "Total Monte Carlo simulations run",
)

# ── Regime ────────────────────────────────────────────────────────────────────

REGIME_LABEL = Gauge(
    "openclaw_market_regime",
    "Current market regime encoded as int (for label tracking use regime_str label)",
    ["symbol", "regime_str"],
)

REGIME_ADX = Gauge(
    "openclaw_regime_adx",
    "ADX value from regime classifier",
    ["symbol"],
)

REGIME_ATR_RATIO = Gauge(
    "openclaw_regime_atr_ratio",
    "Current ATR / baseline ATR ratio",
    ["symbol"],
)

REGIME_RSI = Gauge(
    "openclaw_regime_rsi",
    "Current RSI from regime classifier",
    ["symbol"],
)

# ── Optimizer ────────────────────────────────────────────────────────────────

OPTIMIZER_RUNS_TOTAL = Counter(
    "openclaw_optimizer_runs_total",
    "Total optimization runs completed",
    ["strategy", "method"],  # method: grid/random/bayesian
)

OPTIMIZER_BEST_SCORE = Gauge(
    "openclaw_optimizer_best_score",
    "Best optimization score (e.g. Sharpe) found so far",
    ["strategy", "symbol", "metric"],
)

PARAMETER_STABILITY = Gauge(
    "openclaw_parameter_stability",
    "Walk-forward parameter stability score (0–1)",
    ["strategy"],
)

OVERFIT_SCORE = Gauge(
    "openclaw_overfit_score",
    "Walk-forward overfit detection score (0 = no overfit, 1 = severe)",
    ["strategy"],
)

WALKFORWARD_RUNS_TOTAL = Counter(
    "openclaw_walkforward_runs_total",
    "Total walk-forward validation runs",
    ["strategy"],
)

# ── Execution quality ─────────────────────────────────────────────────────────

SLIPPAGE_HISTOGRAM = Histogram(
    "openclaw_slippage_bps",
    "Realized slippage per fill in basis points",
    ["venue", "symbol"],
    buckets=[0, 1, 2, 3, 5, 8, 10, 15, 20, 30, 50, 100],
)

FILL_LATENCY = Histogram(
    "openclaw_fill_latency_ms",
    "Time from signal to fill in milliseconds",
    ["venue"],
    buckets=[10, 25, 50, 100, 200, 500, 1000, 2000, 5000],
)

ADVERSE_SELECTION_RATE = Gauge(
    "openclaw_adverse_selection_rate",
    "Fraction of fills with adverse price movement before fill",
    ["venue"],
)

FILL_REJECTION_RATE = Gauge(
    "openclaw_fill_rejection_rate",
    "Fraction of orders rejected by venue",
    ["venue"],
)

EXECUTION_QUALITY_SCORE = Gauge(
    "openclaw_execution_quality_score",
    "Composite execution quality score (0–1)",
    ["venue"],
)

VENUE_SCORE = Gauge(
    "openclaw_venue_score",
    "Composite venue routing score (0–1)",
    ["venue"],
)

# ── Liquidity ─────────────────────────────────────────────────────────────────

LIQUIDITY_SCORE = Gauge(
    "openclaw_liquidity_score",
    "Current liquidity score per symbol (0–1)",
    ["symbol"],
)

VOLUME_RATIO = Gauge(
    "openclaw_volume_ratio",
    "Current bar volume / rolling average volume",
    ["symbol"],
)

SPREAD_BPS = Gauge(
    "openclaw_spread_bps",
    "Current bid-ask spread in basis points",
    ["symbol"],
)

# ── Bot operational ───────────────────────────────────────────────────────────

BOT_SCAN_TOTAL = Counter(
    "openclaw_bot_scans_total",
    "Total market scans completed",
)

BOT_SCAN_DURATION = Histogram(
    "openclaw_bot_scan_duration_seconds",
    "Duration of each market scan in seconds",
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
)

KILL_SWITCH_EVENTS = Counter(
    "openclaw_kill_switch_events_total",
    "Kill switch activation events",
    ["reason"],
)

CIRCUIT_BREAKER_EVENTS = Counter(
    "openclaw_circuit_breaker_events_total",
    "Circuit breaker trip events",
    ["trigger"],
)

STRATEGY_COOLDOWNS = Gauge(
    "openclaw_strategy_cooldown_active",
    "1 if strategy is in cooldown, 0 otherwise",
    ["strategy"],
)

# ── Queue / infrastructure ────────────────────────────────────────────────────

QUEUE_DEPTH = Gauge(
    "openclaw_queue_depth",
    "Current queue depth",
    ["queue_name"],
)

QUEUE_LAG_SECONDS = Gauge(
    "openclaw_queue_lag_seconds",
    "Age of oldest unprocessed message in seconds",
    ["queue_name"],
)

WEBSOCKET_STALE = Gauge(
    "openclaw_websocket_stale",
    "1 if websocket has not received data in > 60s",
    ["symbol"],
)

# ── Backtesting ───────────────────────────────────────────────────────────────

BACKTEST_RUNS_TOTAL = Counter(
    "openclaw_backtest_runs_total",
    "Total backtests executed",
    ["strategy", "symbol"],
)

BACKTEST_DURATION = Histogram(
    "openclaw_backtest_duration_seconds",
    "Backtest execution time in seconds",
    ["strategy"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 5, 10, 30, 60],
)

REPLAY_THROUGHPUT = Gauge(
    "openclaw_replay_candles_per_second",
    "Candle replay throughput in candles/second",
)


# ── Metrics server ────────────────────────────────────────────────────────────

_server_started = False
_server_lock    = threading.Lock()


def start_metrics_server(port: int = 9090) -> None:
    """Start Prometheus HTTP metrics server (idempotent)."""
    global _server_started
    with _server_lock:
        if _server_started:
            return
        if not _PROMETHEUS_AVAILABLE:
            logger.warning("Cannot start metrics server — prometheus_client not installed")
            return
        try:
            start_http_server(port)
            _server_started = True
            logger.info(f"Prometheus metrics server started on :{port}")
        except OSError as exc:
            logger.warning(f"Metrics server could not start on :{port}: {exc}")


# ── Convenience updaters ──────────────────────────────────────────────────────

def record_trade(
    strategy: str,
    symbol:   str,
    side:     str,
    outcome:  str,
    pnl:      float,
) -> None:
    """Record a completed trade across all relevant metrics."""
    TRADES_TOTAL.labels(strategy=strategy, symbol=symbol, side=side, outcome=outcome).inc()
    TRADE_PNL.labels(strategy=strategy, symbol=symbol).observe(pnl)


def update_strategy_metrics(
    strategy:      str,
    sharpe:        float,
    sortino:       float,
    win_rate:      float,
    weight:        float,
    expectancy:    float,
    profit_factor: float,
) -> None:
    """Batch-update all strategy performance gauges."""
    STRATEGY_SHARPE.labels(strategy=strategy).set(sharpe)
    STRATEGY_SORTINO.labels(strategy=strategy).set(sortino)
    STRATEGY_WIN_RATE.labels(strategy=strategy).set(win_rate)
    STRATEGY_WEIGHT.labels(strategy=strategy).set(weight)
    STRATEGY_EXPECTANCY.labels(strategy=strategy).set(expectancy)
    STRATEGY_PROFIT_FACTOR.labels(strategy=strategy).set(profit_factor)


def update_regime(symbol: str, regime_label: str, adx: float, atr_ratio: float, rsi: float) -> None:
    """Update regime gauges for a symbol."""
    REGIME_LABEL.labels(symbol=symbol, regime_str=regime_label).set(1)
    REGIME_ADX.labels(symbol=symbol).set(adx)
    REGIME_ATR_RATIO.labels(symbol=symbol).set(atr_ratio)
    REGIME_RSI.labels(symbol=symbol).set(rsi)


def update_execution_quality(
    venue:           str,
    slippage_bps:    float,
    latency_ms:      float,
    symbol:          str = "unknown",
    rejection_rate:  float = 0.0,
    adverse_rate:    float = 0.0,
    quality_score:   float = 0.5,
) -> None:
    """Record execution quality metrics after a fill."""
    SLIPPAGE_HISTOGRAM.labels(venue=venue, symbol=symbol).observe(slippage_bps)
    FILL_LATENCY.labels(venue=venue).observe(latency_ms)
    FILL_REJECTION_RATE.labels(venue=venue).set(rejection_rate)
    ADVERSE_SELECTION_RATE.labels(venue=venue).set(adverse_rate)
    EXECUTION_QUALITY_SCORE.labels(venue=venue).set(quality_score)
