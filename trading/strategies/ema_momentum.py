"""
ClawBot - EMA Momentum Strategy
===============================
Thesis: A fast EMA crossing a slow EMA marks a momentum shift. The strength of
that shift is confirmed (or rejected) by the Rate-of-Change indicator over the
same lookback window.

Rules (most recent two candles, indices i-1 and i):
  bullish_cross : EMA_fast[i-1] <= EMA_slow[i-1] AND EMA_fast[i] > EMA_slow[i]
  bearish_cross : EMA_fast[i-1] >= EMA_slow[i-1] AND EMA_fast[i] < EMA_slow[i]

ROC(period) = (closes[-1] / closes[-period-1] - 1) * 100

Signal grid:
  HIGH BUY    : bullish_cross AND roc >=  +threshold
  HIGH SELL   : bearish_cross AND roc <=  -threshold
  MEDIUM BUY  : bullish_cross AND  0 < roc <  threshold
  MEDIUM SELL : bearish_cross AND -threshold < roc < 0
  HOLD        : otherwise

The class exposes:
  .warmup = slow_period + roc_period + 2
  .config = EmaMomentumConfig instance
  .evaluate(coin, closes) -> Signal
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from trading.strategy import Signal, calculate_ema

logger = logging.getLogger("clawbot.trading.strategies.ema_momentum")


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class EmaMomentumConfig:
    fast_period: int = 9
    slow_period: int = 21
    roc_period: int = 10
    roc_threshold_pct: float = 1.0

    coins: list = field(default_factory=lambda: [
        "BTC_USDT", "SOL_USDT", "XRP_USDT", "ETH_USDT"
    ])


# ── Indicator helper ──────────────────────────────────────────────────────────

def roc(closes: List[float], period: int) -> float:
    """Rate of change percent: (closes[-1] / closes[-period-1] - 1) * 100.

    Pure. Requires len(closes) >= period + 1 and a non-zero anchor price.
    """
    if len(closes) < period + 1:
        raise ValueError(f"Need {period + 1} closes for ROC, got {len(closes)}.")
    anchor = closes[-period - 1]
    if anchor == 0:
        return 0.0
    return (closes[-1] / anchor - 1.0) * 100.0


# ── Strategy Engine ───────────────────────────────────────────────────────────

class EmaMomentumStrategy:
    """EMA crossover + ROC confirmation strategy.

    Uses the fast/slow EMAs to detect a momentum shift on the most recent
    candle, then grades the resulting signal by the magnitude of ROC over
    the configured lookback.
    """

    def __init__(self, config: Optional[EmaMomentumConfig] = None):
        self.config = config or EmaMomentumConfig()

    @property
    def warmup(self) -> int:
        """Min candles required before evaluate is meaningful."""
        return self.config.slow_period + self.config.roc_period + 2

    def evaluate(self, coin: str, closes: List[float]) -> Signal:
        """Evaluate the most recent candle and return a Signal."""
        cfg = self.config

        if len(closes) < self.warmup:
            return Signal(
                coin=coin, action="HOLD",
                rsi=0.0, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                reason="Insufficient candle data.", confidence="LOW",
            )

        try:
            ema_fast_series = calculate_ema(closes, cfg.fast_period)
            ema_slow_series = calculate_ema(closes, cfg.slow_period)
            roc_value = roc(closes, cfg.roc_period)
        except ValueError as e:
            logger.warning(f"[{coin}] Skipped - {e}")
            return Signal(
                coin=coin, action="HOLD",
                rsi=0.0, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                reason="Insufficient candle data.", confidence="LOW",
            )

        # Both EMA series are right-aligned to closes[-1]; their tails refer to
        # the same two most-recent candles.
        if len(ema_slow_series) < 2 or len(ema_fast_series) < 2:
            return Signal(
                coin=coin, action="HOLD",
                rsi=0.0, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                reason="Insufficient EMA history for crossover.", confidence="LOW",
            )

        fast_prev = ema_fast_series[-2]
        fast_curr = ema_fast_series[-1]
        slow_prev = ema_slow_series[-2]
        slow_curr = ema_slow_series[-1]

        bullish_cross = fast_prev <= slow_prev and fast_curr > slow_curr
        bearish_cross = fast_prev >= slow_prev and fast_curr < slow_curr

        if bullish_cross and roc_value >= cfg.roc_threshold_pct:
            return Signal(
                coin=coin, action="BUY",
                rsi=0.0, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                reason=(
                    f"EMA{cfg.fast_period}/EMA{cfg.slow_period} bullish cross + "
                    f"ROC {roc_value:+.2f}% >= {cfg.roc_threshold_pct:+.2f}%. "
                    f"Strong momentum."
                ),
                confidence="HIGH",
            )

        if bearish_cross and roc_value <= -cfg.roc_threshold_pct:
            return Signal(
                coin=coin, action="SELL",
                rsi=0.0, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                reason=(
                    f"EMA{cfg.fast_period}/EMA{cfg.slow_period} bearish cross + "
                    f"ROC {roc_value:+.2f}% <= -{cfg.roc_threshold_pct:.2f}%. "
                    f"Strong downside momentum."
                ),
                confidence="HIGH",
            )

        if bullish_cross and roc_value > 0:
            return Signal(
                coin=coin, action="BUY",
                rsi=0.0, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                reason=(
                    f"EMA{cfg.fast_period}/EMA{cfg.slow_period} bullish cross, "
                    f"ROC {roc_value:+.2f}% positive but below "
                    f"{cfg.roc_threshold_pct:+.2f}% threshold."
                ),
                confidence="MEDIUM",
            )

        if bearish_cross and roc_value < 0:
            return Signal(
                coin=coin, action="SELL",
                rsi=0.0, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                reason=(
                    f"EMA{cfg.fast_period}/EMA{cfg.slow_period} bearish cross, "
                    f"ROC {roc_value:+.2f}% negative but above "
                    f"-{cfg.roc_threshold_pct:.2f}% threshold."
                ),
                confidence="MEDIUM",
            )

        return Signal(
            coin=coin, action="HOLD",
            rsi=0.0, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
            reason=(
                f"No EMA{cfg.fast_period}/EMA{cfg.slow_period} cross on latest "
                f"candle. ROC {roc_value:+.2f}%."
            ),
            confidence="LOW",
        )
