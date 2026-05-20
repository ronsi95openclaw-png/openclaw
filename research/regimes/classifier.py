"""Market regime classifier — combines all regime signals into RegimeState.

Classes:
  RegimeClassifier — classify(candles) → RegimeState
                     classify_series(candles) → List[RegimeState]

Additional regime labels (beyond base 10):
  FUNDING_RATE_EXTREME  — anomalous funding rate (>0.1% or <-0.05% per 8h)
  LIQUIDATION_CASCADE   — rapid drop + volume spike + OI unwinding
  NEWS_SPIKE            — sudden >3σ candle with volume >5× avg, ADX not yet elevated
"""
from __future__ import annotations

import statistics
from typing import List, Optional

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

    # ── New regime detectors ──────────────────────────────────────────────────

    @staticmethod
    def detect_funding_rate_extreme(funding_rate_8h: float) -> bool:
        """Return True when funding is anomalously high or deeply negative.

        Thresholds:
          positive extreme : funding_rate_8h > +0.001  (> +0.1% per 8h)
          negative extreme : funding_rate_8h < -0.0005 (< -0.05% per 8h)
        """
        return funding_rate_8h > 0.001 or funding_rate_8h < -0.0005

    @staticmethod
    def detect_liquidation_cascade(
        candles: List[Candle],
        open_interest: Optional[List[float]] = None,
        price_drop_sigma: float = 2.0,
        volume_spike_multiplier: float = 3.0,
        lookback: int = 20,
    ) -> bool:
        """Return True when a liquidation cascade is underway.

        Conditions (all three must hold for the most recent bar):
        1. Price drop on the last bar exceeds ``price_drop_sigma`` standard
           deviations of recent bar returns (negative return only).
        2. Last bar volume exceeds ``volume_spike_multiplier`` × mean volume
           over the lookback window.
        3. Open interest is unwinding — last OI value is lower than the mean
           of the lookback window (if OI data provided).  When OI is not
           available conditions 1 + 2 are sufficient.
        """
        if len(candles) < lookback + 1:
            return False

        recent = candles[-(lookback + 1):]
        last = recent[-1]

        # 1. Price return on last bar
        prev_close = recent[-2].close
        if prev_close <= 0:
            return False
        bar_return = (last.close - prev_close) / prev_close
        if bar_return >= 0:
            # No price drop — not a cascade
            return False

        # Compute σ of returns over lookback
        returns = [
            (recent[i].close - recent[i - 1].close) / recent[i - 1].close
            for i in range(1, len(recent) - 1)  # exclude the last bar
            if recent[i - 1].close > 0
        ]
        if len(returns) < 3:
            return False
        try:
            ret_std = statistics.stdev(returns)
        except statistics.StatisticsError:
            return False
        if ret_std <= 0:
            return False
        drop_sigmas = abs(bar_return) / ret_std
        if drop_sigmas < price_drop_sigma:
            return False

        # 2. Volume spike
        avg_vol = statistics.mean(c.volume for c in recent[:-1])
        if avg_vol <= 0 or last.volume < volume_spike_multiplier * avg_vol:
            return False

        # 3. OI unwinding (optional)
        if open_interest is not None and len(open_interest) >= lookback + 1:
            oi_window = open_interest[-(lookback + 1):]
            avg_oi = statistics.mean(oi_window[:-1])
            if oi_window[-1] >= avg_oi:
                return False

        return True

    @staticmethod
    def detect_news_spike(
        candles: List[Candle],
        sigma_threshold: float = 3.0,
        volume_multiplier: float = 5.0,
        adx_ceiling: float = 25.0,
        lookback: int = 20,
    ) -> bool:
        """Return True when the most recent bar looks like a news-spike bar.

        Conditions:
        1. The bar's price move (|close - open| / open) exceeds
           ``sigma_threshold`` standard deviations of recent bar moves.
        2. The bar's volume exceeds ``volume_multiplier`` × mean recent volume.
        3. ADX is not yet elevated (< ``adx_ceiling``) — first bar of move.
        """
        if len(candles) < lookback + 1:
            return False

        recent = candles[-(lookback + 1):]
        last = recent[-1]

        # Bar move magnitude
        if last.open <= 0:
            return False
        last_move = abs(last.close - last.open) / last.open

        prior_moves = [
            abs(c.close - c.open) / c.open
            for c in recent[:-1]
            if c.open > 0
        ]
        if len(prior_moves) < 3:
            return False

        try:
            move_mean = statistics.mean(prior_moves)
            move_std = statistics.stdev(prior_moves)
        except statistics.StatisticsError:
            return False
        if move_std <= 0:
            return False

        sigmas = (last_move - move_mean) / move_std
        if sigmas < sigma_threshold:
            return False

        # Volume check
        avg_vol = statistics.mean(c.volume for c in recent[:-1])
        if avg_vol <= 0 or last.volume < volume_multiplier * avg_vol:
            return False

        # ADX check — must not yet be elevated
        try:
            adx_val = adx(candles, period=14)
        except Exception:
            adx_val = 0.0
        if adx_val >= adx_ceiling:
            return False

        return True

    def classify_extended(
        self,
        candles: List[Candle],
        funding_rate_8h: float = 0.0,
        open_interest: Optional[List[float]] = None,
    ) -> RegimeState:
        """Full classify with funding, liquidation, and news-spike detection.

        Returns a plain ``RegimeState`` with the label overridden to one of
        the three new labels when conditions are met.  The new flags are
        surfaced through the label only so the base dataclass is unchanged;
        callers that need the boolean flags should use
        ``ExtendedRegimeState`` via the extended_state module.

        Priority additions (inserted before PANIC in the priority chain):
          FUNDING_RATE_EXTREME > LIQUIDATION_CASCADE > NEWS_SPIKE
        then falls through to the standard label logic.
        """
        base = self.classify(candles)

        # Funding extreme overrides at highest priority (after panic)
        if not base.panic_conditions:
            if self.detect_funding_rate_extreme(funding_rate_8h):
                base.label = "FUNDING_RATE_EXTREME"
                return base

        # Liquidation cascade
        if not base.panic_conditions and base.label not in ("FUNDING_RATE_EXTREME",):
            if self.detect_liquidation_cascade(candles, open_interest):
                base.label = "LIQUIDATION_CASCADE"
                return base

        # News spike
        if base.label not in ("PANIC", "FUNDING_RATE_EXTREME", "LIQUIDATION_CASCADE"):
            if self.detect_news_spike(candles):
                base.label = "NEWS_SPIKE"
                return base

        return base
