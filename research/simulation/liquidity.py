"""Simulates bid-ask spread and available depth."""
from __future__ import annotations

from research.types import Candle

_REGIME_MULTIPLIERS: dict[str, float] = {
    "PANIC": 5.0,
    "VOL_EXPANSION": 2.0,
    "RANGING": 0.8,
}


class LiquidityModel:
    """Simulates bid-ask spread and available depth.

    Spread is computed as ``base_spread_bps × regime_multiplier``.
    Depth is estimated as ``candle.volume × mid_price × depth_scalar``.
    """

    def __init__(
        self,
        base_spread_bps: float = 2.0,
        depth_scalar: float = 1.0,
    ) -> None:
        self.base_spread_bps = base_spread_bps
        self.depth_scalar = depth_scalar

    def estimate_spread_bps(
        self,
        candle: Candle,
        regime_label: str = "UNKNOWN",
    ) -> float:
        """Return estimated spread in basis-points for this bar.

        Regime multipliers:
            PANIC           → 5×
            VOL_EXPANSION   → 2×
            RANGING         → 0.8×
            (any other)     → 1×
        """
        multiplier = _REGIME_MULTIPLIERS.get(regime_label.upper(), 1.0)
        return self.base_spread_bps * multiplier

    def _estimated_depth_usd(self, candle: Candle) -> float:
        mid = (candle.high + candle.low) / 2.0
        return candle.volume * mid * self.depth_scalar

    def can_fill(self, size_usd: float, candle: Candle) -> bool:
        """Return *True* if *size_usd* is below estimated available depth."""
        return size_usd < self._estimated_depth_usd(candle)

    def fill_fraction(self, size_usd: float, candle: Candle) -> float:
        """Return the fraction (0.0–1.0) of *size_usd* that can be filled."""
        depth = self._estimated_depth_usd(candle)
        if depth <= 0.0:
            return 0.0
        fraction = depth / size_usd
        return min(max(fraction, 0.0), 1.0)
