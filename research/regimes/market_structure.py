"""Market structure regime indicators.

Functions:
  liquidity_drought     — True when volume is < 50% of rolling average
  panic_conditions      — True when rapid price drop + vol spike detected
  higher_timeframe_trend — HTF trend from aggregated candles
"""
from __future__ import annotations

import math
from typing import List

from research.types import Candle
from research.regimes.trend import trend_direction
from research.regimes.volatility import relative_atr


# ── helpers ───────────────────────────────────────────────────────────────────

def _aggregate_candles(candles: List[Candle], n: int) -> List[Candle]:
    """Aggregate ``n`` consecutive candles into single higher-timeframe bars.

    Each output bar spans exactly ``n`` input bars:
      open  = first bar's open
      high  = max of all highs
      low   = min of all lows
      close = last bar's close
      volume = sum of all volumes
      ts    = last bar's ts
    """
    if n <= 1 or len(candles) < n:
        return list(candles)

    htf: List[Candle] = []
    # Work through complete chunks
    for i in range(0, len(candles) - n + 1, n):
        chunk = candles[i : i + n]
        htf.append(
            Candle(
                ts=chunk[-1].ts,
                open=chunk[0].open,
                high=max(c.high for c in chunk),
                low=min(c.low for c in chunk),
                close=chunk[-1].close,
                volume=sum(c.volume for c in chunk),
            )
        )
    return htf


# ── public API ────────────────────────────────────────────────────────────────

def liquidity_drought(
    candles: List[Candle],
    volume_ma_period: int = 20,
    volume_drought_threshold: float = 0.5,
) -> bool:
    """True if current volume is below ``volume_drought_threshold`` × rolling average.

    Parameters
    ----------
    candles:
        Price candles (at least ``volume_ma_period + 1`` bars required).
    volume_ma_period:
        Look-back for average volume calculation.
    volume_drought_threshold:
        Fraction of average volume below which we declare a drought.

    Returns False when insufficient data.
    """
    if len(candles) < volume_ma_period + 1:
        return False

    # Rolling average of the preceding ``volume_ma_period`` bars (excluding current)
    window = candles[-volume_ma_period - 1 : -1]
    avg_vol = sum(c.volume for c in window) / len(window)

    if avg_vol <= 0:
        return False

    current_vol = candles[-1].volume
    return current_vol < avg_vol * volume_drought_threshold


def panic_conditions(
    candles: List[Candle],
    vol_expansion_factor: float = 3.0,
    price_drop_pct: float = 5.0,
    lookback: int = 3,
) -> bool:
    """True when the recent ``lookback`` bars show rapid price drop with a vol spike.

    Panic requires BOTH:
    1. Cumulative price drop ≥ ``price_drop_pct`` % over ``lookback`` bars, AND
    2. Relative ATR ratio ≥ ``vol_expansion_factor`` (short ATR vs baseline).

    Parameters
    ----------
    candles:
        Price candles.
    vol_expansion_factor:
        Minimum short/long ATR ratio to qualify as a vol spike.
    price_drop_pct:
        Minimum total price drop (%) over the lookback window.
    lookback:
        Number of recent bars to inspect for the price drop.

    Returns False when fewer than ``lookback + 20`` candles are available.
    """
    min_bars = lookback + 20
    if len(candles) < min_bars:
        return False

    # Price drop check: open of lookback bars ago vs current close
    anchor_price = candles[-lookback - 1].close
    current_price = candles[-1].close

    if anchor_price <= 0:
        return False

    drop_pct = (anchor_price - current_price) / anchor_price * 100.0
    if drop_pct < price_drop_pct:
        return False

    # Vol spike check
    atr_ratio = relative_atr(candles, short_period=3, long_period=20)
    return atr_ratio >= vol_expansion_factor


def higher_timeframe_trend(candles: List[Candle], aggregate_n: int = 4) -> str:
    """'up' | 'down' | 'neutral' — aggregates ``aggregate_n`` bars into HTF candles.

    Parameters
    ----------
    candles:
        Base timeframe candles.
    aggregate_n:
        Number of base bars to combine into one HTF bar.

    Returns 'neutral' when insufficient data.
    """
    htf = _aggregate_candles(candles, aggregate_n)
    if len(htf) < 22:          # need enough HTF bars for EMA-21 + 1
        return "neutral"

    closes = [c.close for c in htf]
    return trend_direction(closes, fast=9, slow=21)
