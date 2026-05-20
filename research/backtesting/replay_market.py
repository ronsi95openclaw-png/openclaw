"""Historical market replay with configurable speed and latency simulation.

MarketReplayer iterates over a list of Candles, optionally introducing
real-time delays and simulated network latency between deliveries.
"""
from __future__ import annotations

import asyncio
import math
import random
from typing import Awaitable, Callable, List

from research.types import Candle


class MarketReplayer:
    """Replays historical candles with configurable speed and latency simulation.

    Designed for both instant offline backtesting (speed_multiplier=0.0) and
    live-latency-simulation scenarios where you want realistic delivery timing.
    """

    async def replay(
        self,
        candles: List[Candle],
        callback: Callable[[Candle, int], Awaitable[None]],
        speed_multiplier: float = 0.0,
        latency_mean_ms: float = 50.0,
        latency_std_ms: float = 10.0,
    ) -> None:
        """Replay candles calling callback(candle, bar_index) for each bar.

        Parameters
        ----------
        candles:          chronologically ordered OHLCV bars
        callback:         async callable that receives (candle, bar_index)
        speed_multiplier: 0.0 = instant (no delay between bars);
                          1.0 = real-time (sleeps for the actual bar interval);
                          >1.0 = faster than real-time
        latency_mean_ms:  mean of the simulated network latency distribution
        latency_std_ms:   std-dev of the simulated network latency distribution
        """
        prev_ts: int = candles[0].ts if candles else 0

        for idx, candle in enumerate(candles):
            # ── Latency simulation ─────────────────────────────────────────
            if speed_multiplier > 0.0 and latency_mean_ms > 0:
                lat_ms = self.simulate_latency(latency_mean_ms, latency_std_ms)
                await asyncio.sleep(lat_ms / 1_000.0)

            # ── Bar-interval delay ─────────────────────────────────────────
            if speed_multiplier > 0.0 and idx > 0:
                bar_gap_ms = max(0.0, candle.ts - prev_ts)
                delay_s = (bar_gap_ms / 1_000.0) / speed_multiplier
                if delay_s > 0:
                    await asyncio.sleep(delay_s)

            prev_ts = candle.ts
            await callback(candle, idx)

    def simulate_latency(self, mean_ms: float, std_ms: float) -> float:
        """Return simulated network latency in ms using a log-normal distribution.

        Log-normal is appropriate because network latencies are bounded below
        (cannot be negative) and occasionally exhibit large spikes.

        Parameters
        ----------
        mean_ms:  desired mean latency in milliseconds
        std_ms:   desired standard deviation of latency in milliseconds

        Returns
        -------
        Simulated latency value in milliseconds (always >= 0).
        """
        if mean_ms <= 0:
            return 0.0
        # Convert normal mean/std to log-normal parameters
        variance = std_ms ** 2
        mu_ln = math.log(mean_ms ** 2 / math.sqrt(mean_ms ** 2 + variance))
        sigma_ln = math.sqrt(math.log(1.0 + variance / (mean_ms ** 2)))
        latency = random.lognormvariate(mu_ln, sigma_ln)
        return max(0.0, latency)
