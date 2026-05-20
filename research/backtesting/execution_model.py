"""Unified execution pipeline combining fill, slippage, fee, and funding models.

ExecutionModel is the single entry-point used by BacktestEngine for all
order-related calculations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from research.types import Candle, Signal
from research.backtesting.slippage import SlippageModel, FixedBpsSlippage
from research.backtesting.fees import FeeModel, BloFinFeeModel
from research.backtesting.funding import FundingModel, FixedFundingModel
from research.backtesting.fills import FillSimulator


# Imported here to avoid circular imports – engine.py defines these dataclasses
# but execution_model.py is imported by engine.py, so we use a TYPE_CHECKING guard.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from research.backtesting.engine import OpenPosition


@dataclass
class _OpenPosition:
    """Local mirror used inside execution_model to avoid circular import at runtime."""
    symbol: str
    side: str
    entry_price: float
    size: float
    entry_bar: int
    sl_price: float
    tp_price: float
    max_adverse_price: float
    max_favorable_price: float
    funding_paid: float = 0.0


class ExecutionModel:
    """Combines fill, slippage, fee, and funding models into a single pipeline.

    Parameters
    ----------
    slippage:      SlippageModel to apply on fills
    fees:          FeeModel for entry/exit commissions
    funding:       FundingModel for periodic funding payments
    latency_bars:  number of bars before a fill executes (0 = same bar, 1 = next bar open)
    """

    def __init__(
        self,
        slippage: SlippageModel,
        fees: FeeModel,
        funding: FundingModel,
        latency_bars: int = 1,
    ) -> None:
        self.slippage = slippage
        self.fees = fees
        self.funding = funding
        self.latency_bars = latency_bars
        self._filler = FillSimulator()

    def execute_entry(
        self,
        signal: Signal,
        entry_candle: Candle,
        capital: float,
        risk_pct: float,
        leverage: int,
    ) -> Tuple[_OpenPosition, float]:  # (position, fee_paid)
        """Open a position based on signal, sizing by risk percentage.

        Position size = (capital * risk_pct) / (entry_price * sl_pct)
        Notional = size * entry_price * leverage

        Returns
        -------
        Tuple of (OpenPosition-like dataclass, fee_paid_usd)
        """
        order_side = "buy" if signal.action == "long" else "sell"
        fill = self._filler.simulate_market_fill(
            order_side, 1.0, entry_candle, self.slippage
        )
        entry_price = fill.fill_price

        # Position sizing: risk a fixed % of capital
        sl_pct = max(signal.sl_pct, 0.01) / 100.0
        tp_pct = max(signal.tp_pct, 0.01) / 100.0
        risk_usd = capital * (risk_pct / 100.0)
        sl_usd_per_unit = entry_price * sl_pct
        if sl_usd_per_unit <= 0:
            size = 0.001
        else:
            size = risk_usd / sl_usd_per_unit
        size = max(size, 0.0001)

        if signal.action == "long":
            sl_price = entry_price * (1.0 - sl_pct)
            tp_price = entry_price * (1.0 + tp_pct)
        else:
            sl_price = entry_price * (1.0 + sl_pct)
            tp_price = entry_price * (1.0 - tp_pct)

        notional = size * entry_price * leverage
        fee = self.fees.compute_taker(notional)

        pos = _OpenPosition(
            symbol=signal.symbol,
            side=signal.action,
            entry_price=entry_price,
            size=size,
            entry_bar=0,  # caller must set the bar index
            sl_price=sl_price,
            tp_price=tp_price,
            max_adverse_price=entry_price,
            max_favorable_price=entry_price,
            funding_paid=0.0,
        )

        return pos, fee

    def execute_exit(
        self,
        position: _OpenPosition,
        exit_candle: Candle,
        leverage: int = 1,
    ) -> Tuple[float, float, float]:  # (exit_price, gross_pnl, fee_paid)
        """Close a position at the exit candle open with slippage.

        Returns
        -------
        Tuple of (exit_price, gross_pnl_usd, fee_paid_usd)
        """
        order_side = "sell" if position.side == "long" else "buy"
        fill = self._filler.simulate_market_fill(
            order_side, position.size, exit_candle, self.slippage
        )
        exit_price = fill.fill_price

        if position.side == "long":
            gross_pnl = (exit_price - position.entry_price) * position.size * leverage
        else:
            gross_pnl = (position.entry_price - exit_price) * position.size * leverage

        notional = position.size * exit_price * leverage
        fee = self.fees.compute_taker(notional)

        return exit_price, gross_pnl, fee

    def should_hit_sl(self, pos: _OpenPosition, candle: Candle) -> bool:
        """Return True if the candle's price range crosses the stop-loss level."""
        if pos.side == "long":
            return candle.low <= pos.sl_price
        else:
            return candle.high >= pos.sl_price

    def should_hit_tp(self, pos: _OpenPosition, candle: Candle) -> bool:
        """Return True if the candle's price range crosses the take-profit level."""
        if pos.side == "long":
            return candle.high >= pos.tp_price
        else:
            return candle.low <= pos.tp_price

    def compute_funding_payment(
        self, pos: _OpenPosition, candle: Candle, leverage: int = 1
    ) -> float:
        """Return the funding payment for this position on this bar (USD).

        The funding rate is looked up from the model; for FixedFundingModel
        the rate is always the configured constant.  For HistoricalFundingModel
        the rate is retrieved by candle timestamp.
        """
        from research.backtesting.funding import HistoricalFundingModel
        notional = pos.size * pos.entry_price * leverage
        if isinstance(self.funding, HistoricalFundingModel):
            rate = self.funding.get_rate(candle.ts)
        else:
            rate = getattr(self.funding, "rate", 0.0001)
        return self.funding.compute_payment(notional, pos.side, rate)
