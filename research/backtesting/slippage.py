"""Slippage models for the backtesting engine.

Three models are provided:
    ZeroSlippage          – best-case, no slippage
    FixedBpsSlippage      – constant basis-point cost
    VolumeImpactSlippage  – square-root market-impact model
"""
from __future__ import annotations

import math


class SlippageModel:
    """Base class — subclass and override ``compute``."""

    def compute(self, side: str, price: float, size: float, volume: float) -> float:
        """Return slippage in price units (always positive).

        Parameters
        ----------
        side:   ``"buy"`` or ``"sell"``
        price:  current reference price
        size:   order size (in contracts / units)
        volume: bar volume (same units as *size*)
        """
        return 0.0


class ZeroSlippage(SlippageModel):
    """No slippage — best-case / fee-only scenario."""

    def compute(self, side: str, price: float, size: float, volume: float) -> float:
        return 0.0


class FixedBpsSlippage(SlippageModel):
    """Fixed basis-point slippage applied symmetrically on both sides.

    Parameters
    ----------
    bps:  basis points of slippage (default 5 bps = 0.05%)
    """

    def __init__(self, bps: float = 5.0) -> None:
        self.bps = bps

    def compute(self, side: str, price: float, size: float, volume: float) -> float:
        """Return slippage in price units."""
        return price * (self.bps / 10_000.0)


class VolumeImpactSlippage(SlippageModel):
    """Square-root market-impact model.

    impact = base_slippage + k * sigma * sqrt(participation_rate)

    where:
        participation_rate = size / volume
        sigma is approximated as base_bps (normalised price volatility proxy)

    Parameters
    ----------
    k:        market-impact coefficient (default 0.1)
    base_bps: base slippage in bps added to impact (default 2.0)
    """

    def __init__(self, k: float = 0.1, base_bps: float = 2.0) -> None:
        self.k = k
        self.base_bps = base_bps

    def compute(self, side: str, price: float, size: float, volume: float) -> float:
        """Return slippage in price units."""
        base = price * (self.base_bps / 10_000.0)
        if volume <= 0 or size <= 0:
            return base
        participation = size / volume
        sigma = self.base_bps / 10_000.0   # use base_bps as normalised vol proxy
        impact = self.k * sigma * math.sqrt(participation)
        return base + price * impact
