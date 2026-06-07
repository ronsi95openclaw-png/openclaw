"""
Trend Continuation Strategy
===========================
Thesis: in an established trend (50-EMA slope positive/negative), wait for a
shallow RSI pullback against the trend, then ride the resumption.

Rules
-----
EMA(50) slope = ema[-1] - ema[-slope_lookback]   (positive => uptrend)

Uptrend  (slope > 0):
  HIGH   BUY  — RSI in [pullback_low, pullback_high] AND RSI just turned UP.
                Pullback confirmed; entry on the bounce.
  MEDIUM BUY  — RSI in [pullback_low, pullback_high] (no turn-up yet).
                Watching the pullback.

Downtrend (slope < 0):
  HIGH   SELL — RSI in [pullback_high, bounce_high] AND RSI just turned DOWN.
                Counter-trend bounce rejected; entry on the rollover.
  MEDIUM SELL — RSI in [pullback_high, bounce_high] (no turn-down yet).

Otherwise: HOLD.

The strategy is pure: no I/O, no time, no network. The Signal it returns
re-uses ``trading.strategy.Signal``; unused MACD fields are zeroed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from trading.strategy import Signal, calculate_ema, calculate_rsi

logger = logging.getLogger("clawbot.trading.strategies.trend_continuation")


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class TrendContinuationConfig:
    """Parameters for the Trend Continuation strategy."""
    ema_period: int = 50
    slope_lookback: int = 5
    rsi_period: int = 14

    pullback_low: float = 35.0
    pullback_high: float = 55.0
    bounce_high: float = 65.0

    # Treat slopes inside this absolute threshold as "no trend" (HOLD).
    flat_slope_eps: float = 1e-9

    risk_per_trade_pct: float = 1.5
    max_open_positions: int = 4

    coins: List[str] = field(default_factory=lambda: [
        "BTC_USDT", "SOL_USDT", "XRP_USDT", "ETH_USDT",
    ])


# ── Strategy Engine ───────────────────────────────────────────────────────────

class TrendContinuationStrategy:
    """Pullback-into-trend strategy keyed off EMA slope + RSI."""

    def __init__(self, config: Optional[TrendContinuationConfig] = None) -> None:
        self.config = config or TrendContinuationConfig()
        # Need ema_period candles to seed the EMA, + slope_lookback prior EMA
        # values to measure slope, + 1 extra close to compute the previous RSI
        # (so we can tell if RSI turned).
        self.warmup: int = (
            self.config.ema_period + self.config.slope_lookback + 1
        )

    # ── helpers ───────────────────────────────────────────────────────────

    def _hold(self, coin: str, reason: str, rsi: float = 0.0) -> Signal:
        return Signal(
            coin=coin,
            action="HOLD",
            rsi=rsi,
            macd=0.0,
            macd_signal_val=0.0,
            macd_histogram=0.0,
            reason=reason,
            confidence="LOW",
        )

    # ── public API ────────────────────────────────────────────────────────

    def evaluate(self, coin: str, closes: List[float]) -> Signal:
        """Score the latest candle. Returns a Signal compatible with the executor."""
        cfg = self.config

        if len(closes) < self.warmup:
            return self._hold(
                coin,
                f"Insufficient candles ({len(closes)} < warmup {self.warmup}).",
            )

        # ── EMA + slope ───────────────────────────────────────────────────
        try:
            ema_series = calculate_ema(closes, cfg.ema_period)
        except ValueError as e:
            logger.warning(f"[{coin}] EMA failed — {e}")
            return self._hold(coin, "EMA calculation failed.")

        if len(ema_series) <= cfg.slope_lookback:
            return self._hold(coin, "Not enough EMA points to measure slope.")

        slope = ema_series[-1] - ema_series[-1 - cfg.slope_lookback]

        # ── RSI now + RSI on the prior candle (to detect turn) ────────────
        try:
            rsi_now = calculate_rsi(closes, cfg.rsi_period)
            rsi_prev = calculate_rsi(closes[:-1], cfg.rsi_period)
        except ValueError as e:
            logger.warning(f"[{coin}] RSI failed — {e}")
            return self._hold(coin, "RSI calculation failed.")

        # ── Classify trend ────────────────────────────────────────────────
        if abs(slope) <= cfg.flat_slope_eps:
            return self._hold(
                coin,
                f"No trend (EMA slope ~ 0, rsi={rsi_now:.1f}).",
                rsi=rsi_now,
            )

        # ── Uptrend branch ────────────────────────────────────────────────
        if slope > 0:
            in_zone = cfg.pullback_low <= rsi_now <= cfg.pullback_high
            if not in_zone:
                return self._hold(
                    coin,
                    f"Uptrend but RSI ({rsi_now:.1f}) outside pullback zone "
                    f"[{cfg.pullback_low:.0f}, {cfg.pullback_high:.0f}].",
                    rsi=rsi_now,
                )
            if rsi_now > rsi_prev:
                return Signal(
                    coin=coin, action="BUY",
                    rsi=rsi_now, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                    reason=(
                        f"Uptrend (EMA slope +{slope:.4f}); pullback confirmed "
                        f"(RSI {rsi_prev:.1f} -> {rsi_now:.1f} turned up in zone). "
                        f"Riding resumption."
                    ),
                    confidence="HIGH",
                )
            return Signal(
                coin=coin, action="BUY",
                rsi=rsi_now, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                reason=(
                    f"Uptrend (EMA slope +{slope:.4f}); RSI ({rsi_now:.1f}) "
                    f"in pullback zone but no turn-up yet. Scaling in."
                ),
                confidence="MEDIUM",
            )

        # ── Downtrend branch (slope < 0) ──────────────────────────────────
        in_zone = cfg.pullback_high <= rsi_now <= cfg.bounce_high
        if not in_zone:
            return self._hold(
                coin,
                f"Downtrend but RSI ({rsi_now:.1f}) outside bounce zone "
                f"[{cfg.pullback_high:.0f}, {cfg.bounce_high:.0f}].",
                rsi=rsi_now,
            )
        if rsi_now < rsi_prev:
            return Signal(
                coin=coin, action="SELL",
                rsi=rsi_now, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                reason=(
                    f"Downtrend (EMA slope {slope:.4f}); bounce rejected "
                    f"(RSI {rsi_prev:.1f} -> {rsi_now:.1f} turned down in zone). "
                    f"Riding resumption."
                ),
                confidence="HIGH",
            )
        return Signal(
            coin=coin, action="SELL",
            rsi=rsi_now, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
            reason=(
                f"Downtrend (EMA slope {slope:.4f}); RSI ({rsi_now:.1f}) in "
                f"bounce zone but no turn-down yet. Scaling out."
            ),
            confidence="MEDIUM",
        )

    # ── Convenience: bulk scan, mirrors RSIMACDStrategy.scan_all ──────────

    def scan_all(self, candle_data: dict) -> list:
        actionable = []
        for coin in self.config.coins:
            if coin not in candle_data:
                logger.warning(f"No data for {coin}, skipping.")
                continue
            signal = self.evaluate(coin, candle_data[coin])
            logger.info(
                f"[{coin}] {signal.action} | RSI={signal.rsi:.1f} | "
                f"Conf={signal.confidence}"
            )
            if signal.action != "HOLD":
                actionable.append(signal)
        return actionable
