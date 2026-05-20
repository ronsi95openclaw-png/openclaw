"""Monte Carlo engine: orchestrates simulation and stress testing."""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Dict, List

from research.types import BacktestResult, BacktestTrade, MonteCarloResult
from research.montecarlo.confidence_intervals import compute_monte_carlo_result

logger = logging.getLogger(__name__)


class MonteCarloEngine:
    """Orchestrates Monte Carlo simulation and reporting.

    Args:
        n_simulations: Number of simulation paths.
        confidence:    Confidence level for CI calculations (default 0.95).
        ruin_threshold: Drawdown fraction that constitutes ruin (default 0.5).
        seed:          Random seed for reproducibility.
    """

    def __init__(
        self,
        n_simulations: int = 10_000,
        confidence: float = 0.95,
        ruin_threshold: float = 0.5,
        seed: int = 42,
    ) -> None:
        self.n_simulations = n_simulations
        self.confidence = confidence
        self.ruin_threshold = ruin_threshold
        self.seed = seed

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, result: BacktestResult) -> MonteCarloResult:
        """Run full Monte Carlo analysis on a BacktestResult.

        Args:
            result: Completed backtest with trade list and capital info.

        Returns:
            Populated ``MonteCarloResult``.
        """
        logger.info(
            "MonteCarloEngine: running %d simulations on %s/%s (%d trades)",
            self.n_simulations,
            result.strategy,
            result.symbol,
            len(result.trades),
        )
        return compute_monte_carlo_result(
            trades=result.trades,
            initial_capital=result.initial_capital,
            n_simulations=self.n_simulations,
            confidence=self.confidence,
            ruin_threshold=self.ruin_threshold,
            seed=self.seed,
        )

    def run_stress_test(
        self,
        result: BacktestResult,
        fee_multipliers: List[float] = None,
        slippage_multipliers: List[float] = None,
    ) -> Dict[str, MonteCarloResult]:
        """Run simulations under different fee/slippage scenarios.

        Creates modified trade lists where fee and slippage costs are scaled by
        the given multipliers.  Each combination is simulated independently.

        Args:
            result:               Base backtest result.
            fee_multipliers:      Factors to multiply fees by (default [1, 2, 3]).
            slippage_multipliers: Factors to multiply slippage by (default [1, 2, 5]).

        Returns:
            Dict mapping ``"fee{F}x_slip{S}x"`` → ``MonteCarloResult``.
        """
        if fee_multipliers is None:
            fee_multipliers = [1.0, 2.0, 3.0]
        if slippage_multipliers is None:
            slippage_multipliers = [1.0, 2.0, 5.0]

        scenarios: Dict[str, MonteCarloResult] = {}

        for fee_mult in fee_multipliers:
            for slip_mult in slippage_multipliers:
                label = f"fee{fee_mult:.1f}x_slip{slip_mult:.1f}x"
                logger.debug("MonteCarloEngine stress: scenario %s", label)
                modified_trades = _apply_stress(
                    result.trades, fee_mult, slip_mult
                )
                mc_result = compute_monte_carlo_result(
                    trades=modified_trades,
                    initial_capital=result.initial_capital,
                    n_simulations=self.n_simulations,
                    confidence=self.confidence,
                    ruin_threshold=self.ruin_threshold,
                    seed=self.seed,
                )
                scenarios[label] = mc_result

        return scenarios


# ── Helpers ───────────────────────────────────────────────────────────────────

def _apply_stress(
    trades: List[BacktestTrade],
    fee_mult: float,
    slip_mult: float,
) -> List[BacktestTrade]:
    """Return copies of trades with scaled fees and slippage.

    Adjusts ``net_pnl``, ``net_pnl_pct``, ``fees``, ``entry_slippage``,
    and ``exit_slippage`` proportionally.

    Args:
        trades:    Original trade list.
        fee_mult:  Fee multiplier (1.0 = baseline).
        slip_mult: Slippage multiplier (1.0 = baseline).

    Returns:
        New list of BacktestTrade objects with modified cost fields.
    """
    stressed: List[BacktestTrade] = []
    for t in trades:
        extra_fees = t.fees * (fee_mult - 1.0)
        extra_slip = (t.entry_slippage + t.exit_slippage) * (slip_mult - 1.0)
        extra_cost = extra_fees + extra_slip

        new_net_pnl = t.net_pnl - extra_cost
        # Re-derive net_pnl_pct from gross_pnl and new costs
        notional = abs(t.entry_price * t.size) if t.entry_price * t.size != 0 else 1.0
        new_net_pnl_pct = (new_net_pnl / notional) * 100.0 if notional > 0 else 0.0

        stressed.append(
            replace(
                t,
                fees=t.fees * fee_mult,
                entry_slippage=t.entry_slippage * slip_mult,
                exit_slippage=t.exit_slippage * slip_mult,
                net_pnl=new_net_pnl,
                net_pnl_pct=new_net_pnl_pct,
            )
        )
    return stressed
