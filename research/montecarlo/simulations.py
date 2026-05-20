"""Monte Carlo simulation methods: bootstrap, trade shuffling, equity paths."""
from __future__ import annotations

import math
from typing import List, Optional

import numpy as np

from research.types import BacktestTrade


class MonteCarloSimulator:
    """Bootstrap and randomization methods for strategy validation.

    All heavy computation is done with numpy for performance.  10 000
    simulations on a 200-trade history should complete in well under 5 s.

    Args:
        n_simulations: Number of Monte Carlo paths to generate.
        seed: Random seed for reproducibility.
    """

    def __init__(self, n_simulations: int = 10_000, seed: int = 42) -> None:
        self.n_simulations = n_simulations
        self.seed = seed
        self._rng = np.random.default_rng(seed)

    # ── Core simulation methods ───────────────────────────────────────────────

    def bootstrap_returns(
        self,
        trades: List[BacktestTrade],
        n_periods: int = 252,
        block_size: int = 20,
    ) -> np.ndarray:
        """Block bootstrap to preserve autocorrelation structure.

        Uses the stationary bootstrap (Politis & Romano 1994) approximation:
        draw random starting indices and take contiguous blocks.

        Args:
            trades:     Historical trades (source of per-trade % returns).
            n_periods:  Number of return periods in each simulated path.
            block_size: Average block length; preserves serial dependence.

        Returns:
            Array of shape ``(n_simulations, n_periods)`` containing
            bootstrapped per-period returns (as percent, matching
            ``BacktestTrade.net_pnl_pct``).
        """
        if not trades:
            return np.zeros((self.n_simulations, n_periods), dtype=float)

        pnls = np.array([t.net_pnl_pct for t in trades], dtype=float)
        n_source = len(pnls)
        rng = self._rng

        result = np.empty((self.n_simulations, n_periods), dtype=float)

        for i in range(self.n_simulations):
            drawn: List[float] = []
            while len(drawn) < n_periods:
                # Random block start
                start = int(rng.integers(0, n_source))
                # Geometric block length centred on block_size
                length = int(rng.geometric(1.0 / block_size))
                length = max(1, min(length, n_periods - len(drawn)))
                for j in range(length):
                    drawn.append(float(pnls[(start + j) % n_source]))
            result[i] = drawn[:n_periods]

        return result

    def randomize_trade_sequence(
        self,
        trades: List[BacktestTrade],
        n_simulations: Optional[int] = None,
    ) -> np.ndarray:
        """Shuffle trade order N times to test path-dependency.

        Args:
            trades:       Historical trades.
            n_simulations: Override ``self.n_simulations`` for this call.

        Returns:
            Array of equity curves with shape
            ``(n_simulations, n_trades + 1)`` where column 0 is 1.0 (start).
        """
        n_sim = n_simulations if n_simulations is not None else self.n_simulations

        if not trades:
            return np.ones((n_sim, 1), dtype=float)

        pnls = np.array([t.net_pnl_pct / 100.0 for t in trades], dtype=float)
        n_trades = len(pnls)
        rng = self._rng

        # Build all shuffled indices at once for speed
        idx_matrix = np.argsort(
            rng.random((n_sim, n_trades)), axis=1
        )  # shape (n_sim, n_trades)

        shuffled = pnls[idx_matrix]  # (n_sim, n_trades)
        returns_plus_one = 1.0 + shuffled  # (n_sim, n_trades)

        equity = np.empty((n_sim, n_trades + 1), dtype=float)
        equity[:, 0] = 1.0
        np.cumprod(returns_plus_one, axis=1, out=equity[:, 1:])

        return equity

    def simulate_equity_paths(
        self,
        trades: List[BacktestTrade],
        initial_capital: float,
    ) -> np.ndarray:
        """Simulate equity paths by bootstrapping trade sequences.

        Uses block bootstrap (``bootstrap_returns``) to draw paths of the
        same length as the original trade history.

        Args:
            trades:          Historical trades.
            initial_capital: Starting portfolio value in currency units.

        Returns:
            Array of shape ``(n_simulations, n_trades + 1)`` with equity
            values starting at ``initial_capital``.
        """
        if not trades:
            return np.full((self.n_simulations, 1), initial_capital, dtype=float)

        n_trades = len(trades)
        bootstrapped = self.bootstrap_returns(
            trades,
            n_periods=n_trades,
            block_size=max(1, min(20, n_trades // 10)),
        )
        # bootstrapped: (n_simulations, n_trades) in percent
        returns_plus_one = 1.0 + bootstrapped / 100.0  # (n_sim, n_trades)

        equity = np.empty((self.n_simulations, n_trades + 1), dtype=float)
        equity[:, 0] = initial_capital
        np.cumprod(returns_plus_one, axis=1, out=equity[:, 1:])
        equity[:, 1:] *= initial_capital

        return equity
