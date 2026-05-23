"""Alpha Durability Validation Lab for OpenClaw.

Validates alpha durability across strategies using half-life analysis, decay
acceleration, execution-adjusted expectancy, volatility segmentation, and
Monte Carlo degradation scenarios.

All outputs are advisory only and must never alter execution, positions, or
bypass governance.

Thread safety: double-checked locking for singleton; _lock guards all mutable
state. File I/O uses fcntl.LOCK_SH. Fail-closed: exceptions return safe
defaults. Deterministic replay: random.Random(seed) — never global random.
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

logger = logging.getLogger("openclaw.research.statistics.live_alpha_lab")

# ── Constants ──────────────────────────────────────────────────────────────────

_MIN_STRATEGY_SAMPLE = 10
_MAX_HALF_LIFE = 1000.0
_WINDOW_DEFAULT = 200
_SEED_DEFAULT = 42
_MONTE_CARLO_N_DEFAULT = 500


# ── Enums ──────────────────────────────────────────────────────────────────────


class AlphaDurabilityClassification(str, Enum):
    ROBUST = "ROBUST"
    FRAGILE = "FRAGILE"
    COLLAPSING = "COLLAPSING"
    INVALIDATED = "INVALIDATED"


_CLASS_ORDER: Dict[AlphaDurabilityClassification, int] = {
    AlphaDurabilityClassification.ROBUST: 3,
    AlphaDurabilityClassification.FRAGILE: 2,
    AlphaDurabilityClassification.COLLAPSING: 1,
    AlphaDurabilityClassification.INVALIDATED: 0,
}


def _worst_classification(
    classes: List[AlphaDurabilityClassification],
) -> AlphaDurabilityClassification:
    if not classes:
        return AlphaDurabilityClassification.INVALIDATED
    return min(classes, key=lambda c: _CLASS_ORDER[c])


# ── Dataclasses ────────────────────────────────────────────────────────────────


@dataclass
class StrategyDurabilityMetrics:
    """Per-strategy durability metrics. All fields are advisory signals."""

    strategy: str
    sample_size: int
    alpha_half_life: float                    # trades until win rate halves from peak
    decay_acceleration: float                 # rate of change of decay rate
    execution_adjusted_expectancy: float      # expectancy minus estimated slippage cost
    latency_adjusted_expectancy: float        # execution_adjusted minus latency cost estimate
    spread_adjusted_expectancy: float         # latency_adjusted minus spread cost
    volatility_segmented_alpha: Dict[str, float]  # regime → expectancy
    confidence_calibration_persistence: float     # Pearson(confidence, win) over window
    classification: AlphaDurabilityClassification
    robustness_score: float                   # 0-100


@dataclass
class AlphaDurabilityReport:
    """Portfolio-level alpha durability report. Advisory only."""

    generated_at: str
    window: int
    seed: int
    strategies: Dict[str, StrategyDurabilityMetrics]
    portfolio_classification: AlphaDurabilityClassification  # worst across strategies
    portfolio_robustness_score: float                        # min across strategies
    alpha_half_life_portfolio: float                         # harmonic mean of strategy half-lives
    monte_carlo_degradation_scenarios: List[dict]            # N=10 bootstrap scenarios
    trades_analyzed: int


# ── Internal helpers ───────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_mean(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _safe_std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    try:
        return statistics.stdev(values)
    except statistics.StatisticsError:
        return 0.0


def _pearson(xs: List[float], ys: List[float]) -> float:
    """Pearson correlation coefficient — no external dependencies."""
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    den_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    denom = den_x * den_y
    return num / denom if denom > 1e-8 else 0.0


def _harmonic_mean(values: List[float]) -> float:
    """Harmonic mean: n / sum(1/v for v in values). Handles zeros gracefully."""
    if not values:
        return 0.0
    safe = [max(1e-6, abs(v)) for v in values]
    return len(safe) / sum(1.0 / v for v in safe)


def _compute_max_drawdown_pct(cum_pnls: List[float]) -> float:
    """Maximum percentage decline from peak in a cumulative PnL series."""
    if len(cum_pnls) < 2:
        return 0.0
    peak = cum_pnls[0]
    max_dd = 0.0
    for v in cum_pnls:
        if v > peak:
            peak = v
        if peak > 1e-8:
            dd = (peak - v) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return max_dd


# ── Lab ────────────────────────────────────────────────────────────────────────


class AlphaDurabilityLab:
    """Alpha durability validation lab.

    Advisory only. Must never alter execution, positions, or bypass governance.
    """

    def __init__(
        self,
        outcomes_path: str = "data/logs/trade_outcomes.jsonl",
        window: int = _WINDOW_DEFAULT,
        seed: int = _SEED_DEFAULT,
        monte_carlo_n: int = _MONTE_CARLO_N_DEFAULT,
    ) -> None:
        self._outcomes_path = outcomes_path
        self._window = window
        self._seed = seed
        self._monte_carlo_n = monte_carlo_n
        self._lock = threading.Lock()
        self._trades: List[dict] = []
        self._by_strategy: Dict[str, List[dict]] = {}
        self._rng = __import__("random").Random(seed)

    # ── Data loading ───────────────────────────────────────────────────────────

    def load_outcomes(self) -> int:
        """Load trade outcomes from JSONL with shared read lock.

        Groups by strategy. Loads up to window most recent records.
        Returns count loaded.
        """
        trades: List[dict] = []
        try:
            p = Path(self._outcomes_path)
            if not p.exists():
                logger.warning("Outcomes file not found: %s", self._outcomes_path)
                with self._lock:
                    self._trades = []
                    self._by_strategy = {}
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
            with self._lock:
                self._trades = []
                self._by_strategy = {}
            return 0

        trades = trades[-self._window:] if len(trades) > self._window else trades

        by_strategy: Dict[str, List[dict]] = {}
        for t in trades:
            s = str(t.get("strategy", "UNKNOWN"))
            by_strategy.setdefault(s, []).append(t)

        with self._lock:
            self._trades = trades
            self._by_strategy = by_strategy

        logger.info("Loaded %d outcomes, %d strategies from %s",
                    len(trades), len(by_strategy), self._outcomes_path)
        return len(trades)

    # ── Strategy-level metrics ─────────────────────────────────────────────────

    def compute_alpha_half_life(self, strategy_pnls: List[float]) -> float:
        """Compute trades until win rate halves from peak win rate.

        Uses binary win/loss classification of PnLs (positive = win).
        Returns trade count; bounded to max 1000.
        """
        if len(strategy_pnls) < 4:
            return _MAX_HALF_LIFE

        wins = [1.0 if p > 0 else 0.0 for p in strategy_pnls]
        n = len(wins)
        mid = max(2, n // 2)

        # Find peak cumulative win rate in first half
        peak_wr = 0.0
        for i in range(1, mid + 1):
            wr = sum(wins[:i]) / i
            if wr > peak_wr:
                peak_wr = wr

        if peak_wr < 1e-6:
            return _MAX_HALF_LIFE

        target = peak_wr * 0.5

        # Find where win rate falls to half of peak in second half
        for i in range(mid, n):
            # Rolling win rate ending at index i
            window_size = min(20, i + 1)
            start = max(0, i - window_size + 1)
            rolling_wr = sum(wins[start: i + 1]) / (i + 1 - start)
            if rolling_wr <= target:
                return min(_MAX_HALF_LIFE, float(i - mid + 1))

        return _MAX_HALF_LIFE

    def compute_decay_acceleration(self, strategy_pnls: List[float]) -> float:
        """Compute rate of change of decay rate across three segments.

        decay_acceleration = (wr3 - wr2) - (wr2 - wr1).
        Negative = accelerating decay.
        """
        n = len(strategy_pnls)
        if n < 6:
            return 0.0

        chunk = max(2, n // 3)
        seg1 = strategy_pnls[:chunk]
        seg2 = strategy_pnls[chunk: 2 * chunk]
        seg3 = strategy_pnls[2 * chunk:]

        def _win_rate(pnls: List[float]) -> float:
            if not pnls:
                return 0.0
            return sum(1 for p in pnls if p > 0) / len(pnls)

        wr1 = _win_rate(seg1)
        wr2 = _win_rate(seg2)
        wr3 = _win_rate(seg3)

        return (wr3 - wr2) - (wr2 - wr1)

    def compute_execution_adjusted_expectancy(self, outcomes: List[dict]) -> float:
        """Compute expectancy adjusted for estimated execution costs.

        Uses confidence as a slippage cost proxy when slippage_bps is unavailable:
        cost_estimate = confidence * 2.0 per trade.
        """
        if not outcomes:
            return 0.0
        pnls = [float(t.get("pnl", 0.0)) for t in outcomes]
        confidences = [float(t.get("confidence", 0.5)) for t in outcomes]
        costs = [c * 2.0 for c in confidences]  # proxy slippage cost
        adjusted = [p - c for p, c in zip(pnls, costs)]
        return _safe_mean(adjusted)

    def compute_volatility_segmented_alpha(self, outcomes: List[dict]) -> Dict[str, float]:
        """Group outcomes by regime and compute mean PnL per regime.

        Returns dict with keys TRENDING, RANGING, UNKNOWN (and any others found).
        """
        by_regime: Dict[str, List[float]] = {}
        for t in outcomes:
            regime = str(t.get("regime", "UNKNOWN")).upper()
            pnl = float(t.get("pnl", 0.0))
            by_regime.setdefault(regime, []).append(pnl)

        # Always include the standard regimes
        result: Dict[str, float] = {
            "TRENDING": 0.0,
            "RANGING": 0.0,
            "UNKNOWN": 0.0,
        }
        for regime, pnls in by_regime.items():
            result[regime] = _safe_mean(pnls)

        return result

    def run_monte_carlo_degradation(
        self, pnls: List[float], n: int
    ) -> List[dict]:
        """Run n bootstrap degradation scenarios using deterministic RNG.

        Each scenario resamples pnls with replacement and computes expectancy
        and max drawdown percentage.

        Uses self._rng (random.Random(self.seed)) for reproducibility.
        """
        if not pnls:
            return [
                {"scenario": i, "expectancy": 0.0, "max_drawdown_pct": 0.0}
                for i in range(n)
            ]

        scenarios: List[dict] = []
        for i in range(n):
            sample = [self._rng.choice(pnls) for _ in range(len(pnls))]
            expectancy = _safe_mean(sample)
            cum = []
            running = 0.0
            for p in sample:
                running += p
                cum.append(running)
            max_dd = _compute_max_drawdown_pct(cum)
            scenarios.append({
                "scenario": i,
                "expectancy": expectancy,
                "max_drawdown_pct": max_dd,
            })

        return scenarios

    def classify_strategy(
        self, metrics: StrategyDurabilityMetrics
    ) -> AlphaDurabilityClassification:
        """Classify a strategy's alpha durability.

        Priority order: INVALIDATED → COLLAPSING → FRAGILE → ROBUST.
        """
        # INVALIDATED: execution expectancy deeply negative or extremely short half-life
        if (
            metrics.execution_adjusted_expectancy < -5.0
            or metrics.alpha_half_life < 10
        ):
            return AlphaDurabilityClassification.INVALIDATED

        # COLLAPSING: accelerating decay or very low robustness
        if (
            metrics.decay_acceleration < -0.15
            or metrics.robustness_score < 40
        ):
            return AlphaDurabilityClassification.COLLAPSING

        # ROBUST: high robustness and stable decay
        if (
            metrics.robustness_score >= 70
            and metrics.decay_acceleration >= -0.05
        ):
            return AlphaDurabilityClassification.ROBUST

        # FRAGILE: moderate robustness or acceptable half-life
        if (
            metrics.robustness_score >= 40
            or (metrics.alpha_half_life > 50 and metrics.decay_acceleration > -0.15)
        ):
            return AlphaDurabilityClassification.FRAGILE

        # Default fallback
        return AlphaDurabilityClassification.COLLAPSING

    def compute_robustness_score(self, metrics: StrategyDurabilityMetrics) -> float:
        """Compute composite robustness score in [0, 100].

        Components:
          - half_life score:        min(100, half_life) / 100 * 30
          - execution_expectancy:   min(100, max(0, exec_exp + 50)) / 100 * 30
          - confidence_calibration: confidence_calibration_persistence * 20
          - decay_stability:        max(0, 1 + decay_acceleration) * 20
        """
        half_life_score = min(100.0, metrics.alpha_half_life) / 100.0 * 30.0
        exec_exp_score = min(100.0, max(0.0, metrics.execution_adjusted_expectancy + 50.0)) / 100.0 * 30.0
        calib_score = max(0.0, min(1.0, metrics.confidence_calibration_persistence)) * 20.0
        decay_score = max(0.0, 1.0 + metrics.decay_acceleration) * 20.0

        total = half_life_score + exec_exp_score + calib_score + decay_score
        return max(0.0, min(100.0, total))

    def analyze_strategy(self, strategy: str) -> Optional[StrategyDurabilityMetrics]:
        """Analyze a single strategy and return its durability metrics.

        Returns None if sample_size < 10.
        """
        try:
            with self._lock:
                outcomes = list(self._by_strategy.get(strategy, []))

            if len(outcomes) < _MIN_STRATEGY_SAMPLE:
                return None

            pnls = [float(t.get("pnl", 0.0)) for t in outcomes]

            alpha_half_life = self.compute_alpha_half_life(pnls)
            decay_acceleration = self.compute_decay_acceleration(pnls)
            execution_adjusted_expectancy = self.compute_execution_adjusted_expectancy(outcomes)

            # Latency cost proxy: 0.5 per trade (small constant overhead)
            latency_adjusted_expectancy = execution_adjusted_expectancy - 0.5

            # Spread cost proxy: additional 0.3 per trade
            spread_adjusted_expectancy = latency_adjusted_expectancy - 0.3

            volatility_segmented_alpha = self.compute_volatility_segmented_alpha(outcomes)

            # Confidence calibration persistence: Pearson(confidence, win_flag)
            confidences = [float(t.get("confidence", 0.5)) for t in outcomes]
            win_flags = [1.0 if t.get("outcome", "") == "win" else 0.0 for t in outcomes]
            confidence_calibration_persistence = max(0.0, _pearson(confidences, win_flags))

            # Build partial metrics for robustness computation
            partial = StrategyDurabilityMetrics(
                strategy=strategy,
                sample_size=len(outcomes),
                alpha_half_life=alpha_half_life,
                decay_acceleration=decay_acceleration,
                execution_adjusted_expectancy=execution_adjusted_expectancy,
                latency_adjusted_expectancy=latency_adjusted_expectancy,
                spread_adjusted_expectancy=spread_adjusted_expectancy,
                volatility_segmented_alpha=volatility_segmented_alpha,
                confidence_calibration_persistence=confidence_calibration_persistence,
                classification=AlphaDurabilityClassification.FRAGILE,  # placeholder
                robustness_score=0.0,  # placeholder
            )

            robustness_score = self.compute_robustness_score(partial)
            partial.robustness_score = robustness_score

            classification = self.classify_strategy(partial)
            partial.classification = classification

            return partial
        except Exception as exc:
            logger.error("analyze_strategy(%s) failed: %s", strategy, exc)
            return None

    def generate_report(self) -> AlphaDurabilityReport:
        """Generate full alpha durability report across all strategies.

        Advisory only. Must never alter execution, positions, or bypass governance.
        """
        try:
            with self._lock:
                all_trades = list(self._trades)
                strategy_names = list(self._by_strategy.keys())

            strategies: Dict[str, StrategyDurabilityMetrics] = {}
            for s in strategy_names:
                m = self.analyze_strategy(s)
                if m is not None:
                    strategies[s] = m

            # Portfolio classification: worst across strategies
            if strategies:
                portfolio_classification = _worst_classification(
                    [m.classification for m in strategies.values()]
                )
                portfolio_robustness_score = min(
                    m.robustness_score for m in strategies.values()
                )
                half_lives = [m.alpha_half_life for m in strategies.values()]
                alpha_half_life_portfolio = _harmonic_mean(half_lives)
            else:
                portfolio_classification = AlphaDurabilityClassification.INVALIDATED
                portfolio_robustness_score = 0.0
                alpha_half_life_portfolio = 0.0

            # Monte Carlo on all PnLs combined (10 scenarios for report)
            all_pnls = [float(t.get("pnl", 0.0)) for t in all_trades]
            monte_carlo_scenarios = self.run_monte_carlo_degradation(all_pnls, n=10)

            return AlphaDurabilityReport(
                generated_at=_now_iso(),
                window=self._window,
                seed=self._seed,
                strategies=strategies,
                portfolio_classification=portfolio_classification,
                portfolio_robustness_score=portfolio_robustness_score,
                alpha_half_life_portfolio=alpha_half_life_portfolio,
                monte_carlo_degradation_scenarios=monte_carlo_scenarios,
                trades_analyzed=len(all_trades),
            )
        except Exception as exc:
            logger.error("generate_report failed: %s", exc)
            return AlphaDurabilityReport(
                generated_at=_now_iso(),
                window=self._window,
                seed=self._seed,
                strategies={},
                portfolio_classification=AlphaDurabilityClassification.INVALIDATED,
                portfolio_robustness_score=0.0,
                alpha_half_life_portfolio=0.0,
                monte_carlo_degradation_scenarios=[],
                trades_analyzed=0,
            )


# ── Singleton ──────────────────────────────────────────────────────────────────

_alpha_lab_instance: Optional[AlphaDurabilityLab] = None
_alpha_lab_lock = threading.Lock()


def get_alpha_lab(
    outcomes_path: str = "data/logs/trade_outcomes.jsonl",
    window: int = _WINDOW_DEFAULT,
    seed: int = _SEED_DEFAULT,
    monte_carlo_n: int = _MONTE_CARLO_N_DEFAULT,
) -> AlphaDurabilityLab:
    """Return the module-level AlphaDurabilityLab singleton (double-checked locking)."""
    global _alpha_lab_instance
    if _alpha_lab_instance is None:
        with _alpha_lab_lock:
            if _alpha_lab_instance is None:
                _alpha_lab_instance = AlphaDurabilityLab(
                    outcomes_path=outcomes_path,
                    window=window,
                    seed=seed,
                    monte_carlo_n=monte_carlo_n,
                )
    return _alpha_lab_instance
