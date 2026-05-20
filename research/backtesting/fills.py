"""Order fill simulation for the backtesting engine.

FillSimulator provides market-fill and partial-fill logic with realistic
slippage applied through a pluggable SlippageModel.
"""
from __future__ import annotations

from dataclasses import dataclass

from research.types import Candle
from research.backtesting.slippage import SlippageModel


@dataclass
class FillResult:
    """Result of a single order fill attempt."""

    filled_size:  float   # units actually filled
    fill_price:   float   # weighted average fill price
    slippage_bps: float   # realised slippage in basis points
    partial:      bool    # True if only partially filled


class FillSimulator:
    """Simulates order fills with realistic partial-fill logic.

    All fills are evaluated on the *entry_candle* (the bar at which the
    fill is executed – typically the bar *after* the signal bar, respecting
    ``latency_bars``).
    """

    def simulate_market_fill(
        self,
        order_side: str,
        size: float,
        candle: Candle,
        slippage_model: SlippageModel,
    ) -> FillResult:
        """Fill a market order at the candle open with slippage applied.

        Parameters
        ----------
        order_side:      ``"buy"`` or ``"sell"``
        size:            desired fill size
        candle:          bar on which the fill executes (typically next-bar)
        slippage_model:  SlippageModel instance for cost calculation
        """
        base_price = candle.open
        slip = slippage_model.compute(
            order_side, base_price, size, max(candle.volume, 1.0)
        )
        # Slippage always works against the trader
        if order_side == "buy":
            fill_price = base_price + slip
        else:
            fill_price = base_price - slip

        # Clamp to candle range for realism
        fill_price = max(candle.low, min(candle.high, fill_price))

        slippage_bps = (abs(fill_price - base_price) / base_price) * 10_000 if base_price > 0 else 0.0

        return FillResult(
            filled_size=size,
            fill_price=fill_price,
            slippage_bps=slippage_bps,
            partial=False,
        )

    def simulate_partial_fill(
        self,
        size: float,
        candle: Candle,
        fill_rate: float = 0.8,
    ) -> FillResult:
        """Simulate a partial fill scenario (e.g. thin order book).

        Parameters
        ----------
        size:      desired order size
        candle:    bar on which fill executes
        fill_rate: fraction of ``size`` actually filled (default 0.8)
        """
        fill_rate = max(0.0, min(1.0, fill_rate))
        filled = size * fill_rate
        fill_price = candle.open  # no additional slippage for partial fills

        return FillResult(
            filled_size=filled,
            fill_price=fill_price,
            slippage_bps=0.0,
            partial=filled < size,
        )
