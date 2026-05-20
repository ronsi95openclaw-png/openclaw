"""Venue scoring engine for smart order routing.

Computes composite venue scores from reliability, liquidity, execution
quality, and latency dimensions.  Scores are persisted to disk so they
survive process restarts.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from research.types import ExecutionRecord, VenueScore


class VenueScoringEngine:
    """Computes composite venue scores for smart order routing.

    Score components (configurable weights):
    - reliability: uptime / rejection rate
    - liquidity: spread + book depth (proxied from slippage)
    - execution: slippage + fill rate
    - latency: p95 latency

    Persists scores to data/venue_scores.json (updated on each fill).
    """

    # Default score for an unseen venue (neutral starting point)
    _DEFAULT_SCORE = 0.70

    def __init__(
        self,
        reliability_weight: float = 0.30,
        liquidity_weight: float = 0.30,
        execution_weight: float = 0.25,
        latency_weight: float = 0.15,
        persist_path: str = "data/venue_scores.json",
    ) -> None:
        if abs(reliability_weight + liquidity_weight + execution_weight + latency_weight - 1.0) > 1e-6:
            raise ValueError("Weights must sum to 1.0")
        self._w_rel = reliability_weight
        self._w_liq = liquidity_weight
        self._w_exe = execution_weight
        self._w_lat = latency_weight

        self._path = Path(persist_path)
        # Per-venue running statistics
        self._stats: Dict[str, Dict[str, Any]] = {}
        self._outages: Dict[str, datetime] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            self._stats = raw.get("stats", {})
        except Exception:
            self._stats = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"stats": self._stats}, indent=2, default=str)
        )
        tmp.replace(self._path)

    # ── Update ────────────────────────────────────────────────────────────────

    def update_from_fill(self, record: ExecutionRecord) -> None:
        """Update scores from a completed fill."""
        v = record.venue
        if v not in self._stats:
            self._stats[v] = {
                "fill_count":    0,
                "reject_count":  0,
                "total_slippage_bps": 0.0,
                "total_latency_ms":  0.0,
                "adverse_count":  0,
                "outage_count_24h": 0,
            }

        s = self._stats[v]
        s["fill_count"] += 1

        if record.fill_status == "rejected":
            s["reject_count"] += 1
        else:
            s["total_slippage_bps"] += record.slippage_bps
            s["total_latency_ms"]   += record.latency_ms
            if record.adverse_selection > 0:
                s["adverse_count"] += 1

        s["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._save()

    # ── Scoring ───────────────────────────────────────────────────────────────

    def score_venue(self, venue: str) -> VenueScore:
        """Get current score for a venue."""
        s = self._stats.get(venue, {})
        if not s:
            return self._default_venue_score(venue)

        total   = s.get("fill_count", 0)
        rejects = s.get("reject_count", 0)
        filled  = max(1, total - rejects)

        rej_rate     = rejects / total if total > 0 else 0.0
        avg_slip     = s.get("total_slippage_bps", 0.0) / filled
        avg_lat      = s.get("total_latency_ms", 0.0) / filled
        adv_rate     = s.get("adverse_count", 0) / filled
        outage_count = s.get("outage_count_24h", 0)

        # Reliability: penalise rejections and outages
        reliability = max(0.0, 1.0 - rej_rate / 0.10 - outage_count * 0.05)

        # Liquidity proxy: low slippage → good liquidity
        liquidity = max(0.0, 1.0 - avg_slip / 20.0)

        # Execution: slippage + adverse selection
        execution = max(0.0, 1.0 - avg_slip / 20.0 - adv_rate * 0.3)

        # Latency: 0 ms = 1.0, 500 ms = 0.0
        latency_score = max(0.0, 1.0 - avg_lat / 500.0)

        # Temporary outage penalty: score drops to 0.1 for 5 minutes
        if venue in self._outages:
            elapsed = (datetime.now(timezone.utc) - self._outages[venue]).total_seconds()
            if elapsed < 300:
                reliability = min(reliability, 0.1)
            else:
                del self._outages[venue]

        composite = (
            self._w_rel * reliability
            + self._w_liq * liquidity
            + self._w_exe * execution
            + self._w_lat * latency_score
        )
        composite = round(min(1.0, max(0.0, composite)), 4)

        last_str = s.get("last_updated")
        last_dt: Optional[datetime] = None
        if last_str:
            try:
                last_dt = datetime.fromisoformat(last_str)
            except Exception:
                pass

        return VenueScore(
            venue=venue,
            reliability=round(reliability, 4),
            liquidity=round(liquidity, 4),
            execution=round(execution, 4),
            composite=composite,
            avg_slippage_bps=round(avg_slip, 4),
            avg_latency_ms=round(avg_lat, 4),
            rejection_rate=round(rej_rate, 4),
            outage_count_24h=outage_count,
            last_updated=last_dt,
        )

    def ranked_venues(self) -> List[VenueScore]:
        """All venues sorted by composite score (highest first)."""
        scores = [self.score_venue(v) for v in self._stats]
        scores.sort(key=lambda vs: vs.composite, reverse=True)
        return scores

    def best_venue(self) -> str:
        """Name of currently highest-scoring venue."""
        ranked = self.ranked_venues()
        return ranked[0].venue if ranked else "unknown"

    def mark_outage(self, venue: str) -> None:
        """Temporarily drop venue score (outage detected)."""
        self._outages[venue] = datetime.now(timezone.utc)
        if venue in self._stats:
            self._stats[venue]["outage_count_24h"] = (
                self._stats[venue].get("outage_count_24h", 0) + 1
            )
            self._save()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _default_venue_score(self, venue: str) -> VenueScore:
        """Return a neutral score for a venue with no history."""
        d = self._DEFAULT_SCORE
        return VenueScore(
            venue=venue,
            reliability=d,
            liquidity=d,
            execution=d,
            composite=d,
            avg_slippage_bps=5.0,
            avg_latency_ms=50.0,
            rejection_rate=0.0,
            outage_count_24h=0,
            last_updated=None,
        )
