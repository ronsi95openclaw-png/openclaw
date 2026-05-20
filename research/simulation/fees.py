"""BloFin-accurate fee simulation."""
from __future__ import annotations

import math


class FeeModel:
    """BloFin-accurate fee simulation.

    BloFin fee tiers (from docs):
        Maker fee: 0.02 % (0.0002)
        Taker fee: 0.05 % (0.0005)
    """

    MAKER_FEE: float = 0.0002  # 0.02 %
    TAKER_FEE: float = 0.0005  # 0.05 %

    def __init__(
        self,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0005,
        funding_rate_8h: float = 0.0001,
    ) -> None:
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.funding_rate_8h = funding_rate_8h

    def compute_fee(self, notional: float, is_maker: bool = False) -> float:
        """Return the fee charged on *notional* for a maker or taker order."""
        rate = self.maker_fee if is_maker else self.taker_fee
        return notional * rate

    def compute_funding(
        self,
        notional: float,
        holding_bars: int,
        bars_per_8h: int = 32,
    ) -> float:
        """Return total funding paid for a position.

        Funding is assessed every 8-hour interval.

        Formula::

            funding = funding_rate_8h × floor(holding_bars / bars_per_8h) × notional
        """
        periods = math.floor(holding_bars / bars_per_8h)
        return self.funding_rate_8h * periods * notional
