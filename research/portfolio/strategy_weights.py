"""Adaptive strategy weight manager based on rolling performance.

Class:
  StrategyWeightEngine — multi-factor weight engine
                         (drop-in replacement for trading/blofin_strategies.py)
"""
from __future__ import annotations

import json
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger("openclaw.research.portfolio.strategy_weights")


@dataclass
class _TradeRecord:
    pnl: float       # absolute PnL
    pnl_pct: float   # percentage PnL


@dataclass
class _StrategyMetrics:
    """Rolling metrics for a single strategy."""
    trades: Deque[_TradeRecord] = field(default_factory=lambda: deque())
    weight: float = 1.0 / 4     # will be re-normalised on first update

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.5
        wins = sum(1 for t in self.trades if t.pnl >= 0)
        return wins / len(self.trades)

    @property
    def avg_pnl_pct(self) -> float:
        if not self.trades:
            return 0.0
        return sum(t.pnl_pct for t in self.trades) / len(self.trades)

    @property
    def pnl_std(self) -> float:
        """Sample standard deviation of pnl_pct returns."""
        if len(self.trades) < 2:
            return 1.0   # conservative: assume high volatility
        mean = self.avg_pnl_pct
        variance = sum((t.pnl_pct - mean) ** 2 for t in self.trades) / (len(self.trades) - 1)
        return math.sqrt(variance) if variance > 0 else 1e-6

    @property
    def sharpe(self) -> float:
        """Approximate Sharpe ratio from trade-level PnL%.

        Uses risk-free rate = 0 for simplicity.
        Returns 0.0 when fewer than 2 trades available.
        """
        if len(self.trades) < 2:
            return 0.0
        return self.avg_pnl_pct / self.pnl_std

    @property
    def max_drawdown(self) -> float:
        """Max drawdown in cumulative PnL% from peak.

        Returns 0.0 (no drawdown) when insufficient data.
        """
        if len(self.trades) < 2:
            return 0.0
        cum: List[float] = []
        running = 0.0
        for t in self.trades:
            running += t.pnl_pct
            cum.append(running)

        peak = cum[0]
        max_dd = 0.0
        for v in cum:
            if v > peak:
                peak = v
            dd = (peak - v) / (abs(peak) + 1e-9)
            if dd > max_dd:
                max_dd = dd
        return max_dd


