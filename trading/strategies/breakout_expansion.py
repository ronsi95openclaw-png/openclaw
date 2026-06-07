"""
ClawBot — Breakout Expansion Strategy
=====================================

Thesis
------
Low-volatility consolidations (Bollinger Band "squeezes") tend to resolve
into directional expansions. When the most recent close pierces the upper
or lower band immediately after such a squeeze, that breakout candle is
the asymmetric entry. Adding an ATR expansion filter separates HIGH-
conviction breakouts (volatility actually woke up) from MEDIUM-conviction
ones (price tagged the band but the range did not expand).

Rules (most recent candle only)
-------------------------------
1. Compute current Bollinger Bands ``(upper, mid, lower)``.
2. Compute the current BB width = ``upper - lower``.
3. Compute the BB-width history for the prior ``squeeze_lookback`` candles
   (each width uses the same ``bb_period``/``bb_stdev``).
4. **Was in squeeze**: the minimum width over the last 3-5 candles
   (excluding the current one) is at or below the ``squeeze_percentile``
   of that history.
5. **Breakout up**:   close > upper  AND was_in_squeeze   ->  BUY
   **Breakout down**: close < lower  AND was_in_squeeze   ->  SELL
6. **HIGH confidence**:  breakout + current ATR >= ``atr_expansion`` * mean
   of the prior 20 ATR readings.
   **MEDIUM confidence**: breakout without the ATR confirmation.
7. Otherwise HOLD.

Notes
-----
- We only have close prices in this codebase, not full OHLC. The ATR
  approximation therefore uses ``|close[i] - close[i-1]|`` as a stand-in
  for True Range. This is the standard "absolute close-to-close range"
  proxy; it is monotonic with realised volatility and adequate for the
  expansion-vs-baseline ratio we care about here.
- The strategy is **pure**: no I/O, no network, no time.sleep. Safe to
  call from the backtest harness and from the live scanner alike.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from trading.strategy import Signal, calculate_ema  # re-use existing helpers

logger = logging.getLogger("clawbot.trading.strategies.breakout_expansion")


# ── Configuration ─────────────────────────────────────────────────────────────


@dataclass
class BreakoutExpansionConfig:
    """Tunable parameters for :class:`BreakoutExpansionStrategy`."""

    bb_period: int = 20
    bb_stdev: float = 2.0

    squeeze_lookback: int = 50
    squeeze_percentile: float = 0.25
    squeeze_recent_window: int = 5  # min width over last N candles (excl. current)

    atr_period: int = 14
    atr_expansion: float = 1.2
    atr_baseline_window: int = 20  # how many prior ATR readings to average

    coins: List[str] = field(default_factory=lambda: [
        "BTC_USDT", "SOL_USDT", "XRP_USDT", "ETH_USDT",
    ])


# ── Indicators ────────────────────────────────────────────────────────────────


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev_population(values: List[float], mean: float) -> float:
    """Population stdev (matches the standard Bollinger Band convention)."""
    if not values:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return var ** 0.5


def bollinger_bands(
    closes: List[float], period: int = 20, stdev: float = 2.0
) -> Tuple[float, float, float]:
    """Latest-candle Bollinger Bands as ``(upper, mid, lower)``.

    Uses the most recent ``period`` closes; ``mid`` is the simple moving
    average and the bands are ``mid ± stdev * sigma`` with population sigma.
    """
    if len(closes) < period:
        raise ValueError(f"Need {period} closes for Bollinger Bands, got {len(closes)}.")
    window = closes[-period:]
    mid = _mean(window)
    sigma = _stdev_population(window, mid)
    upper = mid + stdev * sigma
    lower = mid - stdev * sigma
    return upper, mid, lower


def bb_width_history(
    closes: List[float], period: int = 20, stdev: float = 2.0, lookback: int = 50
) -> List[float]:
    """BB widths over the most recent ``lookback`` candles.

    Returns a list of length ``lookback`` ordered oldest -> newest, where
    ``result[-1]`` is the width using the latest close and ``result[0]`` is
    the width ``lookback - 1`` candles ago. Each width = upper - lower
    using a window ending at that candle.
    """
    if len(closes) < period + lookback - 1:
        raise ValueError(
            f"Need {period + lookback - 1} closes for BB width history "
            f"(period={period}, lookback={lookback}), got {len(closes)}."
        )
    widths: List[float] = []
    # The last candle index in the closes list whose window we measure.
    start = len(closes) - lookback
    end = len(closes)  # exclusive
    for i in range(start, end):
        upper, _, lower = bollinger_bands(closes[: i + 1], period=period, stdev=stdev)
        widths.append(upper - lower)
    return widths


def atr_from_closes(closes: List[float], period: int = 14) -> float:
    """Close-only approximation of ATR over the most recent ``period`` candles.

    True Range normally uses OHLC; we only have closes, so we substitute
    ``|close[i] - close[i-1]|`` as a stand-in. This understates absolute
    range but moves monotonically with realised volatility, which is what
    the expansion ratio cares about.
    """
    if len(closes) < period + 1:
        raise ValueError(f"Need {period + 1} closes for ATR, got {len(closes)}.")
    diffs = [abs(closes[i] - closes[i - 1]) for i in range(len(closes) - period, len(closes))]
    return _mean(diffs)


def _atr_history(closes: List[float], period: int, count: int) -> List[float]:
    """The last ``count`` ATR readings, ordered oldest -> newest.

    ``result[-1]`` is the current ATR; ``result[:-1]`` are the prior
    baseline readings used for the expansion comparison.
    """
    if len(closes) < period + count:
        raise ValueError(
            f"Need {period + count} closes for ATR history "
            f"(period={period}, count={count}), got {len(closes)}."
        )
    out: List[float] = []
    for i in range(count):
        # window ending at len(closes) - (count - 1 - i)
        end = len(closes) - (count - 1 - i)
        out.append(atr_from_closes(closes[:end], period=period))
    return out


# ── Strategy Engine ───────────────────────────────────────────────────────────


class BreakoutExpansionStrategy:
    """Bollinger-squeeze breakout strategy with ATR expansion confirmation.

    See module docstring for the full rule set. The class deliberately
    mirrors the :class:`trading.strategy.RSIMACDStrategy` shape so it
    plugs directly into :func:`trading.backtest.walk_forward`.
    """

    def __init__(self, config: Optional[BreakoutExpansionConfig] = None):
        self.config = config or BreakoutExpansionConfig()
        cfg = self.config
        # warmup must cover: BB width history + the ATR baseline window.
        bb_warmup = cfg.squeeze_lookback + cfg.bb_period
        atr_warmup = cfg.atr_period + cfg.atr_baseline_window + 1
        self.warmup: int = max(bb_warmup, atr_warmup) + 1

    # ------------------------------------------------------------------ helpers

    def _hold(self, coin: str, reason: str, *, confidence: str = "LOW") -> Signal:
        return Signal(
            coin=coin,
            action="HOLD",
            rsi=0.0,
            macd=0.0,
            macd_signal_val=0.0,
            macd_histogram=0.0,
            reason=reason,
            confidence=confidence,
        )

    # ------------------------------------------------------------------ evaluate

    def evaluate(self, coin: str, closes: List[float]) -> Signal:
        """Return a :class:`Signal` for ``coin`` based on the latest close.

        ``closes`` is ordered oldest -> newest; only the final element is
        treated as "now". Returns HOLD with LOW confidence on any
        insufficient-data condition so the backtest can keep walking
        forward safely.
        """
        cfg = self.config

        if len(closes) < self.warmup:
            return self._hold(coin, "Insufficient candle data for breakout expansion.")

        # --- Bollinger Bands on the current candle ----------------------------
        try:
            upper, mid, lower = bollinger_bands(closes, period=cfg.bb_period, stdev=cfg.bb_stdev)
            widths = bb_width_history(
                closes,
                period=cfg.bb_period,
                stdev=cfg.bb_stdev,
                lookback=cfg.squeeze_lookback,
            )
        except ValueError as exc:
            logger.warning(f"[{coin}] BB warmup miss: {exc}")
            return self._hold(coin, f"BB warmup miss: {exc}")

        current_width = widths[-1]
        # "Was in squeeze" looks at the recent window EXCLUDING the current
        # candle — the breakout candle naturally widens the bands, so using
        # it would defeat the squeeze condition.
        recent_n = min(cfg.squeeze_recent_window, cfg.squeeze_lookback - 1)
        if recent_n <= 0:
            return self._hold(coin, "squeeze_recent_window misconfigured.")
        recent_prior_widths = widths[-1 - recent_n:-1]
        min_recent_width = min(recent_prior_widths) if recent_prior_widths else current_width

        # Squeeze threshold from the full lookback history (excluding the
        # current candle so a breakout doesn't inflate the percentile).
        history_for_pct = sorted(widths[:-1])
        if not history_for_pct:
            return self._hold(coin, "Empty BB width history.")
        idx = max(0, min(len(history_for_pct) - 1,
                         int(len(history_for_pct) * cfg.squeeze_percentile) - 1))
        squeeze_threshold = history_for_pct[idx]
        was_in_squeeze = min_recent_width <= squeeze_threshold

        close = closes[-1]
        broke_up = close > upper and was_in_squeeze
        broke_down = close < lower and was_in_squeeze

        if not (broke_up or broke_down):
            if was_in_squeeze:
                reason = (
                    f"Squeeze active (min recent width "
                    f"{min_recent_width:.6f} <= p{int(cfg.squeeze_percentile * 100)} "
                    f"{squeeze_threshold:.6f}) but no breakout yet."
                )
            else:
                reason = (
                    f"No squeeze (min recent width {min_recent_width:.6f} > "
                    f"p{int(cfg.squeeze_percentile * 100)} {squeeze_threshold:.6f})."
                )
            return self._hold(coin, reason)

        # --- ATR expansion confirmation --------------------------------------
        try:
            atr_series = _atr_history(
                closes, period=cfg.atr_period, count=cfg.atr_baseline_window + 1
            )
        except ValueError as exc:
            logger.warning(f"[{coin}] ATR warmup miss: {exc}")
            return self._hold(coin, f"ATR warmup miss: {exc}")

        current_atr = atr_series[-1]
        prior_atrs = atr_series[:-1]
        baseline_atr = _mean(prior_atrs) if prior_atrs else 0.0
        atr_confirmed = (
            baseline_atr > 0.0 and current_atr >= cfg.atr_expansion * baseline_atr
        )

        action = "BUY" if broke_up else "SELL"
        direction = "up" if broke_up else "down"

        if atr_confirmed:
            reason = (
                f"BB squeeze -> breakout {direction} (close {close:.6f} "
                f"{'>' if broke_up else '<'} band) with ATR expansion "
                f"{current_atr:.6f} >= {cfg.atr_expansion:.2f}x baseline "
                f"{baseline_atr:.6f}."
            )
            confidence = "HIGH"
        else:
            reason = (
                f"BB squeeze -> breakout {direction} (close {close:.6f} "
                f"{'>' if broke_up else '<'} band); ATR {current_atr:.6f} "
                f"vs baseline {baseline_atr:.6f} — momentum unconfirmed."
            )
            confidence = "MEDIUM"

        return Signal(
            coin=coin,
            action=action,
            rsi=0.0,
            macd=0.0,
            macd_signal_val=0.0,
            macd_histogram=0.0,
            reason=reason,
            confidence=confidence,
        )

    # ------------------------------------------------------------------ scan_all

    def scan_all(self, candle_data: dict) -> list:
        """Convenience: evaluate every configured coin, return actionable signals."""
        actionable = []
        for coin in self.config.coins:
            if coin not in candle_data:
                logger.warning(f"No data for {coin}, skipping.")
                continue
            signal = self.evaluate(coin, candle_data[coin])
            logger.info(
                f"[{coin}] {signal.action} | conf={signal.confidence} | "
                f"reason={signal.reason}"
            )
            if signal.action != "HOLD":
                actionable.append(signal)
        return actionable


# `calculate_ema` is re-exported in case downstream callers want to compose
# this strategy with EMA-based filters without re-importing trading.strategy.
__all__ = [
    "BreakoutExpansionConfig",
    "BreakoutExpansionStrategy",
    "bollinger_bands",
    "bb_width_history",
    "atr_from_closes",
    "calculate_ema",
]
