"""Alpha Validation Framework for OpenClaw.

Computes per-strategy statistical alpha metrics from closed trade outcomes
and produces an advisory AlphaValidationReport. All outputs are read-only
recommendations fed upstream into StrategyGovernanceEngine — this module
NEVER places trades, disables systems, or bypasses governance.

Thread safety: all public methods hold _lock. Load is atomic under lock.
File reads use fcntl.LOCK_SH. Fail-CLOSED: on any error returns degraded signal.
"""
from __future__ import annotations

import fcntl
import json
import logging
import math
import statistics
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("openclaw.research.statistics.alpha_validation")

# ── Constants ─────────────────────────────────────────────────────────────────

_MIN_TRADES_FOR_METRICS   = 5
_MIN_TRADES_ALPHA_COLLAPSE = 15
_MIN_TRADES_SUFFICIENT     = 20
_SIG_INSUFFICIENCY_CUTOFF  = 0.3
_EWMA_ALPHA_DEFAULT        = 0.1
_WINDOW_DEFAULT            = 100

# Alpha signal thresholds
_STRONG_SHARPE      = 0.3
_STRONG_EXPECTANCY  = 2.0
_STRONG_SIG         = 0.5
_PRESENT_SHARPE     = 0.1
_COLLAPSE_EXPECTANCY = -2.0
_DECAY_SLOPE_THRESHOLD = -0.05  # per-20-trades
_CALIBRATION_DRIFT_UNSTABLE = 0.3


# ── Enums ─────────────────────────────────────────────────────────────────────

class AlphaSignal(str, Enum):
    STRONG_ALPHA    = "STRONG_ALPHA"     # score > 0.8
    ALPHA_PRESENT   = "ALPHA_PRESENT"    # 0.6-0.8
    MARGINAL        = "MARGINAL"         # 0.4-0.6
    DEGRADING       = "DEGRADING"        # 0.2-0.4
    ALPHA_COLLAPSED = "ALPHA_COLLAPSED"  # < 0.2


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class StrategyAlphaMetrics:
    strategy:                       str
    rolling_sharpe:                 float   # EWMA-smoothed Sharpe proxy
    rolling_win_rate:               float   # fraction 0-1
    rolling_expectancy_usd:         float   # mean PnL over window
    regime_adjusted_expectancy:     float   # weighted by regime confidence
    confidence_calibration_drift:   float   # recent calibration shift (0-1)
    win_rate_decay_rate:            float   # slope per 20-trade segment (negative = declining)
    expectancy_decay_rate:          float   # slope of expectancy over thirds
    sample_size:                    int
    statistical_significance:       float   # 0-1 heuristic proxy
    live_vs_backtest_divergence:    float   # stub: 0.0 if no backtest data
    alpha_signal:                   AlphaSignal
    last_updated:                   str     # ISO timestamp


@dataclass
class AlphaValidationReport:
    generated_at:                   str
    strategies:                     Dict[str, StrategyAlphaMetrics]
    portfolio_alpha_signal:         AlphaSignal   # most conservative across strategies
    alpha_collapsed_strategies:     List[str]     # signal == ALPHA_COLLAPSED
    degrading_strategies:           List[str]     # signal == DEGRADING
    insufficient_sample_strategies: List[str]     # sample_size < 20
    overall_portfolio_expectancy:   float
    trades_analyzed:                int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ewma(values: List[float], alpha: float) -> float:
    """Exponentially weighted moving average over a sequence."""
    if not values:
        return 0.0
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1.0 - alpha) * result
    return result


def _win_rate(trades: List[dict]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.get("outcome", "") == "win")
    return wins / len(trades)


def _pnl_list(trades: List[dict]) -> List[float]:
    return [float(t.get("pnl", 0.0)) for t in trades]


def _safe_std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    try:
        return statistics.stdev(values)
    except statistics.StatisticsError:
        return 0.0


