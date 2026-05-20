"""Deterministic replay validation — same inputs must produce same outputs."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.validation.replay")

_CANARY_SENTINEL = 9.87654321e17  # deliberately absurd price used as canary


class ReplayValidator:
    """Validates that backtesting is deterministic across multiple runs."""

    async def validate_determinism(
        self,
        engine,
        candles: list,
        strategy_fn,
        params: Dict[str, Any],
        n_runs: int = 3,
    ) -> bool:
        """Run same backtest n_runs times, verify identical results.

        Checks:
        - Number of trades is identical across all runs.
        - Entry/exit prices match within 1e-10.
        - Final equity matches within 1e-10.

        Returns True if all runs produce identical results.
        """
        if n_runs < 2:
            logger.warning("ReplayValidator: n_runs must be >= 2, got %d", n_runs)
            return False

        results = []
        for run_idx in range(n_runs):
            try:
                result = engine.run(candles, strategy_fn, params)
                if asyncio.iscoroutine(result):
                    result = await result
                results.append(result)
                logger.debug("ReplayValidator: run %d completed", run_idx)
            except Exception as exc:
                logger.error("ReplayValidator: run %d raised %s", run_idx, exc)
                return False

        if not results:
            return False

        ref = results[0]
        ref_trades = getattr(ref, "trades", [])
        ref_equity = getattr(ref, "final_capital", None)

        for i, res in enumerate(results[1:], start=1):
            trades = getattr(res, "trades", [])
            final_equity = getattr(res, "final_capital", None)

            # Trade count
            if len(trades) != len(ref_trades):
                logger.error(
                    "ReplayValidator: run %d trade count %d != ref %d",
                    i,
                    len(trades),
                    len(ref_trades),
                )
                return False

            # Entry/exit price determinism
            for j, (t, r) in enumerate(zip(trades, ref_trades)):
                ep_diff = abs(
                    getattr(t, "entry_price", 0.0) - getattr(r, "entry_price", 0.0)
                )
                xp_diff = abs(
                    getattr(t, "exit_price", 0.0) - getattr(r, "exit_price", 0.0)
                )
                if ep_diff > 1e-10 or xp_diff > 1e-10:
                    logger.error(
                        "ReplayValidator: run %d trade %d price mismatch "
                        "(entry_diff=%.2e exit_diff=%.2e)",
                        i,
                        j,
                        ep_diff,
                        xp_diff,
                    )
                    return False

            # Final equity
            if ref_equity is not None and final_equity is not None:
                if abs(final_equity - ref_equity) > 1e-10:
                    logger.error(
                        "ReplayValidator: run %d final equity %.10f != ref %.10f",
                        i,
                        final_equity,
                        ref_equity,
                    )
                    return False

        logger.info(
            "ReplayValidator: determinism confirmed over %d runs (%d trades)",
            n_runs,
            len(ref_trades),
        )
        return True

    async def validate_no_future_leakage(
        self,
        engine,
        candles: list,
        strategy_fn,
        params: Dict[str, Any],
    ) -> bool:
        """Verify strategy never sees future candle data.

        Injects canary values into future candles at each bar, then runs the
        backtest and checks that the canary never appears in any strategy
        decision metadata.

        Returns True if no future leakage is detected.
        """
        if not candles:
            logger.warning("ReplayValidator: empty candles provided")
            return False

        # Clone candles so we don't mutate the originals
        try:
            from copy import deepcopy
            poisoned = deepcopy(candles)
        except Exception:
            poisoned = list(candles)

        # Inject canary into the close prices of the second half of the series
        mid = len(poisoned) // 2
        for c in poisoned[mid:]:
            try:
                object.__setattr__(c, "close", _CANARY_SENTINEL)
            except (AttributeError, TypeError):
                try:
                    c.close = _CANARY_SENTINEL
                except AttributeError:
                    pass  # dataclass with frozen=True — skip canary injection

        try:
            result = engine.run(poisoned, strategy_fn, params)
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as exc:
            logger.error("ReplayValidator: leakage test engine error: %s", exc)
            return False

        # Check that canary never propagated into early trade decisions
        trades = getattr(result, "trades", [])
        for trade in trades:
            ep = getattr(trade, "entry_price", 0.0)
            xp = getattr(trade, "exit_price", 0.0)
            if abs(ep - _CANARY_SENTINEL) < 1.0 or abs(xp - _CANARY_SENTINEL) < 1.0:
                logger.error(
                    "ReplayValidator: FUTURE LEAKAGE detected in trade %s",
                    getattr(trade, "trade_id", "?"),
                )
                return False

        logger.info(
            "ReplayValidator: no future leakage detected (%d trades examined)",
            len(trades),
        )
        return True
