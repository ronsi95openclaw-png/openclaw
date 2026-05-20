"""Performance metrics computation from BacktestResult.

All functions operate on plain Python lists / floats — no pandas dependency.
numpy is used only for statistical operations where speed matters.
"""
from __future__ import annotations

import math
from typing import List

import numpy as np

from research.types import BacktestResult, BacktestTrade, PerformanceMetrics


# ── Core entry point ──────────────────────────────────────────────────────────

def compute_performance_metrics(
    result: BacktestResult,
    risk_free_rate: float = 0.05,
    bars_per_year: float = 365 * 24 * 4,   # 15-minute bars
) -> PerformanceMetrics:
    """Compute all PerformanceMetrics from a BacktestResult.

    Handles edge cases: empty trades, all wins, all losses, single trade.

    Parameters
    ----------
    result:          completed BacktestResult from BacktestEngine.run()
    risk_free_rate:  annualised risk-free rate (default 5%)
    bars_per_year:   number of bars per calendar year (15m default)
    """
    trades   = result.trades
    equity   = result.equity_curve
    init_cap = result.initial_capital
    fin_cap  = result.final_capital

    # ── Returns ────────────────────────────────────────────────────────────
    total_return_pct = ((fin_cap - init_cap) / init_cap * 100.0) if init_cap > 0 else 0.0

    n_bars = max(1, len(equity))
    years  = n_bars / bars_per_year

    if years > 0 and init_cap > 0 and fin_cap > 0:
        cagr = (math.pow(fin_cap / init_cap, 1.0 / years) - 1.0) * 100.0
    else:
        cagr = 0.0

    annualized_return_pct = cagr  # for perpetual futures, CAGR == annualised return

    # ── Equity-based stats ──────────────────────────────────────────────────
    bar_returns = _bar_returns(equity)

    rf_per_bar = (1.0 + risk_free_rate) ** (1.0 / bars_per_year) - 1.0

    sharpe  = _sharpe(bar_returns, rf_per_bar, bars_per_year)
    sortino = _sortino(bar_returns, rf_per_bar, bars_per_year)
    omega   = _omega(bar_returns, rf_per_bar)

    # ── Drawdown ───────────────────────────────────────────────────────────
    from research.analytics.drawdown import (
        compute_max_drawdown,
        drawdown_duration,
        recovery_factor as _recovery,
        calmar_ratio as _calmar,
    )
    max_dd_pct, _dd_start, _dd_end = compute_max_drawdown(equity)
    dd_dur = drawdown_duration(equity)
    rec    = _recovery(total_return_pct, abs(max_dd_pct))
    cal    = _calmar(annualized_return_pct, abs(max_dd_pct))

    # ── Trade statistics ───────────────────────────────────────────────────
    from research.analytics.expectancy import (
        compute_expectancy,
        compute_profit_factor,
        compute_payoff_ratio,
        streak_analysis,
    )
    total  = len(trades)
    wins   = [t for t in trades if t.net_pnl >= 0]
    losses = [t for t in trades if t.net_pnl < 0]

    win_count  = len(wins)
    loss_count = len(losses)
    win_rate   = win_count / total if total > 0 else 0.0

    expectancy   = compute_expectancy(trades)
    pf           = compute_profit_factor(trades)
    payoff       = compute_payoff_ratio(trades)

    avg_win  = float(np.mean([t.net_pnl for t in wins]))   if wins   else 0.0
    avg_loss = float(np.mean([t.net_pnl for t in losses])) if losses else 0.0

    largest_win  = max((t.net_pnl for t in wins),   default=0.0)
    largest_loss = min((t.net_pnl for t in losses), default=0.0)

    streaks = streak_analysis(trades)
    max_win_streak  = int(streaks.get("max_win_streak",  0))
    max_loss_streak = int(streaks.get("max_loss_streak", 0))

    avg_holding = float(np.mean([t.holding_bars for t in trades])) if trades else 0.0
    total_fees  = sum(t.fees for t in trades)
    total_slip  = sum(t.entry_slippage + t.exit_slippage for t in trades)

    return PerformanceMetrics(
        total_return_pct=total_return_pct,
        annualized_return_pct=annualized_return_pct,
        cagr=cagr,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=cal,
        omega_ratio=omega,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_duration_bars=dd_dur,
        recovery_factor=rec,
        total_trades=total,
        winning_trades=win_count,
        losing_trades=loss_count,
        win_rate=win_rate,
        profit_factor=pf,
        payoff_ratio=payoff,
        expectancy=expectancy,
        avg_win=avg_win,
        avg_loss=avg_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        max_win_streak=max_win_streak,
        max_loss_streak=max_loss_streak,
        avg_holding_bars=avg_holding,
        total_fees=total_fees,
        total_slippage=total_slip,
    )


