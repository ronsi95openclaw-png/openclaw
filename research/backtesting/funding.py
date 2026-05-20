"""Funding rate models for perpetual futures backtesting.

Two concrete models:
    FixedFundingModel       – constant rate every 8-hour period
    HistoricalFundingModel  – per-timestamp rates from historical data
"""
from __future__ import annotations

from typing import Dict


class FundingModel:
    """Base class for perpetual futures funding payment models."""

    def compute_payment(
        self,
        position_notional: float,
        side: str,
        funding_rate: float,
    ) -> float:
        """Return funding payment in USD.

        Positive value means the position holder *pays* funding.
        Negative value means the position holder *receives* funding.

        Convention (same as most CEXes):
            long  position pays  when funding_rate > 0
            short position pays  when funding_rate < 0

        Parameters
        ----------
        position_notional: abs(size * price)
        side:              ``"long"`` or ``"short"``
        funding_rate:      per-period rate, e.g. 0.0001 = 0.01 %
        """
        return 0.0


class FixedFundingModel(FundingModel):
    """Constant funding rate applied at every funding interval.

    Parameters
    ----------
    rate:  per-period (8 h) funding rate, default 0.0001 (0.01 %)
    """

    def __init__(self, rate: float = 0.0001) -> None:
        self.rate = rate

    def compute_payment(
        self,
        position_notional: float,
        side: str,
        funding_rate: float,
    ) -> float:
        """Return funding payment using the fixed rate (ignores ``funding_rate`` arg)."""
        effective_rate = self.rate
        if side == "long":
            return abs(position_notional) * effective_rate
        else:
            return -abs(position_notional) * effective_rate


class HistoricalFundingModel(FundingModel):
    """Uses per-bar funding rates from historical data.

    Parameters
    ----------
    rates:  mapping of Unix-ms timestamp → funding rate for that bar.
            Falls back to 0.0 when a timestamp is missing.
    """

    def __init__(self, rates: Dict[int, float]) -> None:
        self.rates = rates

    def get_rate(self, ts: int) -> float:
        """Return the funding rate for ``ts``, or 0.0 if not found."""
        return self.rates.get(ts, 0.0)

    def compute_payment(
        self,
        position_notional: float,
        side: str,
        funding_rate: float,
    ) -> float:
        """Return funding payment using the provided ``funding_rate``."""
        if side == "long":
            return abs(position_notional) * funding_rate
        else:
            return -abs(position_notional) * funding_rate
