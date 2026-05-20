"""Simulates partial fills based on liquidity availability."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FillResult:
    """Result of a simulated fill attempt."""
    filled_size: float
    fill_price: float
    remaining_size: float
    is_partial: bool


class PartialFillSimulator:
    """Simulates partial fills based on liquidity availability.

    If the available liquidity fraction is below *min_fill_fraction* the
    entire order is treated as un-fillable and ``filled_size`` will be 0.
    """

    def __init__(self, min_fill_fraction: float = 0.3) -> None:
        self.min_fill_fraction = min_fill_fraction

    def simulate_fill(
        self,
        requested_size: float,
        available_fraction: float,
        price: float,
        side: str,
    ) -> FillResult:
        """Simulate a fill for *requested_size* units at *price*.

        Parameters
        ----------
        requested_size:
            The number of units (contracts / base currency) requested.
        available_fraction:
            A value in ``[0, 1]`` representing how much of the order the
            market can absorb this bar (from :meth:`LiquidityModel.fill_fraction`).
        price:
            The fill price (post-slippage).
        side:
            ``"buy"`` or ``"sell"`` — reserved for future asymmetric logic.
        """
        available_fraction = min(max(available_fraction, 0.0), 1.0)

        if available_fraction < self.min_fill_fraction:
            return FillResult(
                filled_size=0.0,
                fill_price=price,
                remaining_size=requested_size,
                is_partial=True,
            )

        filled = requested_size * available_fraction
        remaining = requested_size - filled
        is_partial = remaining > 0.0

        return FillResult(
            filled_size=filled,
            fill_price=price,
            remaining_size=remaining,
            is_partial=is_partial,
        )
