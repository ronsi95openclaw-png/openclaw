"""Walk-forward validation metrics and regime-based analysis."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import numpy as np

from research.types import (
    BacktestTrade,
    Candle,
    PerformanceMetrics,
    WalkForwardWindow,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _zero_metrics() -> PerformanceMetrics:
    """Return a zeroed-out PerformanceMetrics for edge cases."""
    return PerformanceMetrics(
        total_return_pct=0.0,
        annualized_return_pct=0.0,
        cagr=0.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        calmar_ratio=0.0,
        omega_ratio=1.0,
        max_drawdown_pct=0.0,
        max_drawdown_duration_bars=0,
        recovery_factor=0.0,
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate=0.0,
        profit_factor=0.0,
        payoff_ratio=0.0,
        expectancy=0.0,
        avg_win=0.0,
        avg_loss=0.0,
        largest_win=0.0,
        largest_loss=0.0,
        max_win_streak=0,
        max_loss_streak=0,
        avg_holding_bars=0.0,
        total_fees=0.0,
        total_slippage=0.0,
    )


def _compute_metrics_from_trades(
    trades: List[BacktestTrade],
    bars_per_year: int = 252,
) -> PerformanceMetrics:
    """Compute PerformanceMetrics from a flat list of BacktestTrades."""
    if not trades:
        return _zero_metrics()

    pnls = np.array([t.net_pnl_pct for t in trades], dtype=float)

    total_return = float(np.sum(pnls))
    n = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]

    win_rate = len(wins) / n if n > 0 else 0.0

    avg_win = float(np.mean([t.net_pnl for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([abs(t.net_pnl) for t in losses])) if losses else 0.0
    largest_win = float(max((t.net_pnl for t in wins), default=0.0))
    largest_loss = float(min((t.net_pnl for t in losses), default=0.0))

    total_gains = sum(t.net_pnl for t in wins)
    total_losses = abs(sum(t.net_pnl for t in losses))
    profit_factor = (total_gains / total_losses) if total_losses > 1e-9 else float("inf")
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 1e-9 else float("inf")

    expectancy = float(np.mean([t.net_pnl for t in trades]))

    # Sharpe / Sortino on % returns
    if len(pnls) > 1:
        std = float(np.std(pnls, ddof=1))
        mean_r = float(np.mean(pnls))
        sharpe = (mean_r / std * math.sqrt(bars_per_year)) if std > 1e-9 else 0.0
        downside = pnls[pnls < 0]
        down_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 1e-9
        sortino = (mean_r / down_std * math.sqrt(bars_per_year)) if down_std > 1e-9 else 0.0
    else:
        sharpe = sortino = 0.0

    # Equity curve and drawdown
    equity = np.cumprod(1 + pnls / 100.0)
    running_max = np.maximum.accumulate(equity)
    drawdowns = (running_max - equity) / running_max
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    # Max drawdown duration
    max_dd_dur = 0
    cur_dur = 0
    for dd in drawdowns:
        if dd > 0:
            cur_dur += 1
            max_dd_dur = max(max_dd_dur, cur_dur)
        else:
            cur_dur = 0

    annualised = float(np.mean(pnls)) * bars_per_year
    cagr = float((equity[-1] ** (bars_per_year / n) - 1) * 100.0) if n > 0 else 0.0
    calmar = (annualised / (max_dd * 100.0)) if max_dd > 1e-9 else 0.0

    # Recovery factor
    recovery = (total_return / (max_dd * 100.0)) if max_dd > 1e-9 else 0.0

    # Omega ratio (threshold = 0)
    pos_sum = float(np.sum(pnls[pnls > 0]))
    neg_sum = float(np.sum(np.abs(pnls[pnls < 0])))
    omega = (pos_sum / neg_sum) if neg_sum > 1e-9 else float("inf")

    # Streaks
    max_win_streak = max_loss_streak = 0
    cur_win = cur_loss = 0
    for t in trades:
        if t.net_pnl > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        max_win_streak = max(max_win_streak, cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)

    avg_holding = float(np.mean([t.holding_bars for t in trades]))
    total_fees = float(sum(t.fees for t in trades))
    total_slippage = float(sum(t.entry_slippage + t.exit_slippage for t in trades))

    return PerformanceMetrics(
        total_return_pct=total_return,
        annualized_return_pct=annualised,
        cagr=cagr,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        omega_ratio=omega,
        max_drawdown_pct=max_dd * 100.0,
        max_drawdown_duration_bars=max_dd_dur,
        recovery_factor=recovery,
        total_trades=n,
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=win_rate,
        profit_factor=profit_factor,
        payoff_ratio=payoff_ratio,
        expectancy=expectancy,
        avg_win=avg_win,
        avg_loss=avg_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        max_win_streak=max_win_streak,
        max_loss_streak=max_loss_streak,
        avg_holding_bars=avg_holding,
        total_fees=total_fees,
        total_slippage=total_slippage,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def compute_parameter_stability(windows: List[WalkForwardWindow]) -> float:
    """Score 0–1 measuring consistency of optimal parameters across windows.

    Approach:
      - For each numeric parameter, compute the coefficient of variation (CV).
      - For categorical parameters, compute the fraction of windows that use the
        modal value.
      - Average across all parameters, invert CV so high consistency → high score.

    Returns:
        Stability score in [0, 1]. 1.0 means identical params in every window.
    """
    if not windows:
        return 0.0

    # Gather all param keys
    all_keys: set[str] = set()
    for w in windows:
        all_keys.update(w.best_params.keys())

    if not all_keys:
        return 1.0  # No parameters — trivially stable

    scores: List[float] = []

    for key in all_keys:
        values = [w.best_params.get(key) for w in windows if key in w.best_params]
        if not values:
            continue

        # Try numeric
        try:
            nums = [float(v) for v in values]
            mean_v = float(np.mean(nums))
            std_v = float(np.std(nums, ddof=0))
            if abs(mean_v) < 1e-12:
                # All zeros or tiny → stable
                cv = 0.0
            else:
                cv = std_v / abs(mean_v)
            # Convert CV to a stability score: CV=0 → 1.0, CV=1 → 0.5, CV→∞ → 0
            score = 1.0 / (1.0 + cv)
        except (TypeError, ValueError):
            # Categorical: fraction of windows using the modal value
            from collections import Counter
            hashable = [tuple(v) if isinstance(v, list) else v for v in values]
            counts = Counter(hashable)
            modal_freq = counts.most_common(1)[0][1]
            score = modal_freq / len(values)

        scores.append(score)

    return float(np.mean(scores)) if scores else 1.0


def compute_is_oos_ratio(windows: List[WalkForwardWindow]) -> float:
    """Ratio of average IS Sharpe to average OOS Sharpe.

    < 0.5 indicates severe overfit; > 0.8 suggests a reliable strategy.

    Returns:
        Ratio in [0, ∞). Returns 0.0 if IS metrics are unavailable.
    """
    is_sharpes: List[float] = []
    oos_sharpes: List[float] = []

    for w in windows:
        if w.train_metrics is not None:
            is_sharpes.append(w.train_metrics.sharpe_ratio)
        if w.test_metrics is not None:
            oos_sharpes.append(w.test_metrics.sharpe_ratio)

    if not is_sharpes or not oos_sharpes:
        return 0.0

    avg_is = float(np.mean(is_sharpes))
    avg_oos = float(np.mean(oos_sharpes))

    if abs(avg_is) < 1e-9:
        return 1.0 if abs(avg_oos) < 1e-9 else 0.0

    return float(avg_oos / avg_is)


def regime_breakdown(
    windows: List[WalkForwardWindow],
    regime_fn: Callable[[List[Candle]], str],
) -> Dict[str, PerformanceMetrics]:
    """Splits OOS performance by market regime.

    Args:
        windows: Walk-forward windows with test_candles and OOS trades.
        regime_fn: Callable that takes a list of candles and returns a regime
            label string (e.g. "trending", "ranging", "volatile").

    Returns:
        Dict mapping regime label → PerformanceMetrics for that regime.
    """
    regime_trades: Dict[str, List[BacktestTrade]] = {}

    for w in windows:
        if not w.test_candles:
            continue
        label = regime_fn(w.test_candles)
        # Use pre-computed test metrics' trade list if available; otherwise skip
        # The trades are stored on the window via metadata if present
        trades_for_window: List[BacktestTrade] = w.best_params.get(
            "__oos_trades__", []
        )
        if not isinstance(trades_for_window, list):
            trades_for_window = []
        if trades_for_window:
            regime_trades.setdefault(label, []).extend(trades_for_window)

    result: Dict[str, PerformanceMetrics] = {}
    for label, trades in regime_trades.items():
        result[label] = _compute_metrics_from_trades(trades)

    return result


def combined_oos_metrics(
    windows: List[WalkForwardWindow],
) -> Optional[PerformanceMetrics]:
    """Aggregate all OOS trades from every window into a single PerformanceMetrics.

    Returns:
        Combined PerformanceMetrics, or None if no windows have test metrics.
    """
    all_trades: List[BacktestTrade] = []

    for w in windows:
        # Collect trades stored under the __oos_trades__ sentinel key
        trades: object = w.best_params.get("__oos_trades__", [])
        if isinstance(trades, list):
            all_trades.extend(trades)

    if not all_trades:
        # Fall back to using per-window metrics if no trade lists stored
        has_metrics = [w for w in windows if w.test_metrics is not None]
        if not has_metrics:
            return None
        # Aggregate by averaging scalar fields (approximate)
        sharpes = [w.test_metrics.sharpe_ratio for w in has_metrics]  # type: ignore[union-attr]
        returns = [w.test_metrics.total_return_pct for w in has_metrics]  # type: ignore[union-attr]
        m0 = has_metrics[0].test_metrics  # type: ignore[union-attr]
        # Return a representative metrics object from the first window
        # but with averaged key metrics
        return PerformanceMetrics(
            total_return_pct=float(np.sum(returns)),
            annualized_return_pct=float(np.mean([w.test_metrics.annualized_return_pct for w in has_metrics])),  # type: ignore[union-attr]
            cagr=float(np.mean([w.test_metrics.cagr for w in has_metrics])),  # type: ignore[union-attr]
            sharpe_ratio=float(np.mean(sharpes)),
            sortino_ratio=float(np.mean([w.test_metrics.sortino_ratio for w in has_metrics])),  # type: ignore[union-attr]
            calmar_ratio=float(np.mean([w.test_metrics.calmar_ratio for w in has_metrics])),  # type: ignore[union-attr]
            omega_ratio=float(np.mean([w.test_metrics.omega_ratio for w in has_metrics])),  # type: ignore[union-attr]
            max_drawdown_pct=float(np.max([w.test_metrics.max_drawdown_pct for w in has_metrics])),  # type: ignore[union-attr]
            max_drawdown_duration_bars=int(np.max([w.test_metrics.max_drawdown_duration_bars for w in has_metrics])),  # type: ignore[union-attr]
            recovery_factor=float(np.mean([w.test_metrics.recovery_factor for w in has_metrics])),  # type: ignore[union-attr]
            total_trades=int(np.sum([w.test_metrics.total_trades for w in has_metrics])),  # type: ignore[union-attr]
            winning_trades=int(np.sum([w.test_metrics.winning_trades for w in has_metrics])),  # type: ignore[union-attr]
            losing_trades=int(np.sum([w.test_metrics.losing_trades for w in has_metrics])),  # type: ignore[union-attr]
            win_rate=float(np.mean([w.test_metrics.win_rate for w in has_metrics])),  # type: ignore[union-attr]
            profit_factor=float(np.mean([w.test_metrics.profit_factor for w in has_metrics])),  # type: ignore[union-attr]
            payoff_ratio=float(np.mean([w.test_metrics.payoff_ratio for w in has_metrics])),  # type: ignore[union-attr]
            expectancy=float(np.mean([w.test_metrics.expectancy for w in has_metrics])),  # type: ignore[union-attr]
            avg_win=float(np.mean([w.test_metrics.avg_win for w in has_metrics])),  # type: ignore[union-attr]
            avg_loss=float(np.mean([w.test_metrics.avg_loss for w in has_metrics])),  # type: ignore[union-attr]
            largest_win=float(np.max([w.test_metrics.largest_win for w in has_metrics])),  # type: ignore[union-attr]
            largest_loss=float(np.min([w.test_metrics.largest_loss for w in has_metrics])),  # type: ignore[union-attr]
            max_win_streak=int(np.max([w.test_metrics.max_win_streak for w in has_metrics])),  # type: ignore[union-attr]
            max_loss_streak=int(np.max([w.test_metrics.max_loss_streak for w in has_metrics])),  # type: ignore[union-attr]
            avg_holding_bars=float(np.mean([w.test_metrics.avg_holding_bars for w in has_metrics])),  # type: ignore[union-attr]
            total_fees=float(np.sum([w.test_metrics.total_fees for w in has_metrics])),  # type: ignore[union-attr]
            total_slippage=float(np.sum([w.test_metrics.total_slippage for w in has_metrics])),  # type: ignore[union-attr]
        )

    return _compute_metrics_from_trades(all_trades)
