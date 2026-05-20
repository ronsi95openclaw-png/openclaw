"""Walk-forward engine: optimize in-sample, validate out-of-sample."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from research.types import (
    BacktestResult,
    BacktestTrade,
    Candle,
    PerformanceMetrics,
    WalkForwardResult,
    WalkForwardWindow,
)
from research.walkforward.rolling_windows import generate_windows
from research.walkforward.validation import (
    combined_oos_metrics,
    compute_is_oos_ratio,
    compute_parameter_stability,
)
from research.walkforward.overfit_detection import overfit_score

logger = logging.getLogger(__name__)


class WalkForwardEngine:
    """Runs walk-forward validation: optimize on IS, validate on OOS.

    Injects an optimizer (ResearchOptimizer) and a backtest function to avoid
    circular imports.  The optimizer is expected to expose:
        optimizer.run_bayesian_search(n_trials) -> List[OptimizationResult]
        optimizer.candles   (settable)
        optimizer.param_space (settable)

    Args:
        optimizer: A ResearchOptimizer instance (passed as Any to avoid
            circular import at module load time).
        backtest_fn: ``async fn(candles, params) -> BacktestResult``.
        train_bars: Number of bars per training window.
        test_bars:  Number of bars per test (OOS) window.
        step_bars:  Bar step between consecutive windows.
    """

    def __init__(
        self,
        optimizer: Any,
        backtest_fn: Callable,
        train_bars: int = 2016,
        test_bars: int = 672,
        step_bars: int = 336,
    ) -> None:
        self.optimizer = optimizer
        self.backtest_fn = backtest_fn
        self.train_bars = train_bars
        self.test_bars = test_bars
        self.step_bars = step_bars

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(
        self,
        candles: List[Candle],
        param_space: Dict[str, Any],
        symbol: str,
        strategy_name: str,
        n_trials: int = 50,
        optimization_metric: str = "sharpe_ratio",
    ) -> WalkForwardResult:
        """Full walk-forward across all windows.

        For each window:
          1. Optimize on ``train_candles``.
          2. Apply ``best_params`` on ``test_candles``.
          3. Record OOS performance.
          4. Compute per-window overfit score.

        Returns:
            WalkForwardResult with combined OOS trade log and aggregate metrics.
        """
        windows = generate_windows(
            candles,
            train_bars=self.train_bars,
            test_bars=self.test_bars,
            step_bars=self.step_bars,
        )

        if not windows:
            logger.warning(
                "WalkForwardEngine: no windows generated (insufficient candles)."
            )
            return WalkForwardResult(
                windows=[],
                combined_oos_trades=[],
                oos_metrics=None,
                parameter_stability=0.0,
                overfit_detected=False,
            )

        logger.info(
            "WalkForwardEngine: running %d windows for %s/%s",
            len(windows),
            strategy_name,
            symbol,
        )

        processed_windows: List[WalkForwardWindow] = []
        for window in windows:
            processed = await self._run_window(
                window=window,
                param_space=param_space,
                symbol=symbol,
                strategy_name=strategy_name,
                n_trials=n_trials,
                optimization_metric=optimization_metric,
            )
            processed_windows.append(processed)

        # Aggregate OOS trades from all windows
        combined_oos_trades: List[BacktestTrade] = []
        for w in processed_windows:
            oos_trades: object = w.best_params.get("__oos_trades__", [])
            if isinstance(oos_trades, list):
                combined_oos_trades.extend(oos_trades)

        oos_metrics = combined_oos_metrics(processed_windows)

        # Strip the internal sentinel key before stability analysis so that
        # compute_parameter_stability only sees real strategy parameters.
        clean_windows = _strip_sentinel(processed_windows)
        param_stability = compute_parameter_stability(clean_windows)
        is_oos_ratio = compute_is_oos_ratio(processed_windows)

        # Overfit detected if ratio < 0.5 or average window overfit_score > 0.5
        avg_overfit = (
            sum(w.overfit_score for w in processed_windows) / len(processed_windows)
            if processed_windows
            else 0.0
        )
        overfit_detected = (is_oos_ratio < 0.5) or (avg_overfit > 0.5)

        return WalkForwardResult(
            windows=processed_windows,
            combined_oos_trades=combined_oos_trades,
            oos_metrics=oos_metrics,
            parameter_stability=param_stability,
            overfit_detected=overfit_detected,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _run_window(
        self,
        window: WalkForwardWindow,
        param_space: Dict[str, Any],
        symbol: str,
        strategy_name: str,
        n_trials: int,
        optimization_metric: str,
    ) -> WalkForwardWindow:
        """Optimize on train_candles, evaluate on test_candles.

        Mutates and returns the window with best_params, train_metrics,
        test_metrics, and overfit_score populated.
        """
        logger.debug(
            "  window %d: train=%s…%s  test=%s…%s",
            window.window_id,
            window.train_start,
            window.train_end,
            window.test_start,
            window.test_end,
        )

        # ── Step 1: Optimise on training candles ──────────────────────────────
        # Reconfigure optimizer with training candles and param space.
        # We directly call the underlying search functions to avoid recreating
        # the ResearchOptimizer object (avoid circular import).
        try:
            from research.optimization.bayesian import bayesian_search  # noqa: PLC0415

            train_results = await bayesian_search(
                backtest_fn=self.backtest_fn,
                candles=window.train_candles,
                param_space=param_space,
                n_trials=n_trials,
                metric=optimization_metric,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "WalkForwardEngine window %d: optimisation failed: %s",
                window.window_id,
                exc,
            )
            train_results = []

        if not train_results:
            # No optimisation results — skip window
            window.best_params = {}
            return window

        best_opt = train_results[0]
        best_params: Dict[str, Any] = best_opt.params

        # ── Step 2: Record IS performance ─────────────────────────────────────
        try:
            is_result: BacktestResult = await self.backtest_fn(
                window.train_candles, best_params
            )
            window.train_metrics = _metrics_from_result(is_result)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "WalkForwardEngine window %d: IS backtest failed: %s",
                window.window_id,
                exc,
            )
            window.train_metrics = None

        # ── Step 3: Apply best_params on test candles ─────────────────────────
        try:
            oos_result: BacktestResult = await self.backtest_fn(
                window.test_candles, best_params
            )
            window.test_metrics = _metrics_from_result(oos_result)
            oos_trades = oos_result.trades
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "WalkForwardEngine window %d: OOS backtest failed: %s",
                window.window_id,
                exc,
            )
            window.test_metrics = None
            oos_trades = []

        # ── Step 4: Store params and trades ──────────────────────────────────
        window.best_params = dict(best_params)
        # Attach OOS trades under a sentinel key so validation functions can use them
        window.best_params["__oos_trades__"] = oos_trades

        # ── Step 5: Overfit score ─────────────────────────────────────────────
        if window.train_metrics is not None and window.test_metrics is not None:
            window.overfit_score = overfit_score(
                window.train_metrics, window.test_metrics
            )
        else:
            window.overfit_score = 0.0

        return window


# ── Helpers ───────────────────────────────────────────────────────────────────

def _metrics_from_result(result: BacktestResult) -> Optional[PerformanceMetrics]:
    """Extract PerformanceMetrics from a BacktestResult.

    If the result has a pre-computed metrics object in metadata, use it.
    Otherwise return None (caller handles the None case).
    """
    return result.metadata.get("metrics", None)


_SENTINEL_KEY = "__oos_trades__"


def _strip_sentinel(windows: List[WalkForwardWindow]) -> List[WalkForwardWindow]:
    """Return shallow copies of windows with the OOS-trades sentinel key removed.

    ``compute_parameter_stability`` iterates all keys of ``best_params``; the
    sentinel stores a list which is not a valid parameter value.

    Args:
        windows: Original window list (not mutated).

    Returns:
        New list of WalkForwardWindow objects with sentinel removed.
    """
    from dataclasses import replace  # noqa: PLC0415

    cleaned: List[WalkForwardWindow] = []
    for w in windows:
        if _SENTINEL_KEY in w.best_params:
            clean_params = {k: v for k, v in w.best_params.items() if k != _SENTINEL_KEY}
            cleaned.append(replace(w, best_params=clean_params))
        else:
            cleaned.append(w)
    return cleaned
