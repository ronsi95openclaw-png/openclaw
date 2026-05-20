"""Exhaustive grid search over all parameter combinations."""
from __future__ import annotations

import asyncio
import itertools
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List

from research.types import BacktestResult, Candle, OptimizationResult
from research.optimization._extract import extract_metric

logger = logging.getLogger(__name__)


async def grid_search(
    backtest_fn: Callable[[List[Candle], Dict[str, Any]], Awaitable[BacktestResult]],
    candles: List[Candle],
    param_grid: Dict[str, List[Any]],
    metric: str = "sharpe_ratio",
    n_jobs: int = 4,
) -> List[OptimizationResult]:
    """Exhaustive grid search over all parameter combinations.

    Evaluates every combination in ``param_grid`` using the provided async
    backtest function.  Up to ``n_jobs`` evaluations run concurrently.

    Args:
        backtest_fn: Async callable ``fn(candles, params) -> BacktestResult``.
        candles:     Candle series to pass to the backtest.
        param_grid:  Dict mapping parameter name → list of candidate values.
                     Example: ``{"ema_fast": [5, 9, 12], "ema_slow": [21, 34, 50]}``.
        metric:      Name of the ``PerformanceMetrics`` field to optimise.
        n_jobs:      Maximum number of concurrent async evaluations.

    Returns:
        All ``OptimizationResult`` objects sorted by ``metric`` descending.
    """
    keys = list(param_grid.keys())
    value_lists = [param_grid[k] for k in keys]
    combinations = list(itertools.product(*value_lists))

    logger.info(
        "grid_search: %d combinations  metric=%s", len(combinations), metric
    )

    results: List[OptimizationResult] = []
    semaphore = asyncio.Semaphore(n_jobs)

    async def _evaluate(combo: tuple) -> OptimizationResult | None:
        params: Dict[str, Any] = dict(zip(keys, combo))
        async with semaphore:
            try:
                result = await backtest_fn(candles, params)
                score = extract_metric(result, metric)
                metrics_obj = result.metadata.get("metrics")
                return OptimizationResult(
                    strategy=result.strategy,
                    symbol=result.symbol,
                    params=params,
                    score=score,
                    metric=metric,
                    metrics=metrics_obj,
                    timestamp=datetime.now(tz=timezone.utc),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("grid_search: eval failed for %s: %s", params, exc)
                return None

    tasks = [_evaluate(combo) for combo in combinations]
    raw = await asyncio.gather(*tasks)

    for r in raw:
        if r is not None:
            results.append(r)

    results.sort(key=lambda r: r.score, reverse=True)
    return results
