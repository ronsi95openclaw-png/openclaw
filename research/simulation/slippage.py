"""Realistic slippage curves based on order size vs available liquidity."""
from __future__ import annotations

import math

from research.simulation.config import SimulationMode
from research.types import Candle


class SlippageModel:
    """Realistic slippage curves based on order size vs available liquidity.

    Formula:
        slippage_bps = base_bps + impact_coeff * sqrt(size_usd / adv_usd) * 10_000

    In STRESS mode the result is multiplied by 3×.
    In ZERO mode returns 0 always.
    """

    def __init__(
        self,
        base_bps: float = 3.0,
        impact_coeff: float = 0.1,
        mode: SimulationMode = SimulationMode.REALISTIC,
    ) -> None:
        self.base_bps = base_bps
        self.impact_coeff = impact_coeff
        self.mode = mode

    def compute_slippage_bps(
        self,
        size_usd: float,
        avg_daily_volume_usd: float,
        side: str,
        candle: Candle,
    ) -> float:
        """Return slippage in basis-points for the given order.

        Parameters
        ----------
        size_usd:
            Notional value of the order in USD.
        avg_daily_volume_usd:
            Average daily traded volume for the instrument in USD.
        side:
            ``"buy"`` or ``"sell"``.
        candle:
            The bar on which the order executes (used for future extensions).
        """
        if self.mode == SimulationMode.ZERO:
            return 0.0

        adv = max(avg_daily_volume_usd, 1.0)
        participation = size_usd / adv
        slippage = self.base_bps + self.impact_coeff * math.sqrt(participation) * 10_000

        if self.mode == SimulationMode.STRESS:
            slippage *= 3.0

        return slippage

    def apply_to_price(self, price: float, slippage_bps: float, side: str) -> float:
        """Adjust *price* by *slippage_bps* in the direction adverse to the trader.

        Buy orders receive a higher fill price; sell orders receive a lower
        fill price.
        """
        factor = slippage_bps / 10_000.0
        if side == "buy":
            return price * (1.0 + factor)
        # sell
        return price * (1.0 - factor)
