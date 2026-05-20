"""Market regime classifier — combines all regime signals into RegimeState.

Classes:
  RegimeClassifier — classify(candles) → RegimeState
                     classify_series(candles) → List[RegimeState]
"""
from __future__ import annotations

from typing import List

from research.types import Candle, RegimeState
from research.regimes.volatility import (
    relative_atr,
    bollinger_width,
    vol_regime,
)
from research.regimes.trend import (
    adx,
    trend_strength,
    trend_direction,
    is_trending,
)
from research.regimes.momentum import (
    rsi,
    momentum_score,
    is_momentum_dominant,
    is_mean_reverting,
)
from research.regimes.ranging import is_ranging
from research.regimes.market_structure import (
    liquidity_drought,
    panic_conditions,
    higher_timeframe_trend,
)


class RegimeClassifier:
    """Classifies current market regime from a candle series.

    Combines volatility, trend, momentum, ranging, and structure signals
    into a single RegimeState.

    All computation is pure Python + optional numpy.  No external state.

    Usage::

        clf = RegimeClassifier()
        state = clf.classify(candles)
        print(state.label, state.regime_score)
    """

    def __init__(
        self,
        adx_period: int = 14,
        atr_period: int = 14,
        rsi_period: int = 14,
        bb_period: int = 20,
        vol_lookback: int = 20,
        trend_adx_threshold: float = 25.0,
        ranging_adx_threshold: float = 20.0,
        momentum_threshold: float = 0.65,
    ) -> None:
        self.adx_period             = adx_period
        self.atr_period             = atr_period
        self.rsi_period             = rsi_period
        self.bb_period              = bb_period
        self.vol_lookback           = vol_lookback
        self.trend_adx_threshold    = trend_adx_threshold
        self.ranging_adx_threshold  = ranging_adx_threshold
        self.momentum_threshold     = momentum_threshold

    # ── public API ────────────────────────────────────────────────────────────

    def classify(self, candles: List[Candle]) -> RegimeState:
        """Classify market regime from recent candles.

        Returns RegimeState with:
        - trending, ranging, vol_expanding, vol_compressing,
          momentum_dominant, mean_reverting, liquidity_drought, panic_conditions
        - regime_score: 0–1 composite confidence
        - label: one of:
            "TRENDING_BULL", "TRENDING_BEAR", "RANGING",
            "VOL_EXPANSION", "VOL_COMPRESSION",
            "MOMENTUM_BULL", "MEAN_REVERTING",
            "LIQUIDITY_DROUGHT", "PANIC", "UNKNOWN"
        - adx, atr_ratio, bb_width_pct, rsi
        """
        # Graceful degradation for small datasets
        min_useful = max(self.adx_period * 2 + 1, self.bb_period + 1,
                         self.rsi_period + 1, 30)
        if len(candles) < min_useful:
            return self._empty_state()

        closes = [c.close for c in candles]

        # ── Raw indicator values ──────────────────────────────────────────────
        adx_val    = adx(candles, self.adx_period)
        atr_ratio  = relative_atr(candles, short_period=5, long_period=self.vol_lookback)
        bb_w_pct   = bollinger_width(closes, period=self.bb_period)
        rsi_val    = rsi(closes, self.rsi_period)
        m_score    = momentum_score(closes)
        t_strength = trend_strength(candles, self.adx_period)

        # ── Boolean flags ─────────────────────────────────────────────────────
        trending_flag    = is_trending(candles, self.trend_adx_threshold)
        ranging_flag     = is_ranging(candles, self.ranging_adx_threshold)
        vol_exp, vol_cmp = vol_regime(candles)
        mom_dominant     = is_momentum_dominant(closes, self.momentum_threshold)
        mean_rev         = is_mean_reverting(closes)
        liq_drought      = liquidity_drought(candles)
        panic_flag       = panic_conditions(candles)

        # ── Composite regime score ────────────────────────────────────────────
        regime_score = self._compute_score(
            trending=trending_flag,
            ranging=ranging_flag,
            vol_expanding=vol_exp,
            vol_compressing=vol_cmp,
            momentum_dominant=mom_dominant,
            mean_reverting=mean_rev,
            liquidity_drought=liq_drought,
            panic_conditions=panic_flag,
            trend_strength_val=t_strength,
            atr_ratio=atr_ratio,
        )

        state = RegimeState(
            trending=trending_flag,
            ranging=ranging_flag,
            vol_expanding=vol_exp,
            vol_compressing=vol_cmp,
            momentum_dominant=mom_dominant,
            mean_reverting=mean_rev,
            liquidity_drought=liq_drought,
            panic_conditions=panic_flag,
            regime_score=round(regime_score, 4),
            label="UNKNOWN",        # filled below
            adx=round(adx_val, 2),
            atr_ratio=round(atr_ratio, 4),
            bb_width_pct=round(bb_w_pct, 4),
            rsi=round(rsi_val, 2),
        )

        state.label = self._assign_label(state, closes)
        return state

    def classify_series(
        self,
        candles: List[Candle],
        min_bars: int = 50,
    ) -> List[RegimeState]:
        """Classify regime at each bar using a rolling window.

        Parameters
        ----------
        candles:
            Full candle series.
        min_bars:
            Minimum rolling window size.  The window grows from ``min_bars``
            to ``len(candles)``, i.e., we use all historical data at each step
            (expanding window).

        Returns a list of the same length as ``candles``, where early bars
        (before ``min_bars``) contain empty/UNKNOWN states.
        """
        results: List[RegimeState] = []
        for i in range(len(candles)):
            if i < min_bars - 1:
                results.append(self._empty_state())
            else:
                window = candles[: i + 1]
                results.append(self.classify(window))
        return results

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _assign_label(self, state: RegimeState, closes: List[float]) -> str:
        """Priority-based label assignment.

        Priority order (highest first):
        1. PANIC           — overrides everything
        2. LIQUIDITY_DROUGHT
        3. VOL_EXPANSION   — before trend check because breakouts start here
        4. TRENDING_BULL / TRENDING_BEAR — strong directional
        5. MOMENTUM_BULL   — momentum without full trend confirmation
        6. MEAN_REVERTING  — RSI extreme
        7. RANGING         — low ADX, oscillating
        8. VOL_COMPRESSION — quiet coiling
        9. UNKNOWN
        """
        if state.panic_conditions:
            return "PANIC"
        if state.liquidity_drought:
            return "LIQUIDITY_DROUGHT"
        if state.vol_expanding:
            return "VOL_EXPANSION"
        if state.trending:
            direction = trend_direction(closes)
            if direction == "up":
                return "TRENDING_BULL"
            if direction == "down":
                return "TRENDING_BEAR"
            # ADX says trending but EMAs are mixed — call it bullish if last close > sma
            return "TRENDING_BULL" if closes[-1] >= closes[0] else "TRENDING_BEAR"
        if state.momentum_dominant:
            return "MOMENTUM_BULL"
        if state.mean_reverting:
            return "MEAN_REVERTING"
        if state.ranging:
            return "RANGING"
        if state.vol_compressing:
            return "VOL_COMPRESSION"
        return "UNKNOWN"

    def _compute_score(
        self,
        trending: bool,
        ranging: bool,
        vol_expanding: bool,
        vol_compressing: bool,
        momentum_dominant: bool,
        mean_reverting: bool,
        liquidity_drought: bool,
        panic_conditions: bool,
        trend_strength_val: float,
        atr_ratio: float,
    ) -> float:
        """Composite confidence score 0–1.

        Higher = more confident about the dominant regime.

        Scoring logic:
        - Panic / liquidity events are always high confidence (0.9)
        - Trending: confidence scales with trend_strength
        - Vol expansion: scales with atr_ratio
        - Ranging: moderate confidence when confirmed
        - Mixed / UNKNOWN: low score (~0.2)
        """
        if panic_conditions:
            return 0.95
        if liquidity_drought:
            return 0.85
        if vol_expanding:
            # Scale with atr_ratio capped at 3×
            return min(0.90, 0.60 + (atr_ratio - 1.30) * 0.15)
        if trending:
            # trend_strength is 0–1; map to 0.5–0.95
            return 0.50 + trend_strength_val * 0.45
        if momentum_dominant:
            return 0.65
        if mean_reverting:
            return 0.70
        if ranging:
            return 0.60
        if vol_compressing:
            return min(0.65, 0.40 + (1.0 - atr_ratio) * 0.30)
        # No dominant regime
        return 0.20

    def _empty_state(self) -> RegimeState:
        """Return a neutral/unknown RegimeState (used when data is insufficient)."""
        return RegimeState(
            trending=False,
            ranging=False,
            vol_expanding=False,
            vol_compressing=False,
            momentum_dominant=False,
            mean_reverting=False,
            liquidity_drought=False,
            panic_conditions=False,
            regime_score=0.0,
            label="UNKNOWN",
            adx=0.0,
            atr_ratio=1.0,
            bb_width_pct=0.0,
            rsi=50.0,
        )
