"""Unified optimization interface wrapping grid, random, and Bayesian search."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from research.types import Candle, OptimizationResult
from research.optimization.grid_search import grid_search
from research.optimization.random_search import random_search
from research.optimization.bayesian import bayesian_search
from research.optimization.parameter_store import ParameterStore

logger = logging.getLogger(__name__)

_INVALID_SCORE_THRESHOLD = -999.0


class ResearchOptimizer:
    """Unified optimization interface wrapping grid, random, and Bayesian search.

    Example::

        optimizer = ResearchOptimizer(
            backtest_fn=my_backtest,
            candles=candles,
            param_space={"ema_fast": ("int", 5, 20), "ema_slow": ("int", 20, 100)},
            symbol="BTC-USDT",
            strategy_name="ema_cross",
            metric="sharpe_ratio",
        )
        best = await optimizer.optimize(method="bayesian", n_trials=50)

    Args:
        backtest_fn:   Async callable ``fn(candles, params) -> BacktestResult``.
        candles:       Default candle series to use.
        param_space:   Default parameter space for the strategy.
        symbol:        Trading pair identifier (used for storage).
        strategy_name: Strategy name (used for storage).
        metric:        ``PerformanceMetrics`` field to maximise.
        seed:          Random seed.
    """

    def __init__(
        self,
        backtest_fn: Callable,
        candles: List[Candle],
        param_space: Dict[str, Any],
        symbol: str = "BTC-USDT",
        strategy_name: str = "unnamed",
        metric: str = "sharpe_ratio",
        seed: int = 42,
    ) -> None:
        self.backtest_fn = backtest_fn
        self.candles = candles
        self.param_space = param_space
        self.symbol = symbol
        self.strategy_name = strategy_name
        self.metric = metric
        self.seed = seed
        self._store = ParameterStore()

    # ── Search methods ────────────────────────────────────────────────────────

    async def run_grid_search(self, **kwargs: Any) -> List[OptimizationResult]:
        """Run exhaustive grid search.

        ``param_space`` must contain lists of values (``grid_search`` format).
        Converts typed-space specs to flat lists automatically.

        Returns:
            All results sorted by metric descending.
        """
        param_grid = _space_to_grid(self.param_space)
        results = await grid_search(
            backtest_fn=self.backtest_fn,
            candles=self.candles,
            param_grid=param_grid,
            metric=self.metric,
            **kwargs,
        )
        return self.rank_results(results)

    async def run_random_search(
        self, n_trials: int = 100, **kwargs: Any
    ) -> List[OptimizationResult]:
        """Run random parameter sampling.

        Args:
            n_trials: Number of random evaluations.

        Returns:
            All results sorted by metric descending.
        """
        results = await random_search(
            backtest_fn=self.backtest_fn,
            candles=self.candles,
            param_space=self.param_space,
            n_trials=n_trials,
            metric=self.metric,
            seed=self.seed,
            **kwargs,
        )
        return self.rank_results(results)

    async def run_bayesian_search(
        self, n_trials: int = 50, **kwargs: Any
    ) -> List[OptimizationResult]:
        """Run Bayesian optimisation (Optuna TPE).

        Args:
            n_trials: Total number of evaluations.

        Returns:
            All results sorted by metric descending.
        """
        results = await bayesian_search(
            backtest_fn=self.backtest_fn,
            candles=self.candles,
            param_space=self.param_space,
            n_trials=n_trials,
            metric=self.metric,
            seed=self.seed,
            **kwargs,
        )
        return self.rank_results(results)

    # ── High-level ────────────────────────────────────────────────────────────

    async def optimize(
        self, method: str = "bayesian", n_trials: int = 50
    ) -> OptimizationResult:
        """Run optimisation and return the best result.

        Args:
            method:   One of ``"bayesian"``, ``"random"``, ``"grid"``.
            n_trials: Number of evaluations (ignored for ``"grid"``).

        Returns:
            The ``OptimizationResult`` with the highest score.

        Raises:
            ValueError: If no valid results are found.
        """
        method = method.lower()
        if method == "bayesian":
            results = await self.run_bayesian_search(n_trials=n_trials)
        elif method == "random":
            results = await self.run_random_search(n_trials=n_trials)
        elif method == "grid":
            results = await self.run_grid_search()
        else:
            raise ValueError(
                f"Unknown optimization method '{method}'. "
                "Choose 'bayesian', 'random', or 'grid'."
            )

        ranked = self.rank_results(results)
        if not ranked:
            raise ValueError(
                f"No valid optimization results found for method='{method}'."
            )

        best = ranked[0]
        logger.info(
            "ResearchOptimizer: best %s=%.4f  params=%s",
            self.metric,
            best.score,
            best.params,
        )
        return best

    # ── Utility ───────────────────────────────────────────────────────────────

    def rank_results(
        self, results: List[OptimizationResult]
    ) -> List[OptimizationResult]:
        """Sort by metric and filter out degenerate results.

        Removes results where ``score < _INVALID_SCORE_THRESHOLD`` or is NaN.

        Args:
            results: Raw list of OptimizationResult objects.

        Returns:
            Filtered and sorted list, best first.
        """
        import math  # noqa: PLC0415

        valid = [
            r for r in results
            if not math.isnan(r.score) and r.score > _INVALID_SCORE_THRESHOLD
        ]
        valid.sort(key=lambda r: r.score, reverse=True)
        return valid

    def persist_best_result(self, result: OptimizationResult) -> None:
        """Save to ParameterStore using this optimizer's strategy_name and symbol.

        The result's strategy and symbol fields are overridden with the
        optimizer's configured ``strategy_name`` and ``symbol`` so that
        ``load_previous_best`` can retrieve it even if the backtest function
        returns different identifiers.

        Args:
            result: Best OptimizationResult to persist.
        """
        from dataclasses import replace as _replace  # noqa: PLC0415

        canonical = _replace(result, strategy=self.strategy_name, symbol=self.symbol)
        self._store.save(canonical)

    def load_previous_best(self) -> Optional[OptimizationResult]:
        """Load the best previously stored result for this strategy + symbol.

        Returns:
            ``OptimizationResult`` or ``None`` if no stored results exist.
        """
        return self._store.load_best(self.strategy_name, self.symbol)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _space_to_grid(param_space: Dict[str, Any]) -> Dict[str, List[Any]]:
    """Convert a typed param space to a flat grid for ``grid_search``.

    For ``"int"`` and ``"float"`` types, creates a small list of evenly
    spaced values (5 points by default) to keep the grid manageable.
    For ``"choice"`` types, uses the choices directly.

    Args:
        param_space: Typed parameter space dict.

    Returns:
        Dict mapping parameter name → list of candidate values.
    """
    import numpy as np  # noqa: PLC0415

    grid: Dict[str, List[Any]] = {}
    for key, spec in param_space.items():
        if isinstance(spec, (list, tuple)) and len(spec) >= 1:
            kind = spec[0]
            if kind == "int" and len(spec) == 3:
                lo, hi = int(spec[1]), int(spec[2])
                n_steps = min(5, hi - lo + 1)
                grid[key] = [int(v) for v in np.linspace(lo, hi, n_steps)]
            elif kind == "float" and len(spec) == 3:
                lo, hi = float(spec[1]), float(spec[2])
                grid[key] = list(np.linspace(lo, hi, 5))
            elif kind == "choice" and len(spec) == 2:
                grid[key] = list(spec[1])
            else:
                grid[key] = [spec]
        elif isinstance(spec, list):
            grid[key] = spec
        else:
            grid[key] = [spec]
    return grid
