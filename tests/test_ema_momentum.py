"""Unit tests for the EMA Momentum strategy.

Covers the full cross x ROC matrix plus warmup/insufficient-data guards.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from trading.strategies.ema_momentum import (
    EmaMomentumConfig,
    EmaMomentumStrategy,
    roc,
)
from trading.strategy import Signal


# ── ROC helper ────────────────────────────────────────────────────────────────

class TestRoc:
    def test_positive_change(self):
        # 10-period ROC, last = 110, anchor = 100 -> +10%
        closes = [100.0] * 11
        closes[-1] = 110.0
        assert roc(closes, 10) == pytest.approx(10.0)

    def test_negative_change(self):
        closes = [100.0] * 11
        closes[-1] = 90.0
        assert roc(closes, 10) == pytest.approx(-10.0)

    def test_zero_change_flat(self):
        assert roc([100.0] * 11, 10) == 0.0

    def test_insufficient_data_raises(self):
        with pytest.raises(ValueError):
            roc([100.0, 101.0], 10)

    def test_zero_anchor_safe(self):
        closes = [0.0] + [100.0] * 10
        assert roc(closes, 10) == 0.0


# ── Strategy: scenarios ───────────────────────────────────────────────────────

def _strategy(**overrides) -> EmaMomentumStrategy:
    cfg = EmaMomentumConfig(**overrides)
    return EmaMomentumStrategy(cfg)


def _build_bullish_cross_series(
    flat_len: int,
    flat_price: float,
    ramp: list,
) -> list:
    """flat_len candles at flat_price, then `ramp` rising candles."""
    return [flat_price] * flat_len + list(ramp)


def _build_bearish_cross_series(
    flat_len: int,
    flat_price: float,
    drop: list,
) -> list:
    """flat_len candles at flat_price, then `drop` falling candles."""
    return [flat_price] * flat_len + list(drop)


class TestEvaluate:

    # ── HOLD branches ─────────────────────────────────────────────────────────

    def test_hold_on_insufficient_data(self):
        strat = _strategy()
        # warmup = slow + roc + 2 = 33; 20 candles is well below.
        sig = strat.evaluate("BTC_USDT", [100.0] * 20)
        assert isinstance(sig, Signal)
        assert sig.action == "HOLD"
        assert sig.confidence == "LOW"
        assert sig.coin == "BTC_USDT"
        # Indicator fields zeroed per contract.
        assert sig.rsi == 0.0
        assert sig.macd == 0.0
        assert sig.macd_signal_val == 0.0
        assert sig.macd_histogram == 0.0

    def test_hold_on_flat_market(self):
        strat = _strategy()
        # 100 candles of identical price -> no cross, no ROC movement.
        sig = strat.evaluate("BTC_USDT", [100.0] * 100)
        assert sig.action == "HOLD"
        assert sig.confidence == "LOW"

    # ── HIGH BUY ──────────────────────────────────────────────────────────────

    def test_high_confidence_buy_on_strong_bullish_cross(self):
        """Flat baseline, a brief dip pulls fast EMA below slow EMA, then a
        big rip on the final candle flips fast above slow with ROC well
        above the +1% threshold."""
        strat = _strategy()
        closes = [100.0] * 30 + [95.0, 94.0, 93.0, 92.0, 91.0, 130.0]

        sig = strat.evaluate("BTC_USDT", closes)
        assert sig.action == "BUY"
        assert sig.confidence == "HIGH"
        assert "bullish cross" in sig.reason.lower()

    # ── HIGH SELL ─────────────────────────────────────────────────────────────

    def test_high_confidence_sell_on_strong_bearish_cross(self):
        """Flat baseline, a brief uptick pushes fast EMA above slow EMA,
        then a sharp drop on the last candle flips fast below slow with
        ROC well past -threshold."""
        strat = _strategy()
        closes = [100.0] * 30 + [105.0, 106.0, 107.0, 108.0, 109.0, 70.0]

        sig = strat.evaluate("ETH_USDT", closes)
        assert sig.action == "SELL"
        assert sig.confidence == "HIGH"
        assert "bearish cross" in sig.reason.lower()

    # ── MEDIUM BUY (cross present, ROC positive but under threshold) ──────────

    def test_medium_confidence_buy_when_roc_below_threshold(self):
        """Same bullish cross construction as the HIGH case, but with the
        threshold lifted to 50% so ROC ~30% downgrades the signal to MEDIUM.
        """
        strat = _strategy(roc_threshold_pct=50.0)
        closes = [100.0] * 30 + [95.0, 94.0, 93.0, 92.0, 91.0, 130.0]

        sig = strat.evaluate("SOL_USDT", closes)
        assert sig.action == "BUY"
        assert sig.confidence == "MEDIUM"

    # ── MEDIUM SELL (cross present, ROC negative but above -threshold) ────────

    def test_medium_confidence_sell_when_roc_above_negative_threshold(self):
        """Same bearish cross construction as the HIGH case, but with the
        threshold lifted to 50% so ROC ~-30% downgrades the signal to MEDIUM.
        """
        strat = _strategy(roc_threshold_pct=50.0)
        closes = [100.0] * 30 + [105.0, 106.0, 107.0, 108.0, 109.0, 70.0]

        sig = strat.evaluate("XRP_USDT", closes)
        assert sig.action == "SELL"
        assert sig.confidence == "MEDIUM"

    # ── No-cross HOLD with sufficient data ────────────────────────────────────

    def test_hold_when_steady_uptrend_has_no_fresh_cross(self):
        """A smooth, established uptrend keeps fast above slow on both of the
        last two candles -> no bullish_cross flag (it's not a fresh cross)."""
        strat = _strategy()
        closes = [100.0 + i * 0.5 for i in range(60)]
        sig = strat.evaluate("BTC_USDT", closes)
        assert sig.action == "HOLD"
        assert sig.confidence == "LOW"

    # ── Config & warmup contract ──────────────────────────────────────────────

    def test_warmup_matches_formula(self):
        strat = _strategy()
        assert strat.warmup == strat.config.slow_period + strat.config.roc_period + 2

    def test_custom_config_propagates(self):
        cfg = EmaMomentumConfig(fast_period=5, slow_period=15, roc_period=8,
                                roc_threshold_pct=2.0)
        strat = EmaMomentumStrategy(cfg)
        assert strat.warmup == 15 + 8 + 2
        assert strat.config.fast_period == 5

    def test_default_coins_basket(self):
        strat = _strategy()
        assert "BTC_USDT" in strat.config.coins
        assert "ETH_USDT" in strat.config.coins
        assert len(strat.config.coins) == 4
