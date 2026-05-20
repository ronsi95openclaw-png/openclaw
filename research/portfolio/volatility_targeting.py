"""Dynamic position sizing for a target portfolio volatility.

Class:
  VolatilityTargeter — scales position size so realized vol ≈ target vol.
"""
from __future__ import annotations

import math
from typing import List

from research.types import Candle
from research.regimes.volatility import historical_volatility


class VolatilityTargeter:
    """Dynamically scales position size to maintain a target portfolio volatility.

    Core idea: if realized vol is 2× target → halve position size.
               if realized vol is 0.5× target → double it (up to leverage cap).

    Usage::

        vt = VolatilityTargeter(target_vol_annual=0.20, max_leverage=5.0)
        scalar = vt.compute_size_scalar(realized_vol=0.40)  # → 0.5
        size   = vt.target_position_size(base_risk_usd=100, candles=candles)
    """

    def __init__(
        self,
        target_vol_annual: float = 0.20,     # 20 % annualised
        max_leverage: float = 5.0,
        min_size_scalar: float = 0.1,
        vol_lookback: int = 20,
        bars_per_year: float = 365 * 24 * 4,  # 15-minute bars
    ) -> None:
        if target_vol_annual <= 0:
            raise ValueError("target_vol_annual must be > 0")
        if max_leverage < 1.0:
            raise ValueError("max_leverage must be >= 1.0")

        self.target_vol_annual = target_vol_annual
        self.max_leverage      = max_leverage
        self.min_size_scalar   = min_size_scalar
        self.vol_lookback      = vol_lookback
        self.bars_per_year     = bars_per_year

    # ── public API ────────────────────────────────────────────────────────────

    def compute_size_scalar(self, realized_vol: float) -> float:
        """Returns a multiplier in [min_size_scalar, max_leverage].

        scalar = target_vol / realized_vol  (clamped).

        Parameters
        ----------
        realized_vol:
            Annualised realized volatility.

        Returns ``max_leverage`` when realized_vol is zero or very small.
        """
        if realized_vol <= 0:
            return self.max_leverage

        scalar = self.target_vol_annual / realized_vol
        return max(self.min_size_scalar, min(self.max_leverage, scalar))

    def compute_realized_vol(self, candles: List[Candle]) -> float:
        """Compute annualised realized vol from the most recent candles.

        Uses log-return standard deviation over ``vol_lookback`` bars.

        Returns 0.0 when insufficient data.
        """
        closes = [c.close for c in candles]
        return historical_volatility(
            closes,
            window=self.vol_lookback,
            annualized=True,
            bars_per_year=self.bars_per_year,
        )

    def target_position_size(
        self,
        base_risk_usd: float,
        candles: List[Candle],
    ) -> float:
        """Returns the vol-adjusted position size in USD.

        Computes the scalar from recent candle data, then applies it to
        ``base_risk_usd``.

        Parameters
        ----------
        base_risk_usd:
            Base position size before vol-adjustment.
        candles:
            Recent candles for realized vol estimation.

        Returns
        -------
        float
            Adjusted position size in USD.
        """
        realized_vol = self.compute_realized_vol(candles)
        scalar       = self.compute_size_scalar(realized_vol)
        return base_risk_usd * scalar
