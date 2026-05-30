"""Unit tests for trading.strategies.trend_continuation.

The strategy is pure, so each test builds a synthetic closes series that is
engineered to land RSI + EMA-slope in a specific cell of the decision matrix:

    uptrend(slope>0) x rsi in [35,55] x rsi_turned_up   -> HIGH BUY
    uptrend         x rsi in [35,55] x not turned up   -> MEDIUM BUY
    downtrend       x rsi in [55,65] x rsi_turned_down -> HIGH SELL
    downtrend       x rsi in [55,65] x not turned down -> MEDIUM SELL
    flat            x any                              -> HOLD
    trend           x rsi outside zone                 -> HOLD
    < warmup candles                                   -> HOLD
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading.strategies.trend_continuation import (
    TrendContinuationConfig,
    TrendContinuationStrategy,
)
from trading.strategy import Signal, calculate_ema, calculate_rsi


# ── Builders ──────────────────────────────────────────────────────────────────

def _uptrend(n: int = 70, start: float = 100.0, step: float = 1.0) -> list:
    """Smooth uptrend: closes rise by `step` each candle."""
    return [start + i * step for i in range(n)]


def _downtrend(n: int = 70, start: float = 300.0, step: float = 1.0) -> list:
    """Smooth downtrend: closes fall by `step` each candle."""
    return [start - i * step for i in range(n)]


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestStrategyConfig:
    def test_warmup_includes_ema_slope_and_prev_rsi(self):
        s = TrendContinuationStrategy()
        # ema_period (50) + slope_lookback (5) + 1 extra for prev RSI = 56
        assert s.warmup == 56

    def test_evaluate_returns_signal(self):
        s = TrendContinuationStrategy()
        out = s.evaluate("BTC_USDT", _uptrend())
        assert isinstance(out, Signal)
        # MACD fields are unused in this strategy.
        assert out.macd == 0.0
        assert out.macd_signal_val == 0.0
        assert out.macd_histogram == 0.0


class TestHoldCases:
    def test_insufficient_data_holds(self):
        sig = TrendContinuationStrategy().evaluate("BTC_USDT", [1.0, 2.0, 3.0])
        assert sig.action == "HOLD"
        assert sig.confidence == "LOW"
        assert "insufficient" in sig.reason.lower()

    def test_flat_market_holds(self):
        closes = [100.0] * 70
        sig = TrendContinuationStrategy().evaluate("BTC_USDT", closes)
        # slope is exactly 0 -> no trend
        assert sig.action == "HOLD"
        assert "no trend" in sig.reason.lower()

    def test_uptrend_but_rsi_too_high_holds(self):
        # Pure rising sequence => RSI saturates near 100, way above pullback_high (55).
        closes = _uptrend()
        sig = TrendContinuationStrategy().evaluate("BTC_USDT", closes)
        assert sig.action == "HOLD"
        assert "outside pullback zone" in sig.reason.lower()

    def test_downtrend_but_rsi_too_low_holds(self):
        # Pure falling sequence => RSI saturates near 0, below bounce_high (55).
        closes = _downtrend()
        sig = TrendContinuationStrategy().evaluate("BTC_USDT", closes)
        assert sig.action == "HOLD"
        assert "outside bounce zone" in sig.reason.lower()


class TestHighConfidenceBuy:
    def test_pullback_then_turn_up_in_uptrend_fires_high_buy(self):
        # 70 candles of uptrend, then 5 mild down candles to drag RSI into 35-55,
        # then one up candle to confirm the turn.
        closes = _uptrend(n=70, start=100.0, step=1.0)
        for d in [-2, -2, -2, -2, -2]:
            closes.append(closes[-1] + d)
        closes.append(closes[-1] + 0.6)  # turn up

        # Sanity checks: RSI in the zone, ticked up, EMA slope still positive.
        rsi_now = calculate_rsi(closes, 14)
        rsi_prev = calculate_rsi(closes[:-1], 14)
        ema = calculate_ema(closes, 50)
        slope = ema[-1] - ema[-1 - 5]
        assert 35.0 <= rsi_now <= 55.0
        assert rsi_now > rsi_prev
        assert slope > 0

        sig = TrendContinuationStrategy().evaluate("BTC_USDT", closes)
        assert sig.action == "BUY"
        assert sig.confidence == "HIGH"
        assert "pullback confirmed" in sig.reason.lower()
        assert sig.rsi > 0


class TestHighConfidenceSell:
    def test_relief_rally_then_turn_down_in_downtrend_fires_high_sell(self):
        # 70 candles of downtrend, then 6 sharp up candles to push RSI into 55-65,
        # then one down candle to confirm the rollover.
        closes = _downtrend(n=70, start=300.0, step=1.0)
        for d in [3, 3, 3, 3, 3, 3]:
            closes.append(closes[-1] + d)
        closes.append(closes[-1] - 1.0)

        rsi_now = calculate_rsi(closes, 14)
        rsi_prev = calculate_rsi(closes[:-1], 14)
        ema = calculate_ema(closes, 50)
        slope = ema[-1] - ema[-1 - 5]
        assert 55.0 <= rsi_now <= 65.0
        assert rsi_now < rsi_prev
        assert slope < 0

        sig = TrendContinuationStrategy().evaluate("BTC_USDT", closes)
        assert sig.action == "SELL"
        assert sig.confidence == "HIGH"
        assert "bounce rejected" in sig.reason.lower()


class TestMediumConfidence:
    def test_pullback_without_turn_up_fires_medium_buy(self):
        # Uptrend, then 6 down candles dragging RSI into the zone, no turn-up.
        closes = _uptrend(n=70, start=100.0, step=1.0)
        for d in [-2, -2, -2, -2, -2, -2]:
            closes.append(closes[-1] + d)

        rsi_now = calculate_rsi(closes, 14)
        rsi_prev = calculate_rsi(closes[:-1], 14)
        ema = calculate_ema(closes, 50)
        slope = ema[-1] - ema[-1 - 5]
        assert 35.0 <= rsi_now <= 55.0
        assert rsi_now <= rsi_prev          # no turn-up
        assert slope > 0

        sig = TrendContinuationStrategy().evaluate("BTC_USDT", closes)
        assert sig.action == "BUY"
        assert sig.confidence == "MEDIUM"
        assert "no turn-up" in sig.reason.lower()

    def test_relief_rally_without_turn_down_fires_medium_sell(self):
        # Downtrend, then 6 up candles into the bounce zone, no turn-down yet.
        closes = _downtrend(n=70, start=300.0, step=1.0)
        for d in [3, 3, 3, 3, 3, 3]:
            closes.append(closes[-1] + d)

        rsi_now = calculate_rsi(closes, 14)
        rsi_prev = calculate_rsi(closes[:-1], 14)
        ema = calculate_ema(closes, 50)
        slope = ema[-1] - ema[-1 - 5]
        assert 55.0 <= rsi_now <= 65.0
        assert rsi_now >= rsi_prev          # no turn-down
        assert slope < 0

        sig = TrendContinuationStrategy().evaluate("BTC_USDT", closes)
        assert sig.action == "SELL"
        assert sig.confidence == "MEDIUM"
        assert "no turn-down" in sig.reason.lower()


class TestCustomConfig:
    def test_custom_zones_change_decision(self):
        # Same uptrend-then-turn-up setup that produces HIGH BUY with defaults...
        closes = _uptrend(n=70, start=100.0, step=1.0)
        for d in [-2, -2, -2, -2, -2]:
            closes.append(closes[-1] + d)
        closes.append(closes[-1] + 0.6)

        # ...but tighten the pullback zone so the same RSI now falls outside it.
        tight = TrendContinuationConfig(
            pullback_low=10.0, pullback_high=20.0
        )
        sig = TrendContinuationStrategy(tight).evaluate("BTC_USDT", closes)
        assert sig.action == "HOLD"
        assert "outside pullback zone" in sig.reason.lower()
