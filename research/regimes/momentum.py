"""Momentum regime indicators.

Functions:
  rsi                  — Relative Strength Index
  rate_of_change       — (close[-1] - close[-period]) / close[-period]
  momentum_score       — composite 0–1 score (RSI + ROC + price vs SMA)
  is_momentum_dominant — bool threshold on momentum_score
  is_mean_reverting    — True when RSI is in extreme territory
"""
from __future__ import annotations

from typing import List

from research.types import Candle  # noqa: F401 (unused here but available for callers)


# ── helpers ───────────────────────────────────────────────────────────────────

def _sma(values: List[float], period: int) -> float:
    """Simple moving average of the last ``period`` values."""
    if len(values) < period:
        return sum(values) / len(values) if values else 0.0
    return sum(values[-period:]) / period


# ── public API ────────────────────────────────────────────────────────────────

def rsi(closes: List[float], period: int = 14) -> float:
    """Relative Strength Index.

    Uses Wilder smoothing (EMA-based).  Returns 50.0 when insufficient data.
    """
    if len(closes) < period + 1:
        return 50.0

    deltas: List[float] = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains:  List[float] = [d if d > 0 else 0.0 for d in deltas]
    losses: List[float] = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period])  / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i])  / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rate_of_change(closes: List[float], period: int = 10) -> float:
    """(close[-1] - close[-period]) / close[-period].

    Returns 0.0 when insufficient data or division by zero.
    """
    if len(closes) < period + 1:
        return 0.0

    old = closes[-(period + 1)]
    cur = closes[-1]
    if old <= 0:
        return 0.0
    return (cur - old) / old


def momentum_score(closes: List[float]) -> float:
    """Composite 0–1 momentum score.

    Components:
    1. RSI component     — how far RSI is from neutral (50), mapped to 0–1
    2. ROC component     — positive ROC → momentum up; mapped to 0–1
    3. Price-vs-SMA(20)  — price relative to 20-bar SMA; mapped to 0–1

    All three are equally weighted and averaged to produce a single score.
    0 = no momentum / mean-reverting conditions
    1 = very strong momentum

    Returns 0.5 when insufficient data.
    """
    if len(closes) < 21:
        return 0.5

    # 1. RSI component
    rsi_val = rsi(closes, 14)
    # Extreme RSI values (< 30 or > 70) → high *mean-reversion* signal.
    # For momentum score we want middle RSI to be weak and directional RSI
    # (e.g. 60–80 for bullish) to be strong.
    # Map RSI ∈ [0,100] so that 50±15 → near 0, extremes → near 1.
    rsi_distance_from_neutral = abs(rsi_val - 50.0) / 50.0   # 0 at RSI=50, 1 at RSI=0/100
    rsi_component = rsi_distance_from_neutral

    # 2. ROC component — directional momentum
    roc = rate_of_change(closes, 10)
    # Map ±10% range to 0–1; clamped.
    roc_component = min(1.0, max(0.0, (roc + 0.10) / 0.20))

    # 3. Price vs SMA(20)
    sma20 = _sma(closes, 20)
    if sma20 > 0:
        price_vs_sma = (closes[-1] - sma20) / sma20   # fraction above/below
        # Map ±5% to 0–1
        sma_component = min(1.0, max(0.0, (price_vs_sma + 0.05) / 0.10))
    else:
        sma_component = 0.5

    return (rsi_component + roc_component + sma_component) / 3.0


def is_momentum_dominant(closes: List[float], threshold: float = 0.65) -> bool:
    """True when the composite momentum score exceeds ``threshold``."""
    return momentum_score(closes) >= threshold


def is_mean_reverting(closes: List[float], rsi_extreme: float = 30.0) -> bool:
    """True when RSI is in extreme territory (oversold OR overbought).

    Parameters
    ----------
    closes:
        Recent closing prices.
    rsi_extreme:
        Symmetrical RSI boundary.  RSI < rsi_extreme (oversold) or
        RSI > (100 - rsi_extreme) (overbought) → mean-reverting.
    """
    rsi_val = rsi(closes, 14)
    return rsi_val < rsi_extreme or rsi_val > (100.0 - rsi_extreme)