class StrategyWeightEngine:
    """Adaptive strategy weight manager based on rolling performance.

    This replaces the simple win-rate-only approach in
    trading/blofin_strategies.py with a multi-factor weighting scheme:
      - Rolling Sharpe  (primary — 50% of weight calculation)
      - Win rate        (30%)
      - Drawdown penalty (20% — penalises strategies in drawdown)

    Weights are normalised to sum to 1.0 across all active strategies.
    A floor ``min_weight`` and cap ``max_weight`` prevent any single strategy
    from dominating or being fully disabled.

    Drop-in compatibility
    ---------------------
    ``effective_confidence(strategy, raw_conf)`` works identically to the
    method in ``trading/blofin_strategies.py``.
    """

    def __init__(
        self,
        strategies: List[str],
        lookback_trades: int = 20,
        sharpe_weight: float = 0.5,
        winrate_weight: float = 0.3,
        drawdown_penalty: float = 0.2,
        min_weight: float = 0.05,
        max_weight: float = 0.40,
    ) -> None:
        self.strategies       = list(strategies)
        self.lookback_trades  = lookback_trades
        self.sharpe_weight    = sharpe_weight
        self.winrate_weight   = winrate_weight
        self.drawdown_penalty = drawdown_penalty
        self.min_weight       = min_weight
        self.max_weight       = max_weight

        # Initialise metrics with equal weights
        initial_weight = 1.0 / max(len(strategies), 1)
        self._metrics: Dict[str, _StrategyMetrics] = {
            s: _StrategyMetrics(weight=initial_weight)
            for s in strategies
        }

    # ── public API ────────────────────────────────────────────────────────────

    def update(
        self,
        strategy: str,
        trade_pnl: float,
        trade_pnl_pct: float,
    ) -> None:
        """Record a closed trade result and recompute all weights.

        Parameters
        ----------
        strategy:
            Strategy name (must be in self.strategies).
        trade_pnl:
            Absolute PnL of the closed trade.
        trade_pnl_pct:
            Percentage PnL (e.g. 0.023 = 2.3 %).
        """
        if strategy not in self._metrics:
            logger.warning("Unknown strategy '%s' — adding it.", strategy)
            self._metrics[strategy] = _StrategyMetrics(
                weight=1.0 / max(len(self._metrics) + 1, 1)
            )
            if strategy not in self.strategies:
                self.strategies.append(strategy)

        m = self._metrics[strategy]
        m.trades.append(_TradeRecord(pnl=trade_pnl, pnl_pct=trade_pnl_pct))
        # Keep only rolling window
        while len(m.trades) > self.lookback_trades:
            m.trades.popleft()

        self._recompute_weights()
        logger.debug(
            "[%s] weight=%.3f  sharpe=%.3f  WR=%.0f%%  maxDD=%.2f%%",
            strategy,
            m.weight,
            m.sharpe,
            m.win_rate * 100,
            m.max_drawdown * 100,
        )

    def get_weights(self) -> Dict[str, float]:
        """Current normalised weights.  Sum = 1.0."""
        return {s: self._metrics[s].weight for s in self.strategies if s in self._metrics}

    def get_weight(self, strategy: str) -> float:
        """Weight for a single strategy.  Returns equal share if unknown."""
        if strategy not in self._metrics:
            return 1.0 / max(len(self.strategies), 1)
        return self._metrics[strategy].weight

    def effective_confidence(self, strategy: str, raw_conf: float) -> float:
        """Scale raw confidence by strategy weight.  Result capped at 1.0.

        This mirrors the interface of ``trading/blofin_strategies.StrategyWeightEngine``.
        """
        weight = self.get_weight(strategy)
        # Weight is in [min_weight, max_weight] ⊂ [0.05, 0.40].
        # Strategies near 0.25 (equal share) keep near-original confidence.
        # Normalise against equal share so the scalar is sensible.
        n = max(len(self.strategies), 1)
        equal_share = 1.0 / n
        # Scale factor: weight / equal_share, clamped to [0.2, 2.0]
        scale = weight / equal_share
        scale = max(0.2, min(2.0, scale))
        return min(1.0, raw_conf * scale)

    def save(self, path: str = "data/strategy_weights.json") -> None:
        """Persist weights and metrics to JSON."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {}
        for name, m in self._metrics.items():
            payload[name] = {
                "weight": m.weight,
                "trades": [
                    {"pnl": t.pnl, "pnl_pct": t.pnl_pct}
                    for t in m.trades
                ],
            }
        p.write_text(json.dumps(payload, indent=2))
        logger.debug("Strategy weights saved to %s", path)

    def load(self, path: str = "data/strategy_weights.json") -> None:
        """Load weights and metrics from JSON (best-effort)."""
        p = Path(path)
        if not p.exists():
            return
        try:
            raw = json.loads(p.read_text())
            for name, d in raw.items():
                if name not in self._metrics:
                    self._metrics[name] = _StrategyMetrics()
                    if name not in self.strategies:
                        self.strategies.append(name)
                m = self._metrics[name]
                m.weight = float(d.get("weight", m.weight))
                m.trades = deque(
                    [
                        _TradeRecord(pnl=t["pnl"], pnl_pct=t["pnl_pct"])
                        for t in d.get("trades", [])
                    ],
                    maxlen=self.lookback_trades,
                )
            logger.debug("Strategy weights loaded from %s", path)
        except Exception as exc:
            logger.warning("Failed to load strategy weights from %s: %s", path, exc)

    def summary(self) -> Dict[str, Any]:
        """Full weight summary with metrics for each strategy."""
        result: Dict[str, Any] = {}
        for name in self.strategies:
            if name not in self._metrics:
                continue
            m = self._metrics[name]
            result[name] = {
                "weight":       round(m.weight, 4),
                "trades":       m.n,
                "win_rate":     round(m.win_rate * 100, 1),
                "sharpe":       round(m.sharpe, 3),
                "max_drawdown": round(m.max_drawdown * 100, 2),
                "avg_pnl_pct":  round(m.avg_pnl_pct * 100, 3),
            }
        return result

    # ── internal ──────────────────────────────────────────────────────────────

    def _recompute_weights(self) -> None:
        """Recompute all strategy weights from current metrics.

        Formula (per strategy):
            raw_score = sharpe_weight × sharpe_score
                      + winrate_weight × win_rate_score
                      - drawdown_penalty × drawdown_score

        Where each component is normalised relative to peers.
        Scores are then floor/cap-clamped and renormalised to sum = 1.
        """
        # Collect raw scores
        names = [s for s in self.strategies if s in self._metrics]
        if not names:
            return

        raw: Dict[str, float] = {}
        for name in names:
            m = self._metrics[name]
            if m.n < 2:
                # Not enough data — give equal score
                raw[name] = 1.0
                continue

            # Sharpe component: tanh to normalise unbounded Sharpe
            sharpe_score  = (math.tanh(m.sharpe) + 1.0) / 2.0   # 0–1
            # Win-rate component: direct
            wr_score      = m.win_rate                            # 0–1
            # Drawdown penalty: 0 = no penalty, 1 = 100% drawdown
            dd_score      = m.max_drawdown                        # 0–1

            raw[name] = (
                self.sharpe_weight    * sharpe_score
                + self.winrate_weight * wr_score
                - self.drawdown_penalty * dd_score
            )

        # Shift so minimum is 0 (never negative before floor application)
        min_raw = min(raw.values())
        if min_raw < 0:
            for name in raw:
                raw[name] -= min_raw

        total = sum(raw.values())
        if total <= 0:
            # Degenerate — equal weights
            for name in names:
                self._metrics[name].weight = 1.0 / len(names)
            return

        # Normalise, apply floor/cap, renormalise once more
        n = len(names)
        min_w = self.min_weight
        max_w = self.max_weight

        weights: Dict[str, float] = {
            name: raw[name] / total
            for name in names
        }

        # Clamp
        for name in names:
            weights[name] = max(min_w, min(max_w, weights[name]))

        # Renormalise after clamping
        total2 = sum(weights.values())
        for name in names:
            self._metrics[name].weight = round(weights[name] / total2, 6)
