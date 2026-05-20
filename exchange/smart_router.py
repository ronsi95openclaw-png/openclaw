"""Smart order router — selects the best venue for each order.

AI SAFETY CONTRACT:
  SmartOrderRouter does NOT execute orders.
  It only selects the venue and computes execution parameters.
  The caller (trading bot / human) is responsible for submission.

Routing criteria (in priority order):
  1. Kill switch check       — rejects if active
  2. Liquidity conditions    — rejects if adverse
  3. Venue availability      — skips outaged venues
  4. Composite venue score   — routes to highest scoring
  5. Latency tie-breaking    — fastest venue among equal scorers
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from research.types import Candle

from exchange.execution_quality import ExecutionQualityTracker
from exchange.latency_tracker import LatencyTracker
from exchange.liquidity import LiquidityMonitor
from exchange.venue_scoring import VenueScoringEngine


@dataclass
class RoutingDecision:
    """Result of a routing request.

    ``allowed`` is False whenever the router blocks the order.
    Callers MUST check ``allowed`` before submitting.
    """

    venue: str
    allowed: bool
    reject_reason: str
    expected_slippage_bps: float
    expected_latency_ms: float
    venue_score: float
    liquidity_score: float


class SmartOrderRouter:
    """Routes orders to the best venue based on real-time scoring.

    Currently OpenClaw supports only BloFin, but this is designed for
    multi-venue routing when additional exchange adapters are added.

    AI SAFETY: SmartOrderRouter does NOT execute orders.
    It only selects the venue and computes execution parameters.
    """

    # Liquidity threshold below which orders are blocked
    _LIQUIDITY_BLOCK_THRESHOLD = 0.30

    def __init__(
        self,
        venues: List[str],
        quality_tracker: ExecutionQualityTracker,
        latency_tracker: LatencyTracker,
        liquidity_monitor: LiquidityMonitor,
        venue_scorer: VenueScoringEngine,
    ) -> None:
        self._venues = list(venues)
        self._quality = quality_tracker
        self._latency = latency_tracker
        self._liquidity = liquidity_monitor
        self._scorer = venue_scorer

        # AI safety: kill switch flag.
        # Set via set_kill_switch(True) — simple bool, no DB dependency.
        self._kill_switch: bool = False

        # Per-venue outage flags (set externally or via mark_venue_outage)
        self._outaged: Dict[str, bool] = {}

    # ── Kill switch ───────────────────────────────────────────────────────────

    def set_kill_switch(self, active: bool) -> None:
        """Enable or disable the global kill switch."""
        self._kill_switch = active

    @property
    def kill_switch_active(self) -> bool:
        """True if the kill switch is currently engaged."""
        return self._kill_switch

    # ── Venue management ──────────────────────────────────────────────────────

    def mark_venue_outage(self, venue: str) -> None:
        """Mark a venue as temporarily unavailable."""
        self._outaged[venue] = True
        self._scorer.mark_outage(venue)

    def clear_venue_outage(self, venue: str) -> None:
        """Clear an outage flag when a venue recovers."""
        self._outaged.pop(venue, None)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(
        self,
        symbol: str,
        side: str,
        size: float,
        current_candle: Candle,
    ) -> RoutingDecision:
        """Select venue and compute execution params.

        AI SAFETY: does NOT submit the order — caller decides.

        Returns
        -------
        RoutingDecision with ``allowed=False`` when any guard fires.
        """
        # ── Guard 1: Kill switch ──────────────────────────────────────────────
        if self._kill_switch:
            return RoutingDecision(
                venue="",
                allowed=False,
                reject_reason="kill_switch_active",
                expected_slippage_bps=0.0,
                expected_latency_ms=0.0,
                venue_score=0.0,
                liquidity_score=0.0,
            )

        # ── Guard 2: Liquidity check ──────────────────────────────────────────
        self._liquidity.update(current_candle)
        liq_score = self._liquidity.liquidity_score(current_candle)
        if liq_score < self._LIQUIDITY_BLOCK_THRESHOLD:
            return RoutingDecision(
                venue="",
                allowed=False,
                reject_reason=f"adverse_liquidity_score={liq_score:.3f}",
                expected_slippage_bps=0.0,
                expected_latency_ms=0.0,
                venue_score=0.0,
                liquidity_score=liq_score,
            )

        # ── Guard 3: Filter available venues ─────────────────────────────────
        available = [v for v in self._venues if not self._outaged.get(v, False)]
        if not available:
            return RoutingDecision(
                venue="",
                allowed=False,
                reject_reason="no_venues_available",
                expected_slippage_bps=0.0,
                expected_latency_ms=0.0,
                venue_score=0.0,
                liquidity_score=liq_score,
            )

        # ── Guard 4 & 5: Score venues, break ties by latency ─────────────────
        scored = [self._scorer.score_venue(v) for v in available]
        scored.sort(
            key=lambda vs: (vs.composite, -self._latency.get_latency(vs.venue)),
            reverse=True,
        )
        best = scored[0]

        # Expected execution parameters from quality tracker
        quality_summary = self._quality.summary()
        expected_slip = quality_summary.get("avg_slippage_bps", best.avg_slippage_bps)
        expected_lat = self._latency.get_latency(best.venue)
        if expected_lat >= 999.0:
            expected_lat = best.avg_latency_ms  # fall back to historical avg

        return RoutingDecision(
            venue=best.venue,
            allowed=True,
            reject_reason="",
            expected_slippage_bps=round(expected_slip, 4),
            expected_latency_ms=round(expected_lat, 2),
            venue_score=best.composite,
            liquidity_score=liq_score,
        )

    # ── Reporting ─────────────────────────────────────────────────────────────

    def report(self) -> Dict[str, Any]:
        """Current routing state and venue rankings."""
        ranked = self._scorer.ranked_venues()
        return {
            "kill_switch_active": self._kill_switch,
            "venues_total":       len(self._venues),
            "venues_available":   len([v for v in self._venues if not self._outaged.get(v, False)]),
            "outaged_venues":     [v for v, out in self._outaged.items() if out],
            "venue_rankings": [
                {
                    "venue":             vs.venue,
                    "composite":         vs.composite,
                    "reliability":       vs.reliability,
                    "execution":         vs.execution,
                    "avg_slippage_bps":  vs.avg_slippage_bps,
                    "avg_latency_ms":    vs.avg_latency_ms,
                }
                for vs in ranked
            ],
            "quality_summary": self._quality.summary(),
            "latency_percentiles": self._quality.latency_percentiles(),
        }
