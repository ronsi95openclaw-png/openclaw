"""24-hour soak test simulation — runs backtesting engine for extended periods."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger("openclaw.validation.soak")

try:
    import psutil as _psutil
    _HAS_PSUTIL = True
except ImportError:
    _psutil = None  # type: ignore[assignment]
    _HAS_PSUTIL = False


@dataclass
class SoakTestResult:
    duration_seconds: float
    iterations: int
    memory_growth_mb: float
    max_memory_mb: float
    errors: int
    passed: bool
    failure_reason: str = ""


class SoakTestRunner:
    """Runs the backtesting engine under sustained load to detect memory leaks."""

    def __init__(
        self,
        duration_seconds: int = 3600,
        iterations_per_second: float = 10.0,
        max_memory_growth_mb: float = 100.0,
    ) -> None:
        self.duration_seconds = duration_seconds
        self.iterations_per_second = iterations_per_second
        self.max_memory_growth_mb = max_memory_growth_mb

    # ── memory helpers ────────────────────────────────────────────────────────

    def _current_rss_mb(self) -> float:
        """Return RSS memory of current process in MB, or 0 if psutil unavailable."""
        if not _HAS_PSUTIL:
            return 0.0
        try:
            proc = _psutil.Process()
            return proc.memory_info().rss / (1024 * 1024)
        except Exception:
            return 0.0

    # ── public API ────────────────────────────────────────────────────────────

    async def run(self, strategy_fn: Callable, candles: list) -> SoakTestResult:
        """Run sustained backtest loop, monitor memory growth.

        Parameters
        ----------
        strategy_fn:
            Callable that accepts a candle list and returns signals/trades.
            Must be synchronous or awaitable.
        candles:
            Candle data to feed on every iteration.

        Returns
        -------
        SoakTestResult with memory_samples, max_memory_mb, memory_growth_mb,
        passed, and failure_reason.
        """
        interval = 1.0 / max(self.iterations_per_second, 0.001)
        memory_samples: List[float] = []
        sample_interval = 60.0  # seconds between memory samples
        last_sample_ts = time.monotonic()

        start_ts = time.monotonic()
        start_mem = self._current_rss_mb()
        iterations = 0
        errors = 0
        max_mem = start_mem

        logger.info(
            "SoakTest started: duration=%ds ips=%.1f start_mem=%.1fMB",
            self.duration_seconds,
            self.iterations_per_second,
            start_mem,
        )

        while (time.monotonic() - start_ts) < self.duration_seconds:
            iter_start = time.monotonic()

            try:
                result = strategy_fn(candles)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                errors += 1
                logger.warning("SoakTest iteration error: %s", exc)

            iterations += 1

            # Memory sampling
            now = time.monotonic()
            if now - last_sample_ts >= sample_interval:
                mem = self._current_rss_mb()
                memory_samples.append(mem)
                max_mem = max(max_mem, mem)
                last_sample_ts = now
                logger.debug(
                    "SoakTest memory sample: %.1fMB (iter=%d)", mem, iterations
                )

            # Rate limiting
            elapsed = time.monotonic() - iter_start
            sleep_for = interval - elapsed
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

        end_mem = self._current_rss_mb()
        memory_growth_mb = max(0.0, end_mem - start_mem)
        max_mem = max(max_mem, end_mem)

        passed = True
        failure_reason = ""
        if memory_growth_mb > self.max_memory_growth_mb:
            passed = False
            failure_reason = (
                f"Memory grew {memory_growth_mb:.1f} MB, "
                f"limit={self.max_memory_growth_mb:.1f} MB"
            )

        result = SoakTestResult(
            duration_seconds=time.monotonic() - start_ts,
            iterations=iterations,
            memory_growth_mb=round(memory_growth_mb, 2),
            max_memory_mb=round(max_mem, 2),
            errors=errors,
            passed=passed,
            failure_reason=failure_reason,
        )

        logger.info(
            "SoakTest finished: passed=%s iterations=%d mem_growth=%.1fMB errors=%d",
            passed,
            iterations,
            memory_growth_mb,
            errors,
        )
        return result
