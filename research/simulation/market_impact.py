"""Almgren-Chriss inspired temporary + permanent market impact."""
from __future__ import annotations


class MarketImpactModel:
    """Almgren-Chriss inspired temporary + permanent market impact.

    Temporary impact models the instantaneous price move caused by
    executing a trade, which reverts after the trade completes.

    Permanent impact models the lasting shift in the fair-value price
    caused by information leakage / supply-demand changes.
    """

    def __init__(
        self,
        temp_impact_coeff: float = 0.1,
        perm_impact_coeff: float = 0.01,
    ) -> None:
        self.temp_impact_coeff = temp_impact_coeff
        self.perm_impact_coeff = perm_impact_coeff

    def temporary_impact_bps(self, size_usd: float, adv_usd: float) -> float:
        """Return temporary market impact in basis-points.

        Formula::

            temp_impact_coeff × (size_usd / adv_usd)^0.6 × 10_000
        """
        adv = max(adv_usd, 1.0)
        return self.temp_impact_coeff * (size_usd / adv) ** 0.6 * 10_000

    def permanent_impact_bps(self, size_usd: float, adv_usd: float) -> float:
        """Return permanent market impact in basis-points.

        Formula::

            perm_impact_coeff × (size_usd / adv_usd) × 10_000
        """
        adv = max(adv_usd, 1.0)
        return self.perm_impact_coeff * (size_usd / adv) * 10_000

    def total_cost_bps(self, size_usd: float, adv_usd: float) -> float:
        """Return combined temporary + permanent market impact in bps."""
        return self.temporary_impact_bps(size_usd, adv_usd) + self.permanent_impact_bps(size_usd, adv_usd)
