"""Shared data types for all research, backtesting, and analytics modules.

These types form the contract between:
  - Backtesting engine ↔ Analytics
  - Walk-forward engine ↔ Optimizer
  - Regime classifier ↔ Portfolio allocator
  - Exchange intelligence ↔ Execution quality

All modules import from here — never re-define these types elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


# ── Market data ───────────────────────────────────────────────────────────────

@dataclass
class Candle:
    ts:     int    # Unix timestamp in milliseconds
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def range(self) -> float:
        return self.high - self.low


@dataclass
class OrderBook:
    ts:   int
    bids: List[Tuple[float, float]]   # [(price, qty), ...]
    asks: List[Tuple[float, float]]

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> float:
        mid = (self.best_bid + self.best_ask) / 2.0
        return (self.spread / mid * 10_000) if mid > 0 else 0.0


# ── Signal & strategy ─────────────────────────────────────────────────────────

@dataclass
class Signal:
    symbol:     str
    strategy:   str
    action:     str      # "long" | "short" | "hold"
    confidence: float    # 0.0–1.0
    sl_pct:     float
    tp_pct:     float
    metadata:   Dict[str, Any] = field(default_factory=dict)


# ── Backtest trade record ─────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    trade_id:              str
    symbol:                str
    strategy:              str
    side:                  str       # "long" | "short"
    entry_time:            datetime
    exit_time:             datetime
    entry_price:           float
    exit_price:            float
    size:                  float     # contracts / units
    gross_pnl:             float
    fees:                  float
    net_pnl:               float
    net_pnl_pct:           float     # % return on notional
    entry_slippage:        float     # $ cost of entry slippage
    exit_slippage:         float     # $ cost of exit slippage
    max_adverse_excursion: float     # MAE in $
    max_favorable_excursion: float   # MFE in $
    holding_bars:          int
    exit_reason:           str = ""  # "tp" | "sl" | "signal" | "end"
    funding_paid:          float = 0.0


@dataclass
class BacktestResult:
    strategy:        str
    symbol:          str
    params:          Dict[str, Any]
    trades:          List[BacktestTrade]
    equity_curve:    List[float]        # portfolio value at each bar
    timestamps:      List[datetime]
    initial_capital: float
    final_capital:   float
    start_time:      datetime
    end_time:        datetime
    metadata:        Dict[str, Any] = field(default_factory=dict)


# ── Performance metrics ───────────────────────────────────────────────────────

@dataclass
class PerformanceMetrics:
    # Returns
    total_return_pct:     float
    annualized_return_pct: float
    cagr:                 float

    # Risk-adjusted
    sharpe_ratio:         float
    sortino_ratio:        float
    calmar_ratio:         float
    omega_ratio:          float

    # Drawdown
    max_drawdown_pct:            float
    max_drawdown_duration_bars:  int
    recovery_factor:             float

    # Trade statistics
    total_trades:    int
    winning_trades:  int
    losing_trades:   int
    win_rate:        float        # 0.0–1.0
    profit_factor:   float
    payoff_ratio:    float
    expectancy:      float        # avg net_pnl per trade

    # Distribution
    avg_win:      float
    avg_loss:     float
    largest_win:  float
    largest_loss: float

    # Streaks
    max_win_streak:  int
    max_loss_streak: int

    # Execution cost
    avg_holding_bars: float
    total_fees:       float
    total_slippage:   float


# ── Walk-forward ──────────────────────────────────────────────────────────────

@dataclass
class WalkForwardWindow:
    window_id:       int
    train_start:     datetime
    train_end:       datetime
    test_start:      datetime
    test_end:        datetime
    train_candles:   List[Candle]
    test_candles:    List[Candle]
    best_params:     Dict[str, Any]
    train_metrics:   Optional[PerformanceMetrics] = None
    test_metrics:    Optional[PerformanceMetrics] = None
    overfit_score:   float = 0.0   # 0 = no overfit, 1 = severe overfit


@dataclass
class WalkForwardResult:
    windows:             List[WalkForwardWindow]
    combined_oos_trades: List[BacktestTrade]
    oos_metrics:         Optional[PerformanceMetrics]
    parameter_stability: float    # 0–1, 1 = perfectly stable
    overfit_detected:    bool
    regime_breakdown:    Dict[str, PerformanceMetrics] = field(default_factory=dict)


# ── Monte Carlo ───────────────────────────────────────────────────────────────

@dataclass
class MonteCarloResult:
    n_simulations:          int
    confidence_level:       float      # e.g. 0.95
    max_drawdown_median:    float
    max_drawdown_p5:        float      # 5th percentile (worst)
    max_drawdown_p95:       float      # 95th percentile (best)
    ruin_probability:       float      # P(drawdown > ruin_threshold)
    expected_annual_return: float
    return_ci_lower:        float      # lower confidence bound
    return_ci_upper:        float
    survivability:          float      # P(positive equity after N years)
    capital_adequacy_multiplier: float  # how much more capital needed


# ── Regime state ──────────────────────────────────────────────────────────────

@dataclass
class RegimeState:
    trending:           bool
    ranging:            bool
    vol_expanding:      bool
    vol_compressing:    bool
    momentum_dominant:  bool
    mean_reverting:     bool
    liquidity_drought:  bool
    panic_conditions:   bool
    regime_score:       float   # composite 0–1
    label:              str     # human-readable label
    adx:                float = 0.0
    atr_ratio:          float = 0.0   # current ATR / baseline ATR
    bb_width_pct:       float = 0.0
    rsi:                float = 50.0


# ── Portfolio allocation ──────────────────────────────────────────────────────

@dataclass
class AllocationWeights:
    strategy_weights:  Dict[str, float]    # {strategy_name: allocation_weight}
    pair_allocations:  Dict[str, float]    # {symbol: allocation_pct}
    leverage_caps:     Dict[str, float]    # {symbol: max_leverage}
    risk_pct:          float               # base risk % per trade
    cooldown_active:   bool
    rationale:         str
    regime_label:      str = ""
    timestamp:         Optional[datetime] = None


# ── Execution quality ─────────────────────────────────────────────────────────

@dataclass
class ExecutionRecord:
    order_id:          str
    symbol:            str
    side:              str
    intended_price:    float
    fill_price:        float
    size:              float
    latency_ms:        float
    slippage_bps:      float
    adverse_selection: float    # adverse price move in ms before fill
    venue:             str
    timestamp:         datetime
    fill_status:       str      # "full" | "partial" | "rejected"
    reject_reason:     str = ""


@dataclass
class VenueScore:
    venue:            str
    reliability:      float    # 0–1
    liquidity:        float    # 0–1
    execution:        float    # 0–1
    composite:        float    # 0–1
    avg_slippage_bps: float
    avg_latency_ms:   float
    rejection_rate:   float
    outage_count_24h: int
    last_updated:     Optional[datetime] = None


# ── Optimization ─────────────────────────────────────────────────────────────

@dataclass
class OptimizationResult:
    strategy:   str
    symbol:     str
    params:     Dict[str, Any]
    score:      float
    metric:     str        # what was optimized (e.g. "sharpe_ratio")
    metrics:    Optional[PerformanceMetrics] = None
    timestamp:  Optional[datetime] = None
    metadata:   Dict[str, Any] = field(default_factory=dict)
