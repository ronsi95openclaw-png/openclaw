"""Random parameter search with typed param space."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List

from research.types import BacktestResult, Candle, OptimizationResult
from research.optimization._extract import extract_metric

logger = logging.getLogger(__name__)


async def random_search(
    backtest_fn: Callable[[List[Candle], Dict[str, Any]], Awaitable[BacktestResult]],
    candles: List[Candle],
    param_space: Dict[str, Any],
    n_trials: int = 100,
    metric: str = "sharpe_ratio",
    seed: int = 42,
) -> List[OptimizationResult]:
    """Random parameter sampling.

    Draws ``n_trials`` random parameter combinations from ``param_space`` and
    evaluates each using ``backtest_fn``.

    Supported param types in ``param_space``:
      * ``("int",   lo, hi)``    — uniform integer in ``[lo, hi]`` inclusive.
      * ``("float", lo, hi)``    — uniform float in ``[lo, hi]``.
      * ``("choice", [v1, v2, ...])`` — random choice from the list.

    Example::

        param_space = {
            "ema_fast":   ("int",   5, 20),
            "ema_slow":   ("int",   20, 100),
            "rsi_period": ("int",   10, 20),
            "sl_pct":     ("float", 0.5, 3.0),
        }

    Args:
        backtest_fn: Async callable ``fn(candles, params) -> BacktestResult``.
        candles:     Candle series.
        param_space: Parameter specification dict.
        n_trials:    Number of random trials.
        metric:      Metric field name to optimise.
        seed:        Random seed.

    Returns:
        All results sorted by ``metric`` descending.
    """
    rng = random.Random(seed)
    results: List[OptimizationResult] = []

    logger.info("random_search: %d trials  metric=%s", n_trials, metric)

    for trial_idx in range(n_trials):
        params = _sample_params(param_space, rng)
        try:
            result = await backtest_fn(candles, params)
            score = extract_metric(result, metric)
            metrics_obj = result.metadata.get("metrics")
            results.append(
                OptimizationResult(
                    strategy=result.strategy,
                    symbol=result.symbol,
                    params=params,
                    score=score,
                    metric=metric,
                    metrics=metrics_obj,
                    timestamp=datetime.now(tz=timezone.utc),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "random_search: trial %d failed for %s: %s", trial_idx, params, exc
            )

    results.sort(key=lambda r: r.score, reverse=True)
    return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sample_params(
    param_space: Dict[str, Any], rng: random.Random
) -> Dict[str, Any]:
    """Draw one random parameter combination from param_space.

    Args:
        param_space: Typed parameter space dict.
        rng:         Seeded random instance.

    Returns:
        Dict of sampled parameter values.
    """
    params: Dict[str, Any] = {}
    for key, spec in param_space.items():
        if isinstance(spec, list) and len(spec) >= 1:
            # Plain list — treat as a set of discrete choices
            if isinstance(spec[0], str) and spec[0] in ("int", "float", "choice"):
                kind = spec[0]
                if kind == "int" and len(spec) == 3:
                    params[key] = rng.randint(int(spec[1]), int(spec[2]))
                elif kind == "float" and len(spec) == 3:
                    params[key] = rng.uniform(float(spec[1]), float(spec[2]))
                elif kind == "choice" and len(spec) == 2:
                    params[key] = rng.choice(list(spec[1]))
                else:
                    params[key] = rng.choice(spec)
            else:
                # e.g. [1, 2, 3, 4, 5] → random choice
                params[key] = rng.choice(spec)
        elif isinstance(spec, tuple) and len(spec) >= 1:
            kind = spec[0]
            if kind == "int" and len(spec) == 3:
                params[key] = rng.randint(int(spec[1]), int(spec[2]))
            elif kind == "float" and len(spec) == 3:
                params[key] = rng.uniform(float(spec[1]), float(spec[2]))
            elif kind == "choice" and len(spec) == 2:
                params[key] = rng.choice(list(spec[1]))
            else:
                params[key] = spec
        else:
            # Fixed scalar value
            params[key] = spec
    return params