def _compute_win_rate_decay(trades: List[dict], window: int) -> float:
    """Split window into thirds; return (third3_wr - third1_wr) / (window/3)."""
    n = len(trades)
    if n < 9:
        return 0.0
    chunk = max(1, n // 3)
    t1 = trades[:chunk]
    t3 = trades[n - chunk:]
    wr1 = _win_rate(t1)
    wr3 = _win_rate(t3)
    divisor = window / 3.0
    return (wr3 - wr1) / divisor if divisor > 0 else 0.0


def _compute_expectancy_decay(trades: List[dict]) -> float:
    """Slope of expectancy from first third to last third (negative = declining)."""
    n = len(trades)
    if n < 9:
        return 0.0
    chunk = max(1, n // 3)
    pnl1 = _pnl_list(trades[:chunk])
    pnl3 = _pnl_list(trades[n - chunk:])
    mean1 = sum(pnl1) / len(pnl1) if pnl1 else 0.0
    mean3 = sum(pnl3) / len(pnl3) if pnl3 else 0.0
    return mean3 - mean1


def _compute_calibration_drift(trades: List[dict]) -> float:
    """Measures how much confidence-to-WR calibration has shifted.

    Compares confidence vs. actual win rate in first-half vs. second-half.
    Returns |delta_calibration_error|, capped at 1.0.
    """
    if len(trades) < 4:
        return 0.0
    mid = len(trades) // 2
    first_half  = trades[:mid]
    second_half = trades[mid:]

    def cal_error(subset: List[dict]) -> float:
        if not subset:
            return 0.0
        confidences = [float(t.get("confidence", 0.5)) for t in subset]
        wins = [1.0 if t.get("outcome", "") == "win" else 0.0 for t in subset]
        mean_conf = sum(confidences) / len(confidences)
        mean_wr   = sum(wins) / len(wins)
        return abs(mean_conf - mean_wr)

    drift = abs(cal_error(second_half) - cal_error(first_half))
    return min(1.0, drift)


def _compute_regime_adjusted_expectancy(trades: List[dict]) -> float:
    """Expectancy weighted by per-trade regime confidence."""
    if not trades:
        return 0.0
    total_weight = 0.0
    weighted_pnl = 0.0
    for t in trades:
        conf   = float(t.get("confidence", 0.5))
        pnl    = float(t.get("pnl", 0.0))
        weight = max(0.0, min(1.0, conf))
        weighted_pnl  += pnl * weight
        total_weight  += weight
    if total_weight < 1e-9:
        return 0.0
    return weighted_pnl / total_weight


def _determine_alpha_signal(
    rolling_sharpe: float,
    rolling_expectancy_usd: float,
    win_rate_decay_rate: float,
    expectancy_decay_rate: float,
    statistical_significance: float,
    sample_size: int,
) -> AlphaSignal:
    """Classify alpha signal. Evaluation order: COLLAPSED → DEGRADING → MARGINAL → PRESENT → STRONG."""

    # ALPHA_COLLAPSED: sustained negative expectancy with sufficient sample
    if rolling_expectancy_usd < _COLLAPSE_EXPECTANCY and sample_size >= _MIN_TRADES_ALPHA_COLLAPSE:
        return AlphaSignal.ALPHA_COLLAPSED

    # DEGRADING: active deterioration in win rate AND negative expectancy drift
    if win_rate_decay_rate < _DECAY_SLOPE_THRESHOLD and expectancy_decay_rate < 0:
        return AlphaSignal.DEGRADING

    # MARGINAL: negative expectancy or very low Sharpe
    if rolling_expectancy_usd < 0.0 or rolling_sharpe < _PRESENT_SHARPE:
        return AlphaSignal.MARGINAL

    # STRONG_ALPHA: high Sharpe + positive expectancy + statistically significant
    if (
        rolling_sharpe >= _STRONG_SHARPE
        and rolling_expectancy_usd > _STRONG_EXPECTANCY
        and statistical_significance >= _STRONG_SIG
    ):
        return AlphaSignal.STRONG_ALPHA

    # ALPHA_PRESENT: positive Sharpe and positive expectancy
    if rolling_sharpe >= _PRESENT_SHARPE and rolling_expectancy_usd > 0.0:
        return AlphaSignal.ALPHA_PRESENT

    # Default to MARGINAL
    return AlphaSignal.MARGINAL


# Signal ordering (most conservative = lowest)
_SIGNAL_ORDER = {
    AlphaSignal.ALPHA_COLLAPSED: 0,
    AlphaSignal.DEGRADING:       1,
    AlphaSignal.MARGINAL:        2,
    AlphaSignal.ALPHA_PRESENT:   3,
    AlphaSignal.STRONG_ALPHA:    4,
}


def _worst_signal(signals: List[AlphaSignal]) -> AlphaSignal:
    if not signals:
        return AlphaSignal.ALPHA_COLLAPSED
    return min(signals, key=lambda s: _SIGNAL_ORDER[s])


# ── Engine ────────────────────────────────────────────────────────────────────

class AlphaValidationEngine:
    """Advisory-only alpha validation engine.

    SAFETY CONTRACT:
    - NEVER places trades.
    - NEVER disables systems directly.
    - NEVER bypasses governance.
    - All outputs are read-only recommendations for StrategyGovernanceEngine.
    """

    def __init__(
        self,
        outcomes_path: str = "data/logs/trade_outcomes.jsonl",
        window: int = _WINDOW_DEFAULT,
        ewma_alpha: float = _EWMA_ALPHA_DEFAULT,
    ) -> None:
        self._outcomes_path = outcomes_path
        self._window        = window
        self._ewma_alpha    = ewma_alpha
        self._lock          = threading.Lock()
        self._trades: List[dict] = []

    # ── Data loading ──────────────────────────────────────────────────────────

    def load_outcomes(self, path: Optional[str] = None) -> int:
        """Load trade outcomes from JSONL. Returns count loaded. Thread-safe."""
        target = path or self._outcomes_path
        trades: List[dict] = []
        try:
            p = Path(target)
            if not p.exists():
                logger.warning("Outcomes file not found: %s", target)
                with self._lock:
                    self._trades = []
                return 0
            with open(p, "r", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_SH)
                try:
                    for raw in fh:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            trades.append(json.loads(raw))
                        except json.JSONDecodeError:
                            logger.debug("Skipping malformed JSONL line")
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError as exc:
            logger.error("Failed to read outcomes file: %s", exc)
            return 0

        with self._lock:
            self._trades = trades
        logger.info("Loaded %d trade outcomes from %s", len(trades), target)
        return len(trades)

    # ── Per-strategy metrics ──────────────────────────────────────────────────

    def compute_strategy_metrics(
        self, strategy: str, trades: List[dict]
    ) -> StrategyAlphaMetrics:
        """Compute full alpha metrics for a single strategy's trade list."""
        now = _now_iso()
        n   = len(trades)

        # Insufficient sample: return safe defaults
        if n < _MIN_TRADES_FOR_METRICS:
            return StrategyAlphaMetrics(
                strategy=strategy,
                rolling_sharpe=0.0,
                rolling_win_rate=0.0,
                rolling_expectancy_usd=0.0,
                regime_adjusted_expectancy=0.0,
                confidence_calibration_drift=0.0,
                win_rate_decay_rate=0.0,
                expectancy_decay_rate=0.0,
                sample_size=n,
                statistical_significance=0.0,
                live_vs_backtest_divergence=0.0,
                alpha_signal=AlphaSignal.MARGINAL,
                last_updated=now,
            )

        # Use at most the last `window` trades
        window_trades = trades[-self._window:] if n > self._window else trades
        wn = len(window_trades)

        pnls     = _pnl_list(window_trades)
        mean_pnl = sum(pnls) / wn
        std_pnl  = _safe_std(pnls)

        # EWMA-smoothed Sharpe proxy
        # Build a running EWMA of the Sharpe over the window in batches of 10
        # then smooth the final value — simpler: smooth pnl series then compute
        if wn >= _MIN_TRADES_FOR_METRICS:
            ewma_pnls  = []
            current    = pnls[0]
            for v in pnls[1:]:
                current = self._ewma_alpha * v + (1.0 - self._ewma_alpha) * current
                ewma_pnls.append(current)
            ewma_mean  = current  # last EWMA value
            ewma_std   = _safe_std(ewma_pnls) if len(ewma_pnls) >= 2 else std_pnl
            rolling_sharpe = (ewma_mean / (ewma_std + 1e-9)) * math.sqrt(wn)
        else:
            rolling_sharpe = 0.0

        # Statistical significance proxy (bounded heuristic, not a real p-value)
        if wn >= _MIN_TRADES_FOR_METRICS:
            sig = min(1.0, math.sqrt(wn) * abs(mean_pnl) / (std_pnl + 1e-9) / 3.0)
        else:
            sig = 0.0

        rolling_win_rate             = _win_rate(window_trades)
        rolling_expectancy_usd       = mean_pnl
        regime_adjusted_expectancy   = _compute_regime_adjusted_expectancy(window_trades)
        confidence_calibration_drift = _compute_calibration_drift(window_trades)
        win_rate_decay_rate          = _compute_win_rate_decay(window_trades, self._window)
        expectancy_decay_rate        = _compute_expectancy_decay(window_trades)

        alpha_signal = _determine_alpha_signal(
            rolling_sharpe=rolling_sharpe,
            rolling_expectancy_usd=rolling_expectancy_usd,
            win_rate_decay_rate=win_rate_decay_rate,
            expectancy_decay_rate=expectancy_decay_rate,
            statistical_significance=sig,
            sample_size=wn,
        )

        return StrategyAlphaMetrics(
            strategy=strategy,
            rolling_sharpe=rolling_sharpe,
            rolling_win_rate=rolling_win_rate,
            rolling_expectancy_usd=rolling_expectancy_usd,
            regime_adjusted_expectancy=regime_adjusted_expectancy,
            confidence_calibration_drift=confidence_calibration_drift,
            win_rate_decay_rate=win_rate_decay_rate,
            expectancy_decay_rate=expectancy_decay_rate,
            sample_size=wn,
            statistical_significance=sig,
            live_vs_backtest_divergence=0.0,  # stub: no backtest data yet
            alpha_signal=alpha_signal,
            last_updated=now,
        )

    # ── Report generation ─────────────────────────────────────────────────────

    def generate_report(self) -> AlphaValidationReport:
        """Generate a full alpha validation report across all strategies."""
        with self._lock:
            trades_snapshot = list(self._trades)

        # Group trades by strategy
        by_strategy: Dict[str, List[dict]] = {}
        for t in trades_snapshot:
            strat = t.get("strategy", "UNKNOWN")
            by_strategy.setdefault(strat, []).append(t)

        strategy_metrics: Dict[str, StrategyAlphaMetrics] = {}
        for strat, strat_trades in by_strategy.items():
            # Sort by timestamp for correct ordering
            strat_trades_sorted = sorted(strat_trades, key=lambda x: x.get("ts", ""))
            strategy_metrics[strat] = self.compute_strategy_metrics(strat, strat_trades_sorted)

        # Portfolio-level aggregation (most conservative signal = worst)
        all_signals   = [m.alpha_signal for m in strategy_metrics.values()]
        portfolio_sig = _worst_signal(all_signals) if all_signals else AlphaSignal.ALPHA_COLLAPSED

        collapsed_strategies    = [s for s, m in strategy_metrics.items()
                                    if m.alpha_signal == AlphaSignal.ALPHA_COLLAPSED]
        degrading_strategies    = [s for s, m in strategy_metrics.items()
                                    if m.alpha_signal == AlphaSignal.DEGRADING]
        insufficient_strategies = [s for s, m in strategy_metrics.items()
                                    if m.sample_size < _MIN_TRADES_SUFFICIENT]

        # Overall portfolio expectancy = simple mean across strategies
        all_expectancies = [m.rolling_expectancy_usd for m in strategy_metrics.values()]
        overall_expectancy = (
            sum(all_expectancies) / len(all_expectancies) if all_expectancies else 0.0
        )

        return AlphaValidationReport(
            generated_at=_now_iso(),
            strategies=strategy_metrics,
            portfolio_alpha_signal=portfolio_sig,
            alpha_collapsed_strategies=collapsed_strategies,
            degrading_strategies=degrading_strategies,
            insufficient_sample_strategies=insufficient_strategies,
            overall_portfolio_expectancy=overall_expectancy,
            trades_analyzed=len(trades_snapshot),
        )

    # ── Diagnostic helpers ────────────────────────────────────────────────────

    def detect_alpha_collapse(self, metrics: StrategyAlphaMetrics) -> bool:
        """True if strategy has collapsed alpha signal. Advisory only."""
        return metrics.alpha_signal == AlphaSignal.ALPHA_COLLAPSED

    def detect_unstable_adaptation(self, metrics: StrategyAlphaMetrics) -> bool:
        """True if confidence calibration has drifted beyond safe threshold. Advisory only."""
        return metrics.confidence_calibration_drift > _CALIBRATION_DRIFT_UNSTABLE
