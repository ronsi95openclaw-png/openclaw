"""Bayesian optimization using Optuna's TPE sampler."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List

from research.types import BacktestResult, Candle, OptimizationResult
from research.optimization._extract import extract_metric

logger = logging.getLogger(__name__)


async def bayesian_search(
    backtest_fn: Callable[[List[Candle], Dict[str, Any]], Awaitable[BacktestResult]],
    candles: List[Candle],
    param_space: Dict[str, Any],
    n_trials: int = 50,
    metric: str = "sharpe_ratio",
    seed: int = 42,
    n_startup_trials: int = 10,
) -> List[OptimizationResult]:
    """Bayesian optimisation using Optuna TPE sampler.

    This is the preferred optimiser — significantly more efficient than grid or
    random search for high-dimensional parameter spaces.

    Param space spec follows the same format as ``random_search``:
      * ``("int",   lo, hi)``    — integer in ``[lo, hi]`` inclusive.
      * ``("float", lo, hi)``    — float in ``[lo, hi]``.
      * ``("choice", [v1, v2, ...])`` — categorical choice.

    Falls back to ``random_search`` if Optuna is not available.

    Args:
        backtest_fn:      Async callable ``fn(candles, params) -> BacktestResult``.
        candles:          Candle series.
        param_space:      Typed parameter space dict.
        n_trials:         Total number of evaluations.
        metric:           ``PerformanceMetrics`` field to maximise.
        seed:             Random seed for reproducibility.
        n_startup_trials: Random exploration trials before TPE kicks in.

    Returns:
        All results sorted by ``metric`` descending.
    """
    try:
        import optuna  # noqa: PLC0415
    except ImportError:
        logger.warning(
            "bayesian_search: optuna not available, falling back to random_search"
        )
        from research.optimization.random_search import random_search  # noqa: PLC0415

        return await random_search(
            backtest_fn=backtest_fn,
            candles=candles,
            param_space=param_space,
            n_trials=n_trials,
            metric=metric,
            seed=seed,
        )

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    sampler = optuna.samplers.TPESampler(
        n_startup_trials=n_startup_trials,
        seed=seed,
    )
    study = optuna.create_study(direction="maximize", sampler=sampler)

    results: List[OptimizationResult] = []

    for _ in range(n_trials):
        trial = study.ask()
        params = _suggest_params(trial, param_space)

        try:
            backtest_result = await backtest_fn(candles, params)
            score = extract_metric(backtest_result, metric)
            metrics_obj = backtest_result.metadata.get("metrics")

            study.tell(trial, score)

            results.append(
                OptimizationResult(
                    strategy=backtest_result.strategy,
                    symbol=backtest_result.symbol,
                    params=params,
                    score=score,
                    metric=metric,
                    metrics=metrics_obj,
                    timestamp=datetime.now(tz=timezone.utc),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "bayesian_search: trial failed for params %s: %s", params, exc
            )
            study.tell(trial, float("-inf"))

    results.sort(key=lambda r: r.score, reverse=True)
    return results


def _extract_metric(result: BacktestResult, metric: str) -> float:
    """Extract a scalar score from BacktestResult for optimization.

    Thin wrapper around the shared helper for backward compatibility.
    """
    return extract_metric(result, metric)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _suggest_params(trial: Any, param_space: Dict[str, Any]) -> Dict[str, Any]:
    """Ask Optuna to suggest parameter values for a trial.

    Args:
        trial:       An ``optuna.Trial`` object.
        param_space: Typed parameter space dict.

    Returns:
        Dict of suggested parameter values.
    """
    params: Dict[str, Any] = {}
    for key, spec in param_space.items():
        if isinstance(spec, list) and len(spec) >= 1:
            # Plain list → treat as categorical choices unless first element is a type tag
            if isinstance(spec[0], str) and spec[0] in ("int", "float", "choice"):
                kind = spec[0]
                if kind == "int" and len(spec) == 3:
                    params[key] = trial.suggest_int(key, int(spec[1]), int(spec[2]))
                elif kind == "float" and len(spec) == 3:
                    params[key] = trial.suggest_float(key, float(spec[1]), float(spec[2]))
                elif kind == "choice" and len(spec) == 2:
                    params[key] = trial.suggest_categorical(key, list(spec[1]))
                else:
                    params[key] = trial.suggest_categorical(key, spec)
            else:
                # e.g. [1, 2, 3, 4, 5] → categorical choice
                params[key] = trial.suggest_categorical(key, spec)
        elif isinstance(spec, tuple) and len(spec) >= 1:
            kind = spec[0]
            if kind == "int" and len(spec) == 3:
                params[key] = trial.suggest_int(key, int(spec[1]), int(spec[2]))
            elif kind == "float" and len(spec) == 3:
                params[key] = trial.suggest_float(key, float(spec[1]), float(spec[2]))
            elif kind == "choice" and len(spec) == 2:
                params[key] = trial.suggest_categorical(key, list(spec[1]))
            else:
                params[key] = spec
        else:
            # Fixed scalar
            params[key] = spec
    return params
