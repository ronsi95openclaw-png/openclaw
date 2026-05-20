"""Per-venue API latency tracking using exponential moving average.

In-memory only — optimized for real-time routing decisions.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional


class LatencyTracker:
    """Tracks per-venue API latency using exponential moving average.

    Persists in-memory only (fast path for real-time routing decisions).
    """

    _UNKNOWN_LATENCY = 999.0

    def __init__(self, alpha: float = 0.1) -> None:
        """
        Args:
            alpha: EMA smoothing factor. Higher = more responsive to recent samples.
        """
        self._alpha = alpha
        self._ema: Dict[str, float] = {}

    def record(self, venue: str, latency_ms: float) -> None:
        """Update EMA latency for a venue with a new sample."""
        if venue not in self._ema:
            self._ema[venue] = latency_ms
        else:
            self._ema[venue] = (
                self._alpha * latency_ms
                + (1.0 - self._alpha) * self._ema[venue]
            )

    def get_latency(self, venue: str) -> float:
        """Current EMA latency for venue. Returns 999.0 if unknown."""
        return self._ema.get(venue, self._UNKNOWN_LATENCY)

    def rank_venues(self, venues: List[str]) -> List[str]:
        """Venues sorted by latency (fastest first)."""
        return sorted(venues, key=lambda v: self.get_latency(v))

    async def ping_venue(self, venue: str, url: str) -> float:
        """HTTP HEAD request to measure current latency. Returns ms.

        Records the result internally and returns it.
        Falls back to 999.0 on any error.
        """
        try:
            import aiohttp  # type: ignore[import]
            start = time.monotonic()
            async with aiohttp.ClientSession() as session:
                async with session.head(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    _ = resp.status
            elapsed_ms = (time.monotonic() - start) * 1000.0
            self.record(venue, elapsed_ms)
            return elapsed_ms
        except Exception:
            # On failure, record a high latency penalty so the venue ranks low
            penalty = 500.0
            self.record(venue, penalty)
            return penalty
