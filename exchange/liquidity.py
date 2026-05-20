"""Liquidity monitoring and adverse condition detection.

Monitors candle-based liquidity signals and computes composite scores
for routing decisions.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from research.types import Candle


class LiquidityMonitor:
    """Monitors order book liquidity and detects adverse conditions.

    Signals:
    - spread expansion   (spread > 2× baseline)
    - thin book          (top-5 depth < threshold)
    - volatility shock   (price move > 3σ in 1 bar)
    - liquidity drought  (volume < 50% of average)
    """

    def __init__(self, baseline_window: int = 20) -> None:
        self._window = baseline_window
        self._candles: Deque[Candle] = deque(maxlen=baseline_window)
        # Baseline metrics (updated on each new candle)
        self._avg_volume: float  = 0.0
        self._avg_range:  float  = 0.0
        self._avg_spread_bps: float = 0.0   # estimated from (high-low)/mid

    # ── Feed ──────────────────────────────────────────────────────────────────

    def update(self, candle: Candle) -> None:
        """Feed latest candle to update liquidity state."""
        self._candles.append(candle)
        if self._candles:
            vols = [c.volume for c in self._candles]
            rngs = [c.range  for c in self._candles]
            self._avg_volume = sum(vols) / len(vols)
            self._avg_range  = sum(rngs) / len(rngs)

    # ── Individual signals ─────────────────────────────────────────────────────

    def is_spread_expanded(self, current_spread_bps: float) -> bool:
        """True when current spread is more than 2× the baseline average."""
        if self._avg_spread_bps <= 0:
            return False
        return current_spread_bps > 2.0 * self._avg_spread_bps

    def is_vol_shock(self, candle: Candle) -> bool:
        """True when this candle's range exceeds 3σ above average range."""
        if len(self._candles) < 3:
            return False
        ranges = [c.range for c in self._candles]
        avg_r  = sum(ranges) / len(ranges)
        if avg_r <= 0:
            return False
        variance = sum((r - avg_r) ** 2 for r in ranges) / len(ranges)
        sigma    = math.sqrt(variance)
        if sigma > 0:
            return candle.range > avg_r + 3.0 * sigma
        # All historical candles have identical range — any spike > 2× is a shock
        return candle.range > avg_r * 2.0

    def is_liquidity_drought(self, candle: Candle) -> bool:
        """True when candle volume is less than 50% of baseline average."""
        if self._avg_volume <= 0:
            return False
        return candle.volume < 0.50 * self._avg_volume

    def is_thin_book(
        self,
        top5_depth_usd: float,
        threshold_usd: float = 50_000,
    ) -> bool:
        """True when top-5 levels depth is below threshold."""
        return top5_depth_usd < threshold_usd

    # ── Composite score ────────────────────────────────────────────────────────

    def liquidity_score(self, candle: Candle) -> float:
        """0–1 composite. 1 = excellent liquidity, 0 = avoid trading.

        Penalises:
        - vol shock     (−0.35)
        - drought       (−0.30)
        - thin book     (−0.20, if detectable from candle alone)
        """
        score = 1.0

        if self.is_vol_shock(candle):
            score -= 0.35

        if self.is_liquidity_drought(candle):
            score -= 0.30

        # Proxy for thin book: very narrow range on low volume
        if self._avg_range > 0:
            range_ratio = candle.range / self._avg_range
            if range_ratio < 0.20:   # suspiciously quiet = potentially thin
                score -= 0.15

        return round(max(0.0, min(1.0, score)), 4)

    def trading_conditions(self, candle: Candle) -> Dict[str, Any]:
        """Full condition summary for routing decisions."""
        return {
            "vol_shock":         self.is_vol_shock(candle),
            "liquidity_drought": self.is_liquidity_drought(candle),
            "avg_volume":        round(self._avg_volume, 4),
            "candle_volume":     round(candle.volume, 4),
            "avg_range":         round(self._avg_range, 6),
            "candle_range":      round(candle.range, 6),
            "liquidity_score":   self.liquidity_score(candle),
            "candles_in_window": len(self._candles),
        }
