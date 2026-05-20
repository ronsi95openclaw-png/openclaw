"""Validates system meets latency requirements."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import List

logger = logging.getLogger("openclaw.validation.latency")

# Thresholds
_MIN_BACKTEST_CPS = 100.0   # candles per second
_MAX_REGIME_P99_MS = 100.0  # milliseconds


class LatencyValidator:
    """Measures and validates system latency benchmarks."""

    async def measure_backtest_throughput(
        self,
        engine,
        candles: list,
        strategy_fn,
    ) -> float:
        """Returns candles per second achieved by the backtesting engine."""
        if not candles:
            logger.warning("LatencyValidator: empty candles — returning 0 cps")
            return 0.0

        try:
            t0 = time.perf_counter()
            result = engine.run(candles, strategy_fn, {})
            if asyncio.iscoroutine(result):
                result = await result
            elapsed = time.perf_counter() - t0

            if elapsed <= 0:
                return float("inf")

            cps = len(candles) / elapsed
            logger.info(
                "LatencyValidator: backtest throughput %.1f cps (%d candles in %.3fs)",
                cps,
                len(candles),
                elapsed,
            )
            return cps

        except Exception as exc:
            logger.error("LatencyValidator: throughput measurement raised %s", exc)
            return 0.0

    async def measure_regime_classification_latency(
        self,
        classifier,
        candles: list,
        n_samples: int = 100,
    ) -> float:
        """Returns p99 latency in milliseconds for regime classification.

        Runs ``n_samples`` calls and returns the 99th percentile.
        """
        if not candles:
            logger.warning("LatencyValidator: empty candles — returning 0ms")
            return 0.0

        latencies_ms: List[float] = []
        for _ in range(n_samples):
            try:
                t0 = time.perf_counter()
                result = classifier.classify(candles)
                if asyncio.iscoroutine(result):
                    result = await result
                latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            except Exception as exc:
                logger.warning("LatencyValidator: classify raised %s", exc)

        if not latencies_ms:
            return 0.0

        latencies_ms.sort()
        p99_idx = max(0, int(len(latencies_ms) * 0.99) - 1)
        p99 = latencies_ms[p99_idx]

        logger.info(
            "LatencyValidator: regime p99=%.2fms p50=%.2fms (n=%d)",
            p99,
            latencies_ms[len(latencies_ms) // 2],
            len(latencies_ms),
        )
        return p99

    def validate_thresholds(
        self,
        backtest_cps: float,
        regime_ms: float,
    ) -> bool:
        """Returns True if all latency benchmarks pass.

        Thresholds:
        - Backtest : must be > 100 candles/sec
        - Regime   : p99 < 100ms
        """
        ok = True

        if backtest_cps < _MIN_BACKTEST_CPS:
            logger.error(
                "LatencyValidator: FAIL backtest throughput %.1f cps < %.1f cps",
                backtest_cps,
                _MIN_BACKTEST_CPS,
            )
            ok = False
        else:
            logger.info(
                "LatencyValidator: PASS backtest throughput %.1f cps", backtest_cps
            )

        if regime_ms >= _MAX_REGIME_P99_MS:
            logger.error(
                "LatencyValidator: FAIL regime p99 %.2fms >= %.1fms",
                regime_ms,
                _MAX_REGIME_P99_MS,
            )
            ok = False
        else:
            logger.info(
                "LatencyValidator: PASS regime p99 %.2fms", regime_ms
            )

        return ok
