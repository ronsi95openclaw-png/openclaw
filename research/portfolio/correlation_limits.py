"""Correlation-based diversification enforcement.

Class:
  CorrelationLimiter — blocks or scales positions that are too correlated
                       with existing portfolio holdings.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from research.types import Candle


def _log_returns(candles: List[Candle]) -> List[float]:
    """Compute log returns from candle closes."""
    closes = [c.close for c in candles]
    rets: List[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
        else:
            rets.append(0.0)
    return rets


def _pearson(a: List[float], b: List[float]) -> float:
    """Pearson correlation coefficient of two equal-length series.

    Returns 0.0 when the series are empty or have zero variance.
    """
    n = min(len(a), len(b))
    if n < 2:
        return 0.0

    a = a[-n:]
    b = b[-n:]

    mean_a = sum(a) / n
    mean_b = sum(b) / n

    cov   = sum((ai - mean_a) * (bi - mean_b) for ai, bi in zip(a, b)) / n
    var_a = sum((ai - mean_a) ** 2 for ai in a)               / n
    var_b = sum((bi - mean_b) ** 2 for bi in b)               / n

    denom = math.sqrt(var_a * var_b)
    if denom < 1e-12:
        return 0.0
    return cov / denom


class CorrelationLimiter:
    """Prevents adding positions that are too highly correlated with existing ones.

    Core rule: if the new position would have |correlation| > threshold with
    any existing position, block or scale it down.

    Usage::

        limiter = CorrelationLimiter(max_pairwise_correlation=0.75)
        allowed, max_corr = limiter.check_correlation(
            "SOL-USDT", sol_candles,
            existing_positions=[{"symbol": "BTC-USDT"}],
            all_candles={"BTC-USDT": btc_candles, "SOL-USDT": sol_candles},
        )
    """

    def __init__(
        self,
        max_pairwise_correlation: float = 0.75,
        lookback_bars: int = 50,
        block_on_exceed: bool = True,
    ) -> None:
        self.max_pairwise_correlation = max_pairwise_correlation
        self.lookback_bars            = lookback_bars
        self.block_on_exceed          = block_on_exceed

    # ── public API ────────────────────────────────────────────────────────────

    def check_correlation(
        self,
        new_symbol: str,
        new_candles: List[Candle],
        existing_positions: List[Dict[str, Any]],
        all_candles: Dict[str, List[Candle]],
    ) -> Tuple[bool, float]:
        """Check whether adding ``new_symbol`` is allowed given existing positions.

        Parameters
        ----------
        new_symbol:
            Symbol of the proposed new position.
        new_candles:
            Recent candles for the new symbol.
        existing_positions:
            Current open positions.  Each dict must contain a ``"symbol"`` key.
        all_candles:
            Candle data keyed by symbol for all positions.

        Returns
        -------
        (allowed, max_correlation)
            ``allowed`` is False when the max pairwise correlation exceeds
            ``self.max_pairwise_correlation`` AND ``block_on_exceed`` is True.
            ``max_correlation`` is the highest |correlation| found.
        """
        existing_symbols = [
            p["symbol"]
            for p in existing_positions
            if p.get("symbol") and p["symbol"] != new_symbol
        ]

        if not existing_symbols:
            # No existing positions — no correlation constraint
            return True, 0.0

        max_corr = 0.0
        new_rets = _log_returns(new_candles[-self.lookback_bars:])

        for sym in existing_symbols:
            if sym not in all_candles:
                continue
            exist_rets = _log_returns(all_candles[sym][-self.lookback_bars:])
            corr = abs(self.correlation_score(
                new_candles[-self.lookback_bars:],
                all_candles[sym][-self.lookback_bars:],
            ))
            if corr > max_corr:
                max_corr = corr

        allowed = True
        if self.block_on_exceed and max_corr > self.max_pairwise_correlation:
            allowed = False

        return allowed, round(max_corr, 4)

    def correlation_score(
        self,
        candles_a: List[Candle],
        candles_b: List[Candle],
    ) -> float:
        """Pearson correlation of log returns over ``lookback_bars`` bars.

        Parameters
        ----------
        candles_a, candles_b:
            Candle series for the two assets.

        Returns
        -------
        float
            Pearson correlation in [-1.0, 1.0].
        """
        n = self.lookback_bars
        rets_a = _log_returns(candles_a[-n - 1:])
        rets_b = _log_returns(candles_b[-n - 1:])
        return _pearson(rets_a, rets_b)
