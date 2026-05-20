"""Injects realistic network/exchange latency into backtests."""
from __future__ import annotations

import math
import random


class LatencyInjector:
    """Injects realistic network/exchange latency into backtests.

    Base latency is sampled from ``Normal(base_ms, jitter_ms)``.
    With probability ``tail_probability`` the sample is multiplied by
    ``tail_multiplier`` to simulate tail-latency spikes.

    A fixed *seed* makes simulations fully deterministic.
    """

    def __init__(
        self,
        base_ms: float = 80.0,
        jitter_ms: float = 20.0,
        tail_probability: float = 0.05,
        tail_multiplier: float = 5.0,
        seed: int = 42,
    ) -> None:
        self.base_ms = base_ms
        self.jitter_ms = jitter_ms
        self.tail_probability = tail_probability
        self.tail_multiplier = tail_multiplier
        self._rng = random.Random(seed)

    def sample_latency_ms(self) -> float:
        """Sample one latency observation in milliseconds."""
        latency = self._rng.gauss(self.base_ms, self.jitter_ms)
        latency = max(latency, 0.0)
        if self._rng.random() < self.tail_probability:
            latency *= self.tail_multiplier
        return latency

    def bars_delayed(
        self,
        latency_ms: float,
        bar_duration_ms: int = 900_000,
    ) -> int:
        """Convert *latency_ms* to a bar-delay count.

        Returns 0 if latency is less than one full bar duration, 1 if it
        falls between one and two bars, and so on.
        """
        if bar_duration_ms <= 0:
            return 0
        return math.floor(latency_ms / bar_duration_ms)