# ── Equity reconstruction ─────────────────────────────────────────────────────

def compute_equity_curve(
    trades: List[BacktestTrade], initial_capital: float
) -> List[float]:
    """Reconstruct equity curve from trade net_pnl values.

    Returns one equity value per trade (end-of-trade equity).
    The first value is initial_capital; subsequent values add net_pnl cumulatively.
    """
    curve = [initial_capital]
    equity = initial_capital
    for t in trades:
        equity += t.net_pnl
        curve.append(equity)
    return curve


# ── Rolling analytics ─────────────────────────────────────────────────────────

def rolling_returns(equity: List[float], window: int) -> List[float]:
    """Compute rolling window returns (% change over window bars).

    Returns a list aligned with input; first (window) entries are 0.0.
    """
    if window <= 0 or len(equity) < 2:
        return [0.0] * len(equity)
    result: List[float] = [0.0] * window
    for i in range(window, len(equity)):
        base = equity[i - window]
        result.append((equity[i] - base) / base if base != 0 else 0.0)
    return result


def rolling_sharpe(
    equity: List[float],
    window: int = 252,
    risk_free: float = 0.0,
) -> List[float]:
    """Compute rolling Sharpe ratio over a moving window.

    Returns list of same length as equity; first (window+1) entries are 0.0.
    """
    if len(equity) < 2:
        return [0.0] * len(equity)
    bar_rets = _bar_returns(equity)
    result: List[float] = [0.0] * (window + 1)
    for i in range(window + 1, len(bar_rets)):
        window_rets = bar_rets[i - window: i]
        mean  = float(np.mean(window_rets))
        std   = float(np.std(window_rets, ddof=1))
        if std > 0:
            sharpe = (mean - risk_free) / std * math.sqrt(window)
        else:
            sharpe = 0.0
        result.append(sharpe)
    return result


def rolling_volatility(equity: List[float], window: int = 20) -> List[float]:
    """Compute rolling annualised volatility over a moving window.

    Annualises using sqrt(252) convention (daily bars assumed for denominator).
    """
    if len(equity) < 2:
        return [0.0] * len(equity)
    bar_rets = _bar_returns(equity)
    result: List[float] = [0.0] * (window + 1)
    for i in range(window + 1, len(bar_rets)):
        window_rets = bar_rets[i - window: i]
        std = float(np.std(window_rets, ddof=1))
        result.append(std * math.sqrt(252))
    return result


# ── Private helpers ───────────────────────────────────────────────────────────

def _bar_returns(equity: List[float]) -> List[float]:
    """Bar-over-bar returns from equity curve. Returns [] for empty input."""
    if len(equity) < 2:
        return []
    rets: List[float] = []
    for i in range(1, len(equity)):
        base = equity[i - 1]
        rets.append((equity[i] - base) / base if base != 0 else 0.0)
    return rets


def _sharpe(
    bar_returns: List[float],
    rf_per_bar: float,
    bars_per_year: float,
) -> float:
    """Annualised Sharpe ratio from bar returns."""
    if not bar_returns:
        return 0.0
    arr   = np.array(bar_returns, dtype=float)
    excess = arr - rf_per_bar
    mean   = float(np.mean(excess))
    std    = float(np.std(excess, ddof=1)) if len(excess) > 1 else 0.0
    if std == 0:
        return 0.0
    return float(mean / std * math.sqrt(bars_per_year))


def _sortino(
    bar_returns: List[float],
    rf_per_bar: float,
    bars_per_year: float,
) -> float:
    """Annualised Sortino ratio (downside deviation denominator)."""
    if not bar_returns:
        return 0.0
    arr    = np.array(bar_returns, dtype=float)
    excess = arr - rf_per_bar
    mean   = float(np.mean(excess))
    down   = excess[excess < 0]
    if len(down) == 0:
        return float("inf") if mean > 0 else 0.0
    downside_std = float(np.sqrt(np.mean(down ** 2)))
    if downside_std == 0:
        return 0.0
    return float(mean / downside_std * math.sqrt(bars_per_year))


def _omega(bar_returns: List[float], threshold: float) -> float:
    """Omega ratio: sum of gains above threshold / sum of losses below threshold."""
    if not bar_returns:
        return 1.0
    gains  = sum(max(r - threshold, 0.0) for r in bar_returns)
    losses = sum(max(threshold - r, 0.0) for r in bar_returns)
    if losses == 0:
        return float("inf") if gains > 0 else 1.0
    return gains / losses
