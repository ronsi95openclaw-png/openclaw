"""Unit tests for the Liquidity Sweep strategy.

We construct synthetic close-series scenarios that satisfy the strategy's
"intra-window excursion using closes" definition of a sweep, plus negative
and edge cases. All series are deterministic and small.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading.strategies.liquidity_sweep import (  # noqa: E402
    LiquiditySweepConfig,
    LiquiditySweepStrategy,
)
from trading.strategy import Signal  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _strategy(**overrides) -> LiquiditySweepStrategy:
    """Build a strategy with overridable config knobs."""
    cfg = LiquiditySweepConfig(**overrides)
    return LiquiditySweepStrategy(cfg)


def _baseline_padding(n: int, value: float = 100.0) -> list:
    """Constant-price warmup pad so RSI / windows always have enough data."""
    return [value] * n


# ── tests ────────────────────────────────────────────────────────────────────

class TestWarmupAndGuards:
    def test_warmup_is_lookback_plus_sweep_plus_rsi_plus_one(self):
        s = _strategy(swing_lookback=20, sweep_within=5, rsi_period=14)
        assert s.warmup == 20 + 5 + 14 + 1

    def test_hold_on_insufficient_data(self):
        s = _strategy()
        sig = s.evaluate("BTC_USDT", [100.0, 101.0, 99.0])
        assert isinstance(sig, Signal)
        assert sig.action == "HOLD"
        assert sig.confidence == "LOW"
        assert "Insufficient" in sig.reason or "insufficient" in sig.reason.lower()

    def test_hold_on_flat_series(self):
        s = _strategy()
        # Enough closes for full warmup, all identical -> no breach possible.
        closes = _baseline_padding(s.warmup + 5, 100.0)
        sig = s.evaluate("BTC_USDT", closes)
        assert sig.action == "HOLD"


class TestBullishSweep:
    """Sweep below swing_low followed by snap-back above the offset threshold."""

    def _bullish_sweep_series(self):
        """
        Construct: long flat pad, then a descending reference window
        (swing_low at its END, with many losses behind it -> LOW RSI),
        then a recent window that gains for a few candles before printing
        one large dip to a NEW low (RSI at that new low includes the recent
        gains -> HIGHER than at the ref low). Final close snaps back inside.
        """
        s = _strategy(
            swing_lookback=20,
            sweep_within=5,
            min_breach_pct=0.5,
            max_close_offset_pct=0.5,
            rsi_period=14,
        )

        # Flat pad — neutralizes RSI history before the descent.
        pad = [200.0] * s.warmup

        # Reference window: 20 closes descending from 200 -> 101 (each step
        # ~ -5.2). Swing low is the LAST close in the reference window (101),
        # and the candle that prints it has 14 consecutive losses behind it,
        # so RSI there is ~0.
        reference = []
        v = 200.0
        step = (200.0 - 101.0) / 19  # 19 steps to land at 101 on index 19
        for i in range(20):
            reference.append(round(200.0 - step * i, 4))
        # reference[-1] == 101.0 -> swing_low = 101.0

        # Recent window: 3 gain candles (bullish recovery), then a single
        # large dip to a new low at 100.0 (~1% below swing_low=101), then
        # snap back to 101.5 (inside the offset band).
        # 14-RSI at the new-low candle includes ~13 losses (from descent) + 3
        # gains + 1 big loss — still mostly losses, BUT the 3 gains pump it
        # well above ~0.
        recent = [105.0, 110.0, 115.0, 100.0, 101.5]

        return s, pad + reference + recent

    def test_bullish_sweep_high_confidence(self):
        s, closes = self._bullish_sweep_series()
        sig = s.evaluate("BTC_USDT", closes)
        assert sig.action == "BUY"
        assert sig.confidence in ("HIGH", "MEDIUM")  # always at least medium
        # We engineered this for HIGH:
        assert sig.confidence == "HIGH", (
            f"expected HIGH-conf bullish sweep, got {sig.confidence}: {sig.reason}"
        )
        assert "sweep" in sig.reason.lower()

    def test_bullish_sweep_medium_when_no_divergence(self):
        # Force a configuration where divergence cannot occur: pad with strong
        # uptrend so RSI at the reference low is already very HIGH, and the
        # sweep candle's RSI cannot exceed it.
        s = _strategy(
            swing_lookback=20,
            sweep_within=5,
            min_breach_pct=0.5,
            max_close_offset_pct=0.5,
            rsi_period=14,
        )
        # Strong uptrend pad: RSI at the end of this is very high.
        pad = [100.0 + i for i in range(s.warmup)]      # last value: 100 + warmup-1
        ref_base = pad[-1] + 50.0
        # Reference window: floor sits at ref_base.
        reference = [ref_base + (0.2 if i % 2 else 0.0) for i in range(20)]
        # Recent window: dip below ref_base then bounce back.
        sweep_low = ref_base * (1.0 - 0.02)              # ~2% breach
        recent = [ref_base + 0.5, ref_base + 0.5, ref_base + 0.5, sweep_low, ref_base]
        closes = pad + reference + recent

        sig = s.evaluate("BTC_USDT", closes)
        assert sig.action == "BUY"
        # We expect MEDIUM here — RSI after a long uptrend is much higher at the
        # reference low than at the sweep low (which prints a big drop).
        assert sig.confidence == "MEDIUM", (
            f"expected MEDIUM (no divergence), got {sig.confidence}: {sig.reason}"
        )


class TestBearishSweep:
    """Sweep above swing_high followed by snap-back below the offset threshold."""

    def _bearish_sweep_series(self):
        """
        Mirror of the bullish HIGH setup. Long flat pad, then an ascending
        reference window (swing_high at its END, with 14 consecutive gains
        behind it -> very HIGH RSI), then a recent window with 3 small
        decline candles followed by one big spike to a NEW high (RSI at that
        new high is dragged down by the recent losses, so it's LOWER than at
        the ref high). Final close snaps back inside the offset band.

        Crucially, the recent window's MINIMUM must stay above swing_low to
        avoid accidentally tripping the bullish-sweep branch.
        """
        s = _strategy(
            swing_lookback=20,
            sweep_within=5,
            min_breach_pct=0.5,
            max_close_offset_pct=0.5,
            rsi_period=14,
        )

        pad = [100.0] * s.warmup

        # Reference window: ascending from 100 -> 199. swing_low = 100,
        # swing_high = 199 (at index 19, with 14 consecutive gains -> RSI=100).
        reference = []
        step = (199.0 - 100.0) / 19
        for i in range(20):
            reference.append(round(100.0 + step * i, 4))

        # Recent window: 3 small declines (keeping min well above swing_low=100
        # to avoid the bullish-sweep branch), then a spike to a NEW HIGH at
        # 200.0 (~0.5% above swing_high=199), then snap back to 198.5
        # (within the 0.5% offset band).
        # min(recent) = 193.0  >>  swing_low * (1 - 0.005) = 99.5  -> safe.
        recent = [195.0, 194.0, 193.0, 200.0, 198.5]

        return s, pad + reference + recent

    def test_bearish_sweep_high_confidence(self):
        s, closes = self._bearish_sweep_series()
        sig = s.evaluate("BTC_USDT", closes)
        assert sig.action == "SELL"
        assert sig.confidence == "HIGH", (
            f"expected HIGH-conf bearish sweep, got {sig.confidence}: {sig.reason}"
        )
        assert "sweep" in sig.reason.lower()

    def test_bearish_sweep_medium_when_no_divergence(self):
        # Strong downtrend pad -> RSI at the reference high is already very LOW,
        # so the sweep candle's RSI cannot fall below it -> no divergence.
        s = _strategy(
            swing_lookback=20,
            sweep_within=5,
            min_breach_pct=0.5,
            max_close_offset_pct=0.5,
            rsi_period=14,
        )
        pad = [200.0 - i for i in range(s.warmup)]
        ref_base = pad[-1] - 50.0
        reference = [ref_base - (0.2 if i % 2 else 0.0) for i in range(20)]
        sweep_high = ref_base * (1.0 + 0.02)
        recent = [ref_base - 0.5, ref_base - 0.5, ref_base - 0.5, sweep_high, ref_base]
        closes = pad + reference + recent

        sig = s.evaluate("BTC_USDT", closes)
        assert sig.action == "SELL"
        assert sig.confidence == "MEDIUM", (
            f"expected MEDIUM (no divergence), got {sig.confidence}: {sig.reason}"
        )


class TestNoSweep:
    def test_breach_but_close_still_outside_holds(self):
        """Dip below swing_low but never recover -> NOT a sweep, must HOLD."""
        s = _strategy(
            swing_lookback=20,
            sweep_within=5,
            min_breach_pct=0.5,
            max_close_offset_pct=0.5,
        )
        pad = _baseline_padding(s.warmup, 100.0)
        reference = [100.0] * 20
        # Recent: dip to 95 and STAY there. Close at 95 is far below 100.
        recent = [100.0, 100.0, 95.0, 94.5, 94.0]
        sig = s.evaluate("BTC_USDT", pad + reference + recent)
        assert sig.action == "HOLD"

    def test_close_back_inside_but_no_breach_holds(self):
        """Tiny dip that doesn't meet min_breach_pct -> HOLD."""
        s = _strategy(
            swing_lookback=20,
            sweep_within=5,
            min_breach_pct=1.0,      # require a full 1% breach
            max_close_offset_pct=0.5,
        )
        pad = _baseline_padding(s.warmup, 100.0)
        reference = [100.0] * 20
        # 0.2% dip is far less than the 1% threshold.
        recent = [100.0, 100.0, 100.0, 99.8, 100.0]
        sig = s.evaluate("BTC_USDT", pad + reference + recent)
        assert sig.action == "HOLD"
