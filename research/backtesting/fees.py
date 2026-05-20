"""Fee models for the backtesting engine.

Two concrete models:
    ZeroFeeModel    – no fees (ideal-case testing)
    BloFinFeeModel  – BloFin perpetual futures schedule
"""
from __future__ import annotations


class FeeModel:
    """Base class for exchange fee models."""

    def compute_maker(self, notional: float) -> float:
        """Return maker fee in USD for a given notional value."""
        return 0.0

    def compute_taker(self, notional: float) -> float:
        """Return taker fee in USD for a given notional value."""
        return 0.0


class ZeroFeeModel(FeeModel):
    """No fees — useful for isolating pure strategy performance."""

    def compute_maker(self, notional: float) -> float:
        return 0.0

    def compute_taker(self, notional: float) -> float:
        return 0.0


class BloFinFeeModel(FeeModel):
    """BloFin perpetual futures fee schedule.

    Defaults: maker 0.02% (2 bps), taker 0.06% (6 bps).
    Market orders use taker rate; limit orders use maker rate.

    Parameters
    ----------
    maker_rate:  fraction charged as maker fee (default 0.0002)
    taker_rate:  fraction charged as taker fee (default 0.0006)
    """

    def __init__(
        self,
        maker_rate: float = 0.0002,
        taker_rate: float = 0.0006,
    ) -> None:
        self.maker_rate = maker_rate
        self.taker_rate = taker_rate

    def compute_maker(self, notional: float) -> float:
        """Return maker fee in USD."""
        return abs(notional) * self.maker_rate

    def compute_taker(self, notional: float) -> float:
        """Return taker fee in USD."""
        return abs(notional) * self.taker_rate
