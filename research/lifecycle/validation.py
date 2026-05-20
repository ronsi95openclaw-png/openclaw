"""Paper trading and probation result validation."""
from __future__ import annotations

from typing import List, Tuple

from research.types import BacktestResult, BacktestTrade, PerformanceMetrics


class LifecycleValidator:
    """Validates paper trading and probation results before promotion.

    Checks are designed to catch common issues that would indicate a strategy
    should not advance to the next lifecycle stage.
    """

    def __init__(
        self,
        min_trades: int = 100,
        min_sharpe: float = 0.5,
        max_drawdown_pct: float = 25.0,
        min_win_rate: float = 0.40,
        max_slippage_multiplier: float = 2.0,
        min_win_rate_vs_paper: float = 0.70,
        min_sharpe_vs_paper: float = 0.50,
    ) -> None:
        self.min_trades = min_trades
        self.min_sharpe = min_sharpe
        self.max_drawdown_pct = max_drawdown_pct
        self.min_win_rate = min_win_rate
        self.max_slippage_multiplier = max_slippage_multiplier
        self.min_win_rate_vs_paper = min_win_rate_vs_paper
        self.min_sharpe_vs_paper = min_sharpe_vs_paper

    def validate_paper_trading_results(
        self,
        strategy: str,
        results: List[BacktestResult],
    ) -> Tuple[bool, List[str]]:
        """Validate paper trading results prior to PROBATION nomination.

        Parameters
        ----------
        strategy:
            Strategy identifier (informational).
        results:
            One or more backtest results from the paper trading period.

        Returns
        -------
        (passed, list_of_issues)
            *passed* is ``True`` only when the issue list is empty.
        """
        issues: list[str] = []

        if not results:
            issues.append("no paper trading results provided")
            return False, issues

        all_trades = [t for r in results for t in r.trades]
        total_trades = len(all_trades)
        if total_trades < self.min_trades:
            issues.append(
                f"insufficient trades: {total_trades} < {self.min_trades}"
            )

        # Basic data-snooping artifact check: all-winning or extremely high win rate
        if total_trades > 0:
            winners = sum(1 for t in all_trades if t.net_pnl > 0)
            win_rate = winners / total_trades
            if win_rate > 0.95:
                issues.append(
                    f"suspiciously high win_rate {win_rate:.2%} — possible "
                    "data-snooping artifact"
                )
            if win_rate < self.min_win_rate:
                issues.append(
                    f"win_rate {win_rate:.2%} below minimum {self.min_win_rate:.2%}"
                )

        # Check equity curves for non-monotone growth (look-ahead bias signal)
        for r in results:
            if len(r.equity_curve) >= 2:
                diffs = [
                    r.equity_curve[i] - r.equity_curve[i - 1]
                    for i in range(1, len(r.equity_curve))
                ]
                if all(d >= 0 for d in diffs):
                    issues.append(
                        f"equity curve for result starting {r.start_time} "
                        "is monotonically non-decreasing — possible look-ahead bias"
                    )

        # Gross drawdown check from equity curves
        for r in results:
            if r.equity_curve:
                peak = r.equity_curve[0]
                for v in r.equity_curve:
                    if v > peak:
                        peak = v
                    if peak > 0:
                        dd_pct = (peak - v) / peak * 100.0
                        if dd_pct > self.max_drawdown_pct:
                            issues.append(
                                f"max drawdown {dd_pct:.1f}% exceeds "
                                f"{self.max_drawdown_pct}%"
                            )
                            break

        passed = len(issues) == 0
        return passed, issues

    def validate_probation_results(
        self,
        live_trades: List[BacktestTrade],
        paper_baseline: PerformanceMetrics,
    ) -> Tuple[bool, List[str]]:
        """Compare live probation trades against the paper trading baseline.

        Flags issues that suggest live performance is materially worse than
        what was observed during paper trading.

        Parameters
        ----------
        live_trades:
            Trades executed during the probation period.
        paper_baseline:
            Performance metrics from the paper trading phase.

        Returns
        -------
        (passed, list_of_issues)
        """
        issues: list[str] = []

        if not live_trades:
            issues.append("no live probation trades provided")
            return False, issues

        # Compute live stats
        total = len(live_trades)
        winners = sum(1 for t in live_trades if t.net_pnl > 0)
        live_win_rate = winners / total

        total_entry_slip = sum(t.entry_slippage for t in live_trades)
        total_exit_slip = sum(t.exit_slippage for t in live_trades)
        total_slip = total_entry_slip + total_exit_slip
        avg_slip_per_trade = total_slip / total if total > 0 else 0.0

        # Expected slippage from baseline (total_slippage / total_trades)
        if paper_baseline.total_trades > 0:
            expected_slip = (
                paper_baseline.total_slippage / paper_baseline.total_trades
            )
        else:
            expected_slip = 0.0

        if expected_slip > 0 and avg_slip_per_trade > expected_slip * self.max_slippage_multiplier:
            issues.append(
                f"avg slippage per trade ${avg_slip_per_trade:.4f} is "
                f"{avg_slip_per_trade / expected_slip:.1f}× paper expected "
                f"${expected_slip:.4f} (threshold: {self.max_slippage_multiplier}×)"
            )

        # Win-rate < paper × 0.7
        min_live_win_rate = paper_baseline.win_rate * self.min_win_rate_vs_paper
        if live_win_rate < min_live_win_rate:
            issues.append(
                f"live win_rate {live_win_rate:.2%} < "
                f"{self.min_win_rate_vs_paper:.0%}× paper "
                f"({min_live_win_rate:.2%})"
            )

        # Live Sharpe estimation: approximate from returns
        pnls = [t.net_pnl for t in live_trades]
        if len(pnls) >= 2:
            import statistics
            mean_pnl = statistics.mean(pnls)
            std_pnl = statistics.stdev(pnls)
            live_sharpe = (mean_pnl / std_pnl * (len(pnls) ** 0.5)) if std_pnl > 0 else 0.0
        else:
            live_sharpe = 0.0

        min_live_sharpe = paper_baseline.sharpe_ratio * self.min_sharpe_vs_paper
        if live_sharpe < min_live_sharpe:
            issues.append(
                f"estimated live Sharpe {live_sharpe:.3f} < "
                f"{self.min_sharpe_vs_paper:.0%}× paper "
                f"({min_live_sharpe:.3f})"
            )

        passed = len(issues) == 0
        return passed, issues
