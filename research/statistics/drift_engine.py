"""Statistical Drift Detection Engine for OpenClaw.

Monitors for distributional drift between live trade outcomes and backtest
baselines, across eight distinct drift metrics. All outputs are advisory only
and must never gate trades, modify strategy weights, or override governance.

Thread safety: double-checked locking for singleton; per-method _lock held
during all mutable state access. File I/O uses fcntl.LOCK_SH for reads and
fcntl.LOCK_EX for appends. Fail-closed: exceptions return safe defaults.
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

logger = logging.getLogger("openclaw.research.statistics.drift_engine")

# ── Constants ──────────────────────────────────────────────────────────────────

_EWMA_ALPHA_DEFAULT: float = 0.1
_WINDOW_DEFAULT: int = 200
_Z_THRESHOLD_DEFAULT: float = 2.5
_PERSISTENCE_WINDOW: int = 20


# ── Enums ──────────────────────────────────────────────────────────────────────


class DriftSeverity(str, Enum):
    NONE = "NONE"
    MINOR = "MINOR"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"
    CRITICAL = "CRITICAL"


class DriftMetric(str, Enum):
    LIVE_VS_BACKTEST_DIVERGENCE = "LIVE_VS_BACKTEST_DIVERGENCE"
    CONFIDENCE_DRIFT = "CONFIDENCE_DRIFT"
    EXPECTANCY_COLLAPSE = "EXPECTANCY_COLLAPSE"
    VOLATILITY_REGIME_DRIFT = "VOLATILITY_REGIME_DRIFT"
    STRATEGY_INSTABILITY = "STRATEGY_INSTABILITY"
    ALPHA_DECAY_PERSISTENCE = "ALPHA_DECAY_PERSISTENCE"
    OVERFITTING_RECURRENCE = "OVERFITTING_RECURRENCE"
    EXECUTION_DEGRADATION_CORRELATION = "EXECUTION_DEGRADATION_CORRELATION"


# ── Dataclasses ────────────────────────────────────────────────────────────────


@dataclass
class DriftFinding:
    """Result of a single drift detection check."""

    metric: DriftMetric
    severity: DriftSeverity
    current_value: float
    baseline_value: float
    drift_ratio: float        # current / baseline; 1.0 = no drift
    z_score: float
    persistence_score: float  # 0-1: fraction of rolling sub-windows in drifted state
    description: str
    recommended_action: str   # advisory text only


@dataclass
class DriftReport:
    """Aggregated drift report across all metrics. Advisory only."""

    generated_at: str          # ISO timestamp
    window_size: int
    findings: List[DriftFinding]
    overall_severity: DriftSeverity   # max severity across findings
    severity_score: float             # 0-100
    persistence_score: float          # avg persistence of severe+ findings
    recommended_governance_action: str  # "MONITOR", "INVESTIGATE", "ESCALATE"
    trades_analyzed: int
    strategies_analyzed: int


# ── Internal helpers ───────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _severity_from_z(z: float) -> DriftSeverity:
    """Map absolute z-score to DriftSeverity."""
    az = abs(z)
    if az < 1.0:
        return DriftSeverity.NONE
    if az < 1.5:
        return DriftSeverity.MINOR
    if az < 2.5:
        return DriftSeverity.MODERATE
    if az < 3.5:
        return DriftSeverity.SEVERE
    return DriftSeverity.CRITICAL


_SEVERITY_SCORE: Dict[DriftSeverity, float] = {
    DriftSeverity.NONE: 0.0,
    DriftSeverity.MINOR: 20.0,
    DriftSeverity.MODERATE: 40.0,
    DriftSeverity.SEVERE: 70.0,
    DriftSeverity.CRITICAL: 100.0,
}

_SEVERITY_ORDER: Dict[DriftSeverity, int] = {
    DriftSeverity.NONE: 0,
    DriftSeverity.MINOR: 1,
    DriftSeverity.MODERATE: 2,
    DriftSeverity.SEVERE: 3,
    DriftSeverity.CRITICAL: 4,
}


def _max_severity(severities: List[DriftSeverity]) -> DriftSeverity:
    if not severities:
        return DriftSeverity.NONE
    return max(severities, key=lambda s: _SEVERITY_ORDER[s])


def _ewma(values: List[float], alpha: float) -> float:
    """Exponentially weighted moving average."""
    if not values:
        return 0.0
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1.0 - alpha) * result
    return result


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


def _advisory_for_severity(severity: DriftSeverity, context: str = "") -> str:
    if severity == DriftSeverity.NONE:
        return f"No action required. {context}".strip()
    if severity == DriftSeverity.MINOR:
        return f"Monitor closely. {context}".strip()
    if severity == DriftSeverity.MODERATE:
        return f"Investigate drift cause. Consider reducing position sizing. {context}".strip()
    if severity == DriftSeverity.SEVERE:
        return f"Escalate to governance. Consider pausing affected strategies. {context}".strip()
    return f"Immediate escalation required. Halt affected strategies pending review. {context}".strip()


def _safe_finding(metric: DriftMetric, description: str) -> DriftFinding:
    """Return a safe (NONE severity) finding when data is insufficient."""
    return DriftFinding(
        metric=metric,
        severity=DriftSeverity.NONE,
        current_value=0.0,
        baseline_value=0.0,
        drift_ratio=1.0,
        z_score=0.0,
        persistence_score=0.0,
        description=description,
        recommended_action="Insufficient data for drift analysis.",
    )


# ── Engine ─────────────────────────────────────────────────────────────────────


class DriftEngine:
    """Statistical drift detection engine.

    Advisory only. Does not gate trades, modify weights, or override governance.
    """

    def __init__(
        self,
        outcomes_path: str = "data/logs/trade_outcomes.jsonl",
        backtest_path: str = "data/logs/backtest_outcomes.jsonl",
        window: int = _WINDOW_DEFAULT,
        z_threshold: float = _Z_THRESHOLD_DEFAULT,
        ewma_alpha: float = _EWMA_ALPHA_DEFAULT,
    ) -> None:
        self._outcomes_path = outcomes_path
        self._backtest_path = backtest_path
        self._window = window
        self._z_threshold = z_threshold
        self._ewma_alpha = ewma_alpha
        self._lock = threading.Lock()
        self._trades: List[dict] = []
        self._backtest_trades: List[dict] = []

    # ── Data loading ───────────────────────────────────────────────────────────

    def load_outcomes(self) -> int:
        """Load live trade outcomes from JSONL with shared read lock.

        Loads up to window most recent records. Returns count loaded.
        """
        trades: List[dict] = []
        try:
            p = Path(self._outcomes_path)
            if not p.exists():
                logger.warning("Outcomes file not found: %s", self._outcomes_path)
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
                            logger.debug("Skipping malformed JSONL line in %s", self._outcomes_path)
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError as exc:
            logger.error("Failed to read outcomes file: %s", exc)
            with self._lock:
                self._trades = []
            return 0

        # Keep only the most recent window records
        trades = trades[-self._window:] if len(trades) > self._window else trades
        with self._lock:
            self._trades = trades
        logger.info("Loaded %d live outcomes from %s", len(trades), self._outcomes_path)
        return len(trades)

    def load_backtest_outcomes(self) -> int:
        """Load backtest outcomes from JSONL with shared read lock.

        Returns 0 if file is missing — graceful degradation.
        """
        trades: List[dict] = []
        try:
            p = Path(self._backtest_path)
            if not p.exists():
                logger.info("Backtest file not found (OK): %s", self._backtest_path)
                with self._lock:
                    self._backtest_trades = []
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
                            logger.debug("Skipping malformed JSONL line in %s", self._backtest_path)
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError as exc:
            logger.error("Failed to read backtest file: %s", exc)
            with self._lock:
                self._backtest_trades = []
            return 0

        trades = trades[-self._window:] if len(trades) > self._window else trades
        with self._lock:
            self._backtest_trades = trades
        logger.info("Loaded %d backtest outcomes from %s", len(trades), self._backtest_path)
        return len(trades)

    # ── Snapshot helpers ───────────────────────────────────────────────────────

    def _snapshot_trades(self) -> List[dict]:
        with self._lock:
            return list(self._trades)

    def _snapshot_backtest(self) -> List[dict]:
        with self._lock:
            return list(self._backtest_trades)

    # ── Detection methods ──────────────────────────────────────────────────────

    def detect_live_vs_backtest_divergence(self) -> DriftFinding:
        """Compare mean PnL of live outcomes vs backtest outcomes.

        Advisory only. Does not gate trades, modify weights, or override governance.
        """
        metric = DriftMetric.LIVE_VS_BACKTEST_DIVERGENCE
        try:
            live = self._snapshot_trades()
            bt = self._snapshot_backtest()

            if len(live) < 2:
                return _safe_finding(metric, "Insufficient live data for divergence analysis.")

            live_pnls = [float(t.get("pnl", 0.0)) for t in live]
            bt_pnls = [float(t.get("pnl", 0.0)) for t in bt] if bt else []

            live_mean = _safe_mean(live_pnls)
            bt_mean = _safe_mean(bt_pnls) if bt_pnls else 0.0

            if abs(bt_mean) < 1e-8:
                drift_ratio = 1.0
            else:
                drift_ratio = live_mean / bt_mean

            combined = live_pnls + bt_pnls
            combined_std = _safe_std(combined) if combined else 0.0
            z_score = (live_mean - bt_mean) / (combined_std + 1e-8)

            severity = _severity_from_z(z_score)

            # Persistence: rolling sub-windows of size 20 where live_mean < bt_mean
            sub_size = min(_PERSISTENCE_WINDOW, max(1, len(live_pnls) // 4))
            drifted_windows = 0
            total_windows = 0
            for i in range(0, len(live_pnls) - sub_size + 1, max(1, sub_size // 2)):
                sub = live_pnls[i: i + sub_size]
                sub_mean = _safe_mean(sub)
                if sub_mean < bt_mean:
                    drifted_windows += 1
                total_windows += 1
            persistence = drifted_windows / total_windows if total_windows > 0 else 0.0

            desc = (
                f"Live mean PnL={live_mean:.4f} vs backtest mean={bt_mean:.4f}. "
                f"Z-score={z_score:.2f}, drift_ratio={drift_ratio:.3f}."
            )
            if not bt_pnls:
                desc = "No backtest data available; using zero baseline."

            return DriftFinding(
                metric=metric,
                severity=severity,
                current_value=live_mean,
                baseline_value=bt_mean,
                drift_ratio=drift_ratio,
                z_score=z_score,
                persistence_score=persistence,
                description=desc,
                recommended_action=_advisory_for_severity(severity, "Live vs backtest divergence."),
            )
        except Exception as exc:
            logger.error("detect_live_vs_backtest_divergence failed: %s", exc)
            return _safe_finding(metric, f"Error during analysis: {exc}")

    def detect_confidence_drift(self) -> DriftFinding:
        """Detect drift in confidence scores across the rolling window.

        Computes EWMA of confidence over first half vs second half.
        Advisory only. Does not gate trades, modify weights, or override governance.
        """
        metric = DriftMetric.CONFIDENCE_DRIFT
        try:
            trades = self._snapshot_trades()
            if len(trades) < 4:
                return _safe_finding(metric, "Insufficient data for confidence drift analysis.")

            confidences = [float(t.get("confidence", 0.5)) for t in trades]
            mid = len(confidences) // 2
            first_half = confidences[:mid]
            second_half = confidences[mid:]

            ewma_first = _ewma(first_half, self._ewma_alpha)
            ewma_second = _ewma(second_half, self._ewma_alpha)

            if abs(ewma_first) < 1e-8:
                drift_ratio = 1.0
            else:
                drift_ratio = ewma_second / ewma_first

            baseline_std = _safe_std(first_half)
            z_score = (ewma_second - ewma_first) / (baseline_std + 1e-8)
            severity = _severity_from_z(z_score)

            # Persistence: fraction of rolling 20-record windows where ewma declined
            drifted = 0
            total = 0
            sub = min(_PERSISTENCE_WINDOW, max(2, len(confidences) // 5))
            for i in range(0, len(confidences) - sub + 1, max(1, sub // 2)):
                chunk = confidences[i: i + sub]
                mid_c = len(chunk) // 2
                e1 = _ewma(chunk[:mid_c], self._ewma_alpha)
                e2 = _ewma(chunk[mid_c:], self._ewma_alpha)
                if e2 < e1:
                    drifted += 1
                total += 1
            persistence = drifted / total if total > 0 else 0.0

            desc = (
                f"Confidence EWMA: first_half={ewma_first:.4f}, second_half={ewma_second:.4f}. "
                f"drift_ratio={drift_ratio:.3f}, z={z_score:.2f}."
            )
            return DriftFinding(
                metric=metric,
                severity=severity,
                current_value=ewma_second,
                baseline_value=ewma_first,
                drift_ratio=drift_ratio,
                z_score=z_score,
                persistence_score=persistence,
                description=desc,
                recommended_action=_advisory_for_severity(severity, "Confidence calibration may be drifting."),
            )
        except Exception as exc:
            logger.error("detect_confidence_drift failed: %s", exc)
            return _safe_finding(metric, f"Error: {exc}")

    def detect_expectancy_collapse(self) -> DriftFinding:
        """Detect collapse in rolling expectancy across thirds of the window.

        CRITICAL if drift_ratio < 0.3 (collapsed to < 30% of baseline).
        Advisory only. Does not gate trades, modify weights, or override governance.
        """
        metric = DriftMetric.EXPECTANCY_COLLAPSE
        try:
            trades = self._snapshot_trades()
            if len(trades) < 9:
                return _safe_finding(metric, "Insufficient data for expectancy collapse analysis.")

            pnls = [float(t.get("pnl", 0.0)) for t in trades]
            n = len(pnls)
            chunk = max(1, n // 3)

            first_third = pnls[:chunk]
            last_third = pnls[n - chunk:]

            first_mean = _safe_mean(first_third)
            last_mean = _safe_mean(last_third)

            # Clamp to avoid divide-by-zero; treat near-zero baseline as 1e-4
            denom = first_mean if abs(first_mean) > 1e-4 else (1e-4 if first_mean >= 0 else -1e-4)
            drift_ratio = last_mean / denom

            baseline_std = _safe_std(first_third)
            z_score = (last_mean - first_mean) / (baseline_std + 1e-8)

            # Override severity for collapse threshold
            if drift_ratio < 0.3 and first_mean > 0:
                severity = DriftSeverity.CRITICAL
            else:
                severity = _severity_from_z(z_score)

            # Persistence: thirds showing decline
            mid_chunk = pnls[chunk: n - chunk]
            mid_mean = _safe_mean(mid_chunk)
            drifted = sum(1 for m in [mid_mean, last_mean] if m < first_mean)
            persistence = drifted / 2.0

            desc = (
                f"Expectancy thirds: first={first_mean:.4f}, last={last_mean:.4f}. "
                f"drift_ratio={drift_ratio:.3f}, z={z_score:.2f}."
            )
            action = _advisory_for_severity(severity, "Expectancy collapse detected.")
            if drift_ratio < 0.3 and first_mean > 0:
                action = (
                    "CRITICAL: expectancy has collapsed to <30% of baseline. "
                    "Escalate immediately. Do not increase position sizes."
                )

            return DriftFinding(
                metric=metric,
                severity=severity,
                current_value=last_mean,
                baseline_value=first_mean,
                drift_ratio=drift_ratio,
                z_score=z_score,
                persistence_score=persistence,
                description=desc,
                recommended_action=action,
            )
        except Exception as exc:
            logger.error("detect_expectancy_collapse failed: %s", exc)
            return _safe_finding(metric, f"Error: {exc}")

    def detect_volatility_regime_drift(self) -> DriftFinding:
        """Detect instability in regime transitions across the window.

        High transition rate signals regime instability.
        Advisory only. Does not gate trades, modify weights, or override governance.
        """
        metric = DriftMetric.VOLATILITY_REGIME_DRIFT
        try:
            trades = self._snapshot_trades()
            if len(trades) < 4:
                return _safe_finding(metric, "Insufficient data for regime drift analysis.")

            regimes = [str(t.get("regime", "UNKNOWN")) for t in trades]
            n = len(regimes)

            # Count total transitions
            total_transitions = sum(1 for i in range(1, n) if regimes[i] != regimes[i - 1])

            # Rolling transition rate — split into first half vs second half
            mid = n // 2
            first_transitions = sum(1 for i in range(1, mid) if regimes[i] != regimes[i - 1])
            second_transitions = sum(1 for i in range(mid + 1, n) if regimes[i] != regimes[i - 1])

            first_rate = first_transitions / max(1, mid - 1)
            second_rate = second_transitions / max(1, n - mid - 1)

            if abs(first_rate) < 1e-8:
                drift_ratio = 1.0
            else:
                drift_ratio = second_rate / first_rate

            baseline_rate = total_transitions / max(1, n - 1)
            z_score = (second_rate - first_rate) / (max(0.01, first_rate) + 1e-8)
            severity = _severity_from_z(z_score)

            # Persistence: rolling sub-windows with elevated transition rates
            sub = min(20, max(4, n // 4))
            drifted = 0
            total = 0
            for i in range(0, n - sub + 1, max(1, sub // 2)):
                chunk = regimes[i: i + sub]
                rate = sum(1 for j in range(1, len(chunk)) if chunk[j] != chunk[j - 1]) / max(1, len(chunk) - 1)
                if rate > baseline_rate * 1.5:
                    drifted += 1
                total += 1
            persistence = drifted / total if total > 0 else 0.0

            desc = (
                f"Regime transitions: first_half_rate={first_rate:.3f}, "
                f"second_half_rate={second_rate:.3f}. drift_ratio={drift_ratio:.3f}."
            )
            return DriftFinding(
                metric=metric,
                severity=severity,
                current_value=second_rate,
                baseline_value=first_rate,
                drift_ratio=drift_ratio,
                z_score=z_score,
                persistence_score=persistence,
                description=desc,
                recommended_action=_advisory_for_severity(severity, "Regime instability may indicate market structure change."),
            )
        except Exception as exc:
            logger.error("detect_volatility_regime_drift failed: %s", exc)
            return _safe_finding(metric, f"Error: {exc}")

    def detect_strategy_instability(self) -> DriftFinding:
        """Detect instability via coefficient of variation of per-strategy win rates.

        High CV = strategy instability across the portfolio.
        Advisory only. Does not gate trades, modify weights, or override governance.
        """
        metric = DriftMetric.STRATEGY_INSTABILITY
        try:
            trades = self._snapshot_trades()
            if len(trades) < 4:
                return _safe_finding(metric, "Insufficient data for strategy instability analysis.")

            # Group by strategy
            by_strategy: Dict[str, List[dict]] = {}
            for t in trades:
                s = str(t.get("strategy", "UNKNOWN"))
                by_strategy.setdefault(s, []).append(t)

            if len(by_strategy) < 2:
                return _safe_finding(metric, "Need at least 2 strategies for instability analysis.")

            win_rates = []
            for strat_trades in by_strategy.values():
                wins = sum(1 for t in strat_trades if t.get("outcome", "") == "win")
                wr = wins / len(strat_trades)
                win_rates.append(wr)

            mean_wr = _safe_mean(win_rates)
            std_wr = _safe_std(win_rates)
            cv = std_wr / (mean_wr + 1e-8)  # coefficient of variation

            # Baseline: cv=0 is no instability; cv=1 is max; use as drift_ratio
            drift_ratio = cv / 0.3 if cv < 0.3 else cv  # normalize; cv > 0.3 is elevated
            z_score = (cv - 0.2) / 0.1  # z relative to expected cv=0.2 baseline
            severity = _severity_from_z(z_score)

            desc = (
                f"Per-strategy win-rate CV={cv:.4f} across {len(win_rates)} strategies. "
                f"mean_wr={mean_wr:.3f}, std_wr={std_wr:.3f}."
            )
            return DriftFinding(
                metric=metric,
                severity=severity,
                current_value=cv,
                baseline_value=0.2,  # expected stable CV baseline
                drift_ratio=drift_ratio,
                z_score=z_score,
                persistence_score=min(1.0, cv),
                description=desc,
                recommended_action=_advisory_for_severity(severity, "Strategy win-rate dispersion elevated."),
            )
        except Exception as exc:
            logger.error("detect_strategy_instability failed: %s", exc)
            return _safe_finding(metric, f"Error: {exc}")

    def detect_alpha_decay_persistence(self) -> DriftFinding:
        """Detect sustained alpha decay across strategies using AlphaValidationEngine if available.

        Uses lazy import; falls back to raw win-rate decay analysis.
        Advisory only. Does not gate trades, modify weights, or override governance.
        """
        metric = DriftMetric.ALPHA_DECAY_PERSISTENCE
        try:
            trades = self._snapshot_trades()
            if len(trades) < 9:
                return _safe_finding(metric, "Insufficient data for alpha decay analysis.")

            # Attempt lazy import of AlphaValidationEngine
            decay_rates: List[float] = []
            try:
                from research.statistics.alpha_validation import AlphaValidationEngine  # type: ignore
                engine = AlphaValidationEngine(
                    outcomes_path=self._outcomes_path,
                    window=self._window,
                    ewma_alpha=self._ewma_alpha,
                )
                with self._lock:
                    engine._trades = list(self._trades)  # share already-loaded data

                report = engine.generate_report()
                for strat_metrics in report.strategies.values():
                    decay_rates.append(strat_metrics.win_rate_decay_rate)
            except Exception:
                # Fallback: compute win-rate decay from raw trades per strategy
                by_strategy: Dict[str, List[dict]] = {}
                for t in trades:
                    s = str(t.get("strategy", "UNKNOWN"))
                    by_strategy.setdefault(s, []).append(t)

                for strat_trades in by_strategy.values():
                    n = len(strat_trades)
                    if n < 9:
                        continue
                    chunk = max(1, n // 3)
                    t1 = strat_trades[:chunk]
                    t3 = strat_trades[n - chunk:]
                    wr1 = sum(1 for t in t1 if t.get("outcome") == "win") / max(1, len(t1))
                    wr3 = sum(1 for t in t3 if t.get("outcome") == "win") / max(1, len(t3))
                    decay_rates.append(wr3 - wr1)

            if not decay_rates:
                return _safe_finding(metric, "No strategy decay rates computed.")

            n_decaying = sum(1 for r in decay_rates if r < -0.1)
            persistence = n_decaying / len(decay_rates)
            mean_decay = _safe_mean(decay_rates)
            std_decay = _safe_std(decay_rates)

            z_score = (mean_decay - 0.0) / (std_decay + 1e-8)
            severity = _severity_from_z(z_score)

            # Elevate severity based on persistence
            if persistence >= 0.7 and severity == DriftSeverity.NONE:
                severity = DriftSeverity.MINOR
            elif persistence >= 0.9:
                severity = max(severity, DriftSeverity.MODERATE, key=lambda s: _SEVERITY_ORDER[s])

            desc = (
                f"Alpha decay: {n_decaying}/{len(decay_rates)} strategies showing sustained decay "
                f"(rate < -0.1). mean_decay={mean_decay:.4f}, persistence={persistence:.2f}."
            )
            return DriftFinding(
                metric=metric,
                severity=severity,
                current_value=mean_decay,
                baseline_value=0.0,
                drift_ratio=1.0 + abs(mean_decay),
                z_score=z_score,
                persistence_score=persistence,
                description=desc,
                recommended_action=_advisory_for_severity(severity, "Alpha decay persisting across strategies."),
            )
        except Exception as exc:
            logger.error("detect_alpha_decay_persistence failed: %s", exc)
            return _safe_finding(metric, f"Error: {exc}")

    def detect_overfitting_recurrence(self) -> DriftFinding:
        """Detect overfitting by comparing in-sample vs out-of-sample PnL.

        First half = in-sample, second half = out-of-sample.
        drift_ratio < 0.5 indicates overfitting recurrence.
        Advisory only. Does not gate trades, modify weights, or override governance.
        """
        metric = DriftMetric.OVERFITTING_RECURRENCE
        try:
            trades = self._snapshot_trades()
            if len(trades) < 4:
                return _safe_finding(metric, "Insufficient data for overfitting analysis.")

            pnls = [float(t.get("pnl", 0.0)) for t in trades]
            mid = len(pnls) // 2
            in_sample = pnls[:mid]
            out_sample = pnls[mid:]

            in_mean = _safe_mean(in_sample)
            out_mean = _safe_mean(out_sample)

            denom = in_mean if abs(in_mean) > 1e-4 else (1e-4 if in_mean >= 0 else -1e-4)
            drift_ratio = out_mean / denom

            baseline_std = _safe_std(in_sample)
            z_score = (out_mean - in_mean) / (baseline_std + 1e-8)
            severity = _severity_from_z(z_score)

            # Override: drift_ratio < 0.5 in positive territory = overfitting
            if in_mean > 0 and drift_ratio < 0.5:
                severity = max(severity, DriftSeverity.SEVERE, key=lambda s: _SEVERITY_ORDER[s])

            persistence = 1.0 - max(0.0, min(1.0, drift_ratio)) if drift_ratio < 1.0 else 0.0

            desc = (
                f"In-sample mean PnL={in_mean:.4f}, out-of-sample={out_mean:.4f}. "
                f"drift_ratio={drift_ratio:.3f}. {'Overfitting suspected.' if drift_ratio < 0.5 and in_mean > 0 else ''}"
            )
            return DriftFinding(
                metric=metric,
                severity=severity,
                current_value=out_mean,
                baseline_value=in_mean,
                drift_ratio=drift_ratio,
                z_score=z_score,
                persistence_score=persistence,
                description=desc,
                recommended_action=_advisory_for_severity(severity, "Out-of-sample degradation detected."),
            )
        except Exception as exc:
            logger.error("detect_overfitting_recurrence failed: %s", exc)
            return _safe_finding(metric, f"Error: {exc}")

    def detect_execution_degradation_correlation(self) -> DriftFinding:
        """Correlate fill rate with PnL to detect execution degrading alpha.

        Low or negative Pearson correlation = execution degrading alpha.
        Advisory only. Does not gate trades, modify weights, or override governance.
        """
        metric = DriftMetric.EXECUTION_DEGRADATION_CORRELATION
        try:
            trades = self._snapshot_trades()
            if len(trades) < 4:
                return _safe_finding(metric, "Insufficient data for execution degradation analysis.")

            # Extract fill_rate and pnl; use confidence as fill_rate proxy if missing
            fill_rates: List[float] = []
            pnls: List[float] = []
            for t in trades:
                fr = t.get("fill_rate", t.get("confidence", None))
                if fr is None:
                    continue
                fill_rates.append(float(fr))
                pnls.append(float(t.get("pnl", 0.0)))

            if len(fill_rates) < 4:
                return _safe_finding(metric, "Insufficient fill_rate data for correlation analysis.")

            corr = _pearson(fill_rates, pnls)

            # Baseline expectation: positive correlation (good fills → good PnL)
            # Low/negative = degradation
            z_score = (0.3 - corr) / 0.15  # z relative to expected corr=0.3
            severity = _severity_from_z(z_score)

            drift_ratio = corr / 0.3 if abs(0.3) > 1e-8 else 1.0

            desc = (
                f"Pearson(fill_rate, pnl)={corr:.4f} across {len(fill_rates)} trades. "
                f"Low/negative correlation suggests execution is degrading alpha."
            )
            return DriftFinding(
                metric=metric,
                severity=severity,
                current_value=corr,
                baseline_value=0.3,
                drift_ratio=drift_ratio,
                z_score=z_score,
                persistence_score=max(0.0, 1.0 - (corr + 1) / 2),
                description=desc,
                recommended_action=_advisory_for_severity(severity, "Execution quality may be degrading alpha."),
            )
        except Exception as exc:
            logger.error("detect_execution_degradation_correlation failed: %s", exc)
            return _safe_finding(metric, f"Error: {exc}")

    # ── Report generation ──────────────────────────────────────────────────────

    def generate_report(self) -> DriftReport:
        """Run all 8 drift detectors and aggregate results.

        Advisory only. Does not gate trades, modify weights, or override governance.
        """
        try:
            findings: List[DriftFinding] = [
                self.detect_live_vs_backtest_divergence(),
                self.detect_confidence_drift(),
                self.detect_expectancy_collapse(),
                self.detect_volatility_regime_drift(),
                self.detect_strategy_instability(),
                self.detect_alpha_decay_persistence(),
                self.detect_overfitting_recurrence(),
                self.detect_execution_degradation_correlation(),
            ]

            overall_severity = _max_severity([f.severity for f in findings])
            severity_score = max(_SEVERITY_SCORE.get(f.severity, 0.0) for f in findings)

            # Persistence: average across severe+ findings
            severe_findings = [
                f for f in findings
                if _SEVERITY_ORDER[f.severity] >= _SEVERITY_ORDER[DriftSeverity.SEVERE]
            ]
            persistence_score = (
                _safe_mean([f.persistence_score for f in severe_findings])
                if severe_findings
                else 0.0
            )

            if severity_score >= 70:
                governance_action = "ESCALATE"
            elif severity_score >= 40:
                governance_action = "INVESTIGATE"
            else:
                governance_action = "MONITOR"

            trades = self._snapshot_trades()
            strategies = set(str(t.get("strategy", "UNKNOWN")) for t in trades)

            return DriftReport(
                generated_at=_now_iso(),
                window_size=self._window,
                findings=findings,
                overall_severity=overall_severity,
                severity_score=severity_score,
                persistence_score=persistence_score,
                recommended_governance_action=governance_action,
                trades_analyzed=len(trades),
                strategies_analyzed=len(strategies),
            )
        except Exception as exc:
            logger.error("generate_report failed: %s", exc)
            return DriftReport(
                generated_at=_now_iso(),
                window_size=self._window,
                findings=[],
                overall_severity=DriftSeverity.NONE,
                severity_score=0.0,
                persistence_score=0.0,
                recommended_governance_action="MONITOR",
                trades_analyzed=0,
                strategies_analyzed=0,
            )

    def persist_report(
        self,
        report: DriftReport,
        output_path: str = "data/drift_reports.jsonl",
    ) -> None:
        """Append DriftReport as a JSON line with exclusive write lock."""
        try:
            p = Path(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)

            record = {
                "generated_at": report.generated_at,
                "window_size": report.window_size,
                "overall_severity": report.overall_severity.value,
                "severity_score": report.severity_score,
                "persistence_score": report.persistence_score,
                "recommended_governance_action": report.recommended_governance_action,
                "trades_analyzed": report.trades_analyzed,
                "strategies_analyzed": report.strategies_analyzed,
                "findings": [
                    {
                        "metric": f.metric.value,
                        "severity": f.severity.value,
                        "current_value": f.current_value,
                        "baseline_value": f.baseline_value,
                        "drift_ratio": f.drift_ratio,
                        "z_score": f.z_score,
                        "persistence_score": f.persistence_score,
                        "description": f.description,
                        "recommended_action": f.recommended_action,
                    }
                    for f in report.findings
                ],
            }

            with open(p, "a", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    fh.write(json.dumps(record) + "\n")
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)

            logger.info("Persisted drift report to %s", output_path)
        except Exception as exc:
            logger.error("persist_report failed: %s", exc)


# ── Singleton ──────────────────────────────────────────────────────────────────

_drift_engine_instance: Optional[DriftEngine] = None
_drift_engine_lock = threading.Lock()


def get_drift_engine(
    outcomes_path: str = "data/logs/trade_outcomes.jsonl",
    backtest_path: str = "data/logs/backtest_outcomes.jsonl",
    window: int = _WINDOW_DEFAULT,
    z_threshold: float = _Z_THRESHOLD_DEFAULT,
    ewma_alpha: float = _EWMA_ALPHA_DEFAULT,
) -> DriftEngine:
    """Return the module-level DriftEngine singleton (double-checked locking)."""
    global _drift_engine_instance
    if _drift_engine_instance is None:
        with _drift_engine_lock:
            if _drift_engine_instance is None:
                _drift_engine_instance = DriftEngine(
                    outcomes_path=outcomes_path,
                    backtest_path=backtest_path,
                    window=window,
                    z_threshold=z_threshold,
                    ewma_alpha=ewma_alpha,
                )
    return _drift_engine_instance
