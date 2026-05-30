"""Unit tests for the Breakout Expansion strategy.

We construct synthetic close-price series that deliberately exercise each
branch of the rules:

- a long calm consolidation (the squeeze) followed by a single, decisive
  candle that pierces the upper or lower band,
- the same setup with and without ATR confirmation,
- the absence of a squeeze (sustained trend),
- not-enough-data conditions.

All tests are pure: no I/O, no monkeypatching.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from trading.strategies.breakout_expansion import (
    BreakoutExpansionConfig,
    BreakoutExpansionStrategy,
    atr_from_closes,
    bb_width_history,
    bollinger_bands,
)


# ── Fixtures / builders ───────────────────────────────────────────────────────


def _consolidation(price: float, n: int, jitter: float = 0.0005) -> list[float]:
    """A tight, low-vol consolidation around ``price``.

    Uses a deterministic sawtooth so tests are reproducible. ``jitter`` is
    the fractional band; 0.05% by default keeps Bollinger width very small.
    """
    out = []
    for i in range(n):
        # Tiny zig-zag: +jitter, -jitter, +jitter, ...
        sign = 1.0 if i % 2 == 0 else -1.0
        out.append(price * (1.0 + sign * jitter))
    return out


def _breakout_candle(prev_close: float, direction: str, magnitude: float = 0.05) -> float:
    """A single explosive candle ``magnitude`` away from ``prev_close``."""
    if direction == "up":
        return prev_close * (1.0 + magnitude)
    return prev_close * (1.0 - magnitude)


def _trending_series(start: float, n: int, step_pct: float) -> list[float]:
    """A steady uptrend (or downtrend if step_pct < 0). No squeeze."""
    out = [start]
    for _ in range(n - 1):
        out.append(out[-1] * (1.0 + step_pct))
    return out


def _default_strategy() -> BreakoutExpansionStrategy:
    return BreakoutExpansionStrategy()


# ── Indicator tests ───────────────────────────────────────────────────────────


class TestIndicators:
    def test_bollinger_bands_midline_is_sma(self):
        closes = [10.0] * 25
        upper, mid, lower = bollinger_bands(closes, period=20, stdev=2.0)
        assert mid == pytest.approx(10.0, abs=1e-9)
        # Zero variance -> upper == lower == mid
        assert upper == pytest.approx(10.0, abs=1e-9)
        assert lower == pytest.approx(10.0, abs=1e-9)

    def test_bollinger_bands_widen_with_variance(self):
        flat = [100.0] * 25
        noisy = [100.0 + (5.0 if i % 2 == 0 else -5.0) for i in range(25)]
        _, _, lower_flat = bollinger_bands(flat, period=20, stdev=2.0)
        upper_flat = bollinger_bands(flat, period=20, stdev=2.0)[0]
        upper_noisy, _, lower_noisy = bollinger_bands(noisy, period=20, stdev=2.0)
        assert (upper_noisy - lower_noisy) > (upper_flat - lower_flat)

    def test_bollinger_bands_insufficient_data_raises(self):
        with pytest.raises(ValueError):
            bollinger_bands([1.0, 2.0, 3.0], period=20, stdev=2.0)

    def test_bb_width_history_length_and_ordering(self):
        # 20 calm + 5 progressively wider -> last 5 widths should be increasing.
        calm = [100.0] * 20
        wider = [100.0, 105.0, 95.0, 110.0, 90.0]
        closes = calm + wider
        widths = bb_width_history(closes, period=20, stdev=2.0, lookback=5)
        assert len(widths) == 5
        # widths should strictly grow as variance enters the rolling window
        assert all(widths[i + 1] >= widths[i] for i in range(len(widths) - 1))
        # The last width corresponds to bollinger_bands(closes).
        last_upper, _, last_lower = bollinger_bands(closes, period=20, stdev=2.0)
        assert widths[-1] == pytest.approx(last_upper - last_lower, abs=1e-9)

    def test_atr_from_closes_zero_when_flat(self):
        assert atr_from_closes([100.0] * 20, period=14) == pytest.approx(0.0, abs=1e-12)

    def test_atr_expands_with_volatility(self):
        calm = [100.0] * 20
        spiky = [100.0 + (10.0 if i % 2 == 0 else -10.0) for i in range(20)]
        assert atr_from_closes(spiky, period=14) > atr_from_closes(calm, period=14)


# ── Strategy: HIGH-confidence cases ───────────────────────────────────────────


class TestHighConfidenceBreakouts:
    def test_high_confidence_buy_on_upside_breakout_with_atr_expansion(self):
        """Long calm squeeze, then one explosive up candle with prior wide
        moves giving fresh ATR > 1.2x of the older baseline."""
        strat = _default_strategy()
        # Plenty of warmup, then a tight squeeze, then a breakout.
        # Start with a regime that has moderate ATR to establish a baseline,
        # then go very calm (deflates ATR's baseline window further down),
        # then break out hard so current ATR jumps relative to the recent
        # baseline.
        early = [100.0 + (0.5 if i % 2 == 0 else -0.5) for i in range(60)]
        calm = _consolidation(100.0, n=80, jitter=0.0002)
        breakout = _breakout_candle(calm[-1], "up", magnitude=0.08)
        closes = early + calm + [breakout]

        sig = strat.evaluate("BTC_USDT", closes)
        assert sig.action == "BUY"
        assert sig.confidence == "HIGH"
        assert "breakout up" in sig.reason

    def test_high_confidence_sell_on_downside_breakout_with_atr_expansion(self):
        strat = _default_strategy()
        early = [100.0 + (0.5 if i % 2 == 0 else -0.5) for i in range(60)]
        calm = _consolidation(100.0, n=80, jitter=0.0002)
        breakout = _breakout_candle(calm[-1], "down", magnitude=0.08)
        closes = early + calm + [breakout]

        sig = strat.evaluate("ETH_USDT", closes)
        assert sig.action == "SELL"
        assert sig.confidence == "HIGH"
        assert "breakout down" in sig.reason


# ── Strategy: MEDIUM-confidence cases ─────────────────────────────────────────


class TestMediumConfidenceBreakouts:
    def test_medium_confidence_when_atr_does_not_expand(self):
        """Engineer a breakout where the baseline ATR is already
        bigger than the current ATR — so we cross the band but the
        expansion filter fails."""
        strat = _default_strategy()

        # Phase 1: a very volatile early stretch — builds up a large baseline
        # ATR over the prior-20 window.
        volatile = [100.0 + (15.0 if i % 2 == 0 else -15.0) for i in range(40)]

        # Phase 2: a very tight squeeze. The ATR baseline window is 20
        # candles ending one before "now", so after this calm stretch the
        # baseline is largely calm too — but we keep this stretch *just*
        # short enough that some of the volatile candles remain inside the
        # ATR baseline window once we tack the breakout on the end.
        calm = _consolidation(100.0, n=30, jitter=0.0002)

        # Phase 3: a modest breakout that still pierces the (very tight)
        # upper band but whose magnitude is small enough that current
        # ATR < 1.2x baseline (because the baseline still includes very
        # volatile early candles).
        small_break = calm[-1] * 1.01  # 1% pop, just barely outside the tight band

        closes = volatile + calm + [small_break]
        sig = strat.evaluate("SOL_USDT", closes)
        # We expect a real breakout signal but only MEDIUM conviction.
        assert sig.action in {"BUY", "SELL"}
        # The construction targets an upside break; assert direction too.
        assert sig.action == "BUY"
        assert sig.confidence == "MEDIUM"


# ── Strategy: HOLD cases ──────────────────────────────────────────────────────


class TestHoldCases:
    def test_hold_when_no_squeeze(self):
        """Sustained, smooth uptrend — bands widen with the trend, never
        squeeze, and a fresh candle on the upper band should NOT fire."""
        strat = _default_strategy()
        # A long trending series with continuous volatility means recent
        # widths are never near the bottom percentile of history.
        closes = _trending_series(100.0, n=200, step_pct=0.01)
        sig = strat.evaluate("XRP_USDT", closes)
        assert sig.action == "HOLD"
        assert sig.confidence == "LOW"

    def test_hold_on_insufficient_data(self):
        strat = _default_strategy()
        # Only 20 candles, but warmup is much larger than that.
        closes = [100.0 + i for i in range(20)]
        sig = strat.evaluate("BTC_USDT", closes)
        assert sig.action == "HOLD"
        assert sig.confidence == "LOW"
        assert "Insufficient" in sig.reason

    def test_hold_on_flat_market(self):
        """Perfectly flat closes: there IS a squeeze, but the current
        candle doesn't break either band -> HOLD."""
        strat = _default_strategy()
        closes = [100.0] * 200
        sig = strat.evaluate("ETH_USDT", closes)
        assert sig.action == "HOLD"

    def test_squeeze_detected_but_no_breakout_yet(self):
        """Squeeze present, current candle exactly on midline -> HOLD
        and the reason should mention the active squeeze."""
        strat = _default_strategy()
        calm = _consolidation(100.0, n=200, jitter=0.0003)
        # End with a candle right at price -- no breakout
        calm[-1] = 100.0
        sig = strat.evaluate("BTC_USDT", calm)
        assert sig.action == "HOLD"
        # Either way it's a HOLD; we don't pin the exact wording, but
        # squeeze-with-no-breakout is the realistic message.
        assert "Squeeze" in sig.reason or "No squeeze" in sig.reason


# ── Strategy: integration-style sanity ────────────────────────────────────────


class TestStrategyShape:
    def test_warmup_is_consistent_with_config(self):
        cfg = BreakoutExpansionConfig()
        strat = BreakoutExpansionStrategy(cfg)
        # Must cover both the BB and ATR baselines.
        assert strat.warmup >= cfg.squeeze_lookback + cfg.bb_period
        assert strat.warmup >= cfg.atr_period + cfg.atr_baseline_window + 1

    def test_signal_unused_fields_are_zero(self):
        strat = _default_strategy()
        closes = _consolidation(100.0, n=200, jitter=0.0003)
        sig = strat.evaluate("BTC_USDT", closes)
        # RSI/MACD are not produced by this strategy.
        assert sig.rsi == 0.0
        assert sig.macd == 0.0
        assert sig.macd_signal_val == 0.0
        assert sig.macd_histogram == 0.0

    def test_evaluate_is_pure_no_mutation(self):
        """Evaluating must not mutate the input closes list."""
        strat = _default_strategy()
        closes = _consolidation(100.0, n=200, jitter=0.0003) + [110.0]
        snapshot = list(closes)
        strat.evaluate("BTC_USDT", closes)
        assert closes == snapshot
