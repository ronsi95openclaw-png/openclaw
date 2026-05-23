"""Strategy performance attribution engine for OpenClaw.

Reads trade outcomes from ``data/logs/trade_outcomes.jsonl`` and produces
a per-strategy breakdown that covers win rate, expectancy, regime fitness,
decay, overfitting, confidence calibration, and volatility-adjusted returns.

Typical usage
-------------
    from research.analytics.strategy_attribution import StrategyAttributionEngine

    engine = StrategyAttributionEngine()
    engine.load_outcomes("data/logs/trade_outcomes.jsonl")
    report = engine.generate_report()
    print(report.degraded_strategies)

Thread safety
-------------
All public methods acquire ``_lock`` before accessing the shared trade list.
``load_outcomes`` replaces the list atomically under the same lock.
"""
from __future__ import annotations

import json
import logging
import math
import statistics
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("openclaw.research.analytics.strategy_attribution")

# ── Constants ─────────────────────────────────────────────────────────────────

KNOWN_STRATEGIES = [
    "EMA_CROSS",
    "RSI_MEAN_REVERT",
    "BREAKOUT",
    "BOLLINGER",
    "TREND_FOLLOW",
]

KNOWN_REGIMES = [
    "TRENDING_BULL",
    "TRENDING_BEAR",
    "RANGING",
    "MEAN_REVERTING",
    "VOL_COMPRESSION",
    "VOL_EXPANSION",
    "MOMENTUM_BULL",
    "MOMENTUM_BEAR",
    "NEWS_SPIKE",
    "UNKNOWN",
]

_DECAY_WINDOW     = 10   # trades per half-window used in decay detection
_DECAY_THRESHOLD  = 0.15 # WR drop required to flag decay
_MIN_REGIME_TRADES = 5   # min trades in a regime to report blindness
_BLIND_WR_CUTOFF  = 0.30 # win rate below this = regime blind
_OVERFIT_MIN_TRADES = 30 # fewer than this + high WR → possible overfit
_OVERFIT_HIGH_WR    = 0.70
_OVERFIT_REGIME_STDDEV = 0.30


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class RegimePerf:
    trades:     int
    win_rate:   float
    expectancy: float   # average PnL per trade in USD


@dataclass
class StrategyMetrics:
    strategy:                   str
    total_trades:               int
    win_rate:                   float
    expectancy_usd:             float
    expectancy_pct:             float
    avg_confidence:             float
    confidence_calibration_score: float   # Pearson r, 0–1
    regime_breakdown:           Dict[str, RegimePerf]
    symbol_breakdown:           Dict[str, RegimePerf]
    vol_adjusted_expectancy:    float
    decay_detected:             bool
    decay_severity:             float     # 0–1
    overfitting_score:          float     # 0–1


@dataclass
class AttributionReport:
    generated_at:               str
    total_trades_analyzed:      int
    strategies:                 Dict[str, StrategyMetrics]
    best_regime_fit:            Dict[str, str]    # strategy → best regime
    worst_regime_fit:           Dict[str, str]    # strategy → worst regime
    regime_blind_strategies:    List[str]
    degraded_strategies:        List[str]
    overfitting_warnings:       List[str]
    overall_portfolio_expectancy: float


# ── Trade record (internal) ───────────────────────────────────────────────────

@dataclass
class _TradeRecord:
    id:           str
    symbol:       str
    strategy:     str
    side:         str
    entry_price:  float
    exit_price:   float
    pnl:          float
    win:          bool
    regime_label: str
    confidence:   float
    duration_s:   float


def _parse_trade(raw: Dict[str, Any]) -> Optional[_TradeRecord]:
    """Parse one raw dict into a ``_TradeRecord``.  Returns None on error."""
    try:
        # Derive 'win' from the 'outcome' field if the bool 'win' key is absent
        outcome = raw.get("outcome", "")
        win_raw = raw.get("win")
        if win_raw is None:
            win = (outcome == "win")
        else:
            win = bool(win_raw)

        # Regime may be stored as 'regime' or 'regime_label'
        regime = raw.get("regime_label") or raw.get("regime") or "UNKNOWN"

        return _TradeRecord(
            id           = str(raw.get("id", "")),
            symbol       = str(raw.get("symbol", "")),
            strategy     = str(raw.get("strategy", "")),
            side         = str(raw.get("side", "")),
            entry_price  = float(raw.get("entry_price", 0.0)),
            exit_price   = float(raw.get("exit_price", 0.0)),
            pnl          = float(raw.get("pnl", 0.0)),
            win          = win,
            regime_label = regime,
            confidence   = float(raw.get("confidence", 0.0)),
            duration_s   = float(raw.get("duration_s", 0.0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.debug("_parse_trade: skipping malformed record (%s): %r", exc, raw)
        return None


# ── Engine ────────────────────────────────────────────────────────────────────

class StrategyAttributionEngine:
    """Compute strategy-level performance attribution from live trade data.

    Parameters
    ----------
    None required — call ``load_outcomes`` before ``generate_report``.
    """

    def __init__(self) -> None:
        self._lock:   threading.Lock         = threading.Lock()
        self._trades: List[_TradeRecord]     = []

    # ── Data loading ──────────────────────────────────────────────────────────

    def load_outcomes(self, path: str) -> int:
        """Load trade outcomes from a JSONL file.

        Parameters
        ----------
        path:
            Path to ``trade_outcomes.jsonl``.

        Returns
        -------
        int
            Number of valid records loaded.
        """
        p = Path(path)
        if not p.exists():
            logger.warning("load_outcomes: file not found — %s", path)
            with self._lock:
                self._trades = []
            return 0

        records: List[_TradeRecord] = []
        try:
            with p.open("r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError as exc:
                        logger.debug("load_outcomes: JSON error on line %d: %s", lineno, exc)
                        continue
                    rec = _parse_trade(raw)
                    if rec is not None:
                        records.append(rec)
        except OSError as exc:
            logger.error("load_outcomes: cannot read %s: %s", path, exc)
            with self._lock:
                self._trades = []
            return 0

        with self._lock:
            self._trades = records

        logger.info("load_outcomes: loaded %d trade records from %s", len(records), path)
        return len(records)

    # ── Public analysis API ───────────────────────────────────────────────────

    def generate_report(self) -> AttributionReport:
        """Produce a complete ``AttributionReport`` for all strategies."""
        with self._lock:
            trades = list(self._trades)

        if not trades:
            return AttributionReport(
                generated_at              = datetime.now(timezone.utc).isoformat(),
                total_trades_analyzed     = 0,
                strategies                = {},
                best_regime_fit           = {},
                worst_regime_fit          = {},
                regime_blind_strategies   = [],
                degraded_strategies       = [],
                overfitting_warnings      = [],
                overall_portfolio_expectancy = 0.0,
            )

        # Group by strategy
        by_strategy: Dict[str, List[_TradeRecord]] = {}
        for t in trades:
            by_strategy.setdefault(t.strategy, []).append(t)

        strategy_metrics: Dict[str, StrategyMetrics] = {}
        for strat, strat_trades in by_strategy.items():
            strategy_metrics[strat] = self._compute_strategy_metrics(
                strat, strat_trades
            )

        # Cross-strategy summaries
        best_regime_fit:  Dict[str, str] = {}
        worst_regime_fit: Dict[str, str] = {}
        regime_blind_strategies: List[str] = []
        degraded_strategies:     List[str] = []
        overfitting_warnings:    List[str] = []

        for strat, m in strategy_metrics.items():
            # Best / worst regime
            if m.regime_breakdown:
                best = max(
                    m.regime_breakdown.items(),
                    key=lambda kv: kv[1].expectancy
                )
                worst = min(
                    m.regime_breakdown.items(),
                    key=lambda kv: kv[1].expectancy
                )
                best_regime_fit[strat]  = best[0]
                worst_regime_fit[strat] = worst[0]

            # Regime blindness
            blind_regimes = self.detect_regime_blindness(strat)
            if blind_regimes:
                regime_blind_strategies.append(strat)

            # Decay
            if m.decay_detected:
                degraded_strategies.append(strat)

            # Overfitting
            if m.overfitting_score > 0.5:
                overfitting_warnings.append(
                    f"{strat}: overfitting_score={m.overfitting_score:.2f}"
                )

        # Portfolio-level expectancy (equal weight across strategies)
        if strategy_metrics:
            overall_portfolio_expectancy = statistics.mean(
                m.expectancy_usd for m in strategy_metrics.values()
            )
        else:
            overall_portfolio_expectancy = 0.0

        return AttributionReport(
            generated_at                 = datetime.now(timezone.utc).isoformat(),
            total_trades_analyzed        = len(trades),
            strategies                   = strategy_metrics,
            best_regime_fit              = best_regime_fit,
            worst_regime_fit             = worst_regime_fit,
            regime_blind_strategies      = sorted(set(regime_blind_strategies)),
            degraded_strategies          = sorted(set(degraded_strategies)),
            overfitting_warnings         = overfitting_warnings,
            overall_portfolio_expectancy = overall_portfolio_expectancy,
        )

    def detect_decay(
        self, strategy: str, window: int = 20
    ) -> Tuple[bool, float]:
        """Compare recent performance against prior performance.

        Parameters
        ----------
        strategy:
            Strategy name to analyse.
        window:
            Total number of trades to examine (split into two equal halves).

        Returns
        -------
        (decaying, severity)
            ``decaying`` is True when the recent half-window win rate has
            fallen more than ``_DECAY_THRESHOLD`` below the prior half.
            ``severity`` is in [0, 1] and is proportional to the drop.
        """
        with self._lock:
            trades = [t for t in self._trades if t.strategy == strategy]

        half = window // 2
        if len(trades) < window:
            return False, 0.0

        recent_trades = trades[-half:]
        prior_trades  = trades[-(window):-half]

        if not prior_trades or not recent_trades:
            return False, 0.0

        recent_wr = sum(1 for t in recent_trades if t.win) / len(recent_trades)
        prior_wr  = sum(1 for t in prior_trades  if t.win) / len(prior_trades)

        drop = prior_wr - recent_wr
        decaying = drop > _DECAY_THRESHOLD

        # Severity: map drop onto [0, 1] where 1 = drop of 0.5 or more
        severity = min(1.0, max(0.0, drop / 0.5)) if decaying else 0.0

        return decaying, round(severity, 4)

    def detect_overfitting(self, strategy: str) -> float:
        """Return an overfitting score in [0, 1].

        A high score indicates the strategy may be overfit:

        * win_rate > 0.70 with fewer than 30 trades, OR
        * regime win-rates are highly inconsistent (stddev > 0.30).

        Both conditions are scored on [0, 1] and the maximum is returned.
        """
        with self._lock:
            trades = [t for t in self._trades if t.strategy == strategy]

        if not trades:
            return 0.0

        n      = len(trades)
        wr     = sum(1 for t in trades if t.win) / n
        score1 = 0.0
        score2 = 0.0

        # Condition 1: suspiciously high WR with few trades
        if wr > _OVERFIT_HIGH_WR and n < _OVERFIT_MIN_TRADES:
            # More extreme → higher score
            wr_excess    = (wr - _OVERFIT_HIGH_WR) / (1.0 - _OVERFIT_HIGH_WR)
            sample_ratio = 1.0 - (n / _OVERFIT_MIN_TRADES)  # 1 when n=0, 0 when n=30
            score1 = min(1.0, wr_excess * sample_ratio * 2.0)

        # Condition 2: inconsistent regime win-rates
        regime_wrs = self._regime_win_rates(trades)
        if len(regime_wrs) >= 2:
            stddev = statistics.stdev(regime_wrs.values())
            if stddev > _OVERFIT_REGIME_STDDEV:
                score2 = min(1.0, stddev / 0.5)

        return round(max(score1, score2), 4)

    def detect_regime_blindness(self, strategy: str) -> List[str]:
        """Return regimes where the strategy wins less than 30 % of the time.

        Only considers regimes with at least ``_MIN_REGIME_TRADES`` completed
        trades (to avoid statistical noise from tiny samples).
        """
        with self._lock:
            trades = [t for t in self._trades if t.strategy == strategy]

        blind: List[str] = []
        by_regime = _group_by(trades, lambda t: t.regime_label)
        for regime, rt in by_regime.items():
            if len(rt) < _MIN_REGIME_TRADES:
                continue
            wr = sum(1 for t in rt if t.win) / len(rt)
            if wr < _BLIND_WR_CUTOFF:
                blind.append(regime)
        return sorted(blind)

    def confidence_calibration(self, strategy: str) -> float:
        """Measure how well the strategy's confidence score predicts win rate.

        Trades are bucketed into 10 confidence deciles.  Pearson r between
        the bucket midpoint and the bucket win rate is computed.  Returns a
        score in [0, 1] where 1 = perfect calibration.

        Returns 0.0 if there are fewer than 10 trades or no variance.
        """
        with self._lock:
            trades = [t for t in self._trades if t.strategy == strategy]

        if len(trades) < 10:
            return 0.0

        # Bucket into 10 deciles by confidence
        n_buckets = 10
        buckets: Dict[int, List[_TradeRecord]] = {i: [] for i in range(n_buckets)}
        for t in trades:
            bucket_idx = min(int(t.confidence * n_buckets), n_buckets - 1)
            buckets[bucket_idx].append(t)

        xs: List[float] = []
        ys: List[float] = []
        for idx in range(n_buckets):
            bt = buckets[idx]
            if not bt:
                continue
            midpoint = (idx + 0.5) / n_buckets
            win_rate  = sum(1 for t in bt if t.win) / len(bt)
            xs.append(midpoint)
            ys.append(win_rate)

        if len(xs) < 2:
            return 0.0

        r = _pearson_r(xs, ys)
        # Map from [-1, 1] to [0, 1]: negative correlation is still 0
        return round(max(0.0, r), 4)

    def get_vol_adjusted_expectancy(self, strategy: str) -> float:
        """Return expectancy / stddev(pnl).

        This is a Sharpe-like metric for the trade PnL distribution.
        Returns 0.0 when the PnL standard deviation is zero or there are
        fewer than 2 trades.
        """
        with self._lock:
            trades = [t for t in self._trades if t.strategy == strategy]

        if len(trades) < 2:
            return 0.0

        pnls   = [t.pnl for t in trades]
        mean   = statistics.mean(pnls)
        stddev = statistics.stdev(pnls)

        if stddev == 0.0:
            return 0.0

        return round(mean / stddev, 4)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _compute_strategy_metrics(
        self, strategy: str, trades: List[_TradeRecord]
    ) -> StrategyMetrics:
        n       = len(trades)
        wins    = [t for t in trades if t.win]
        win_rate = len(wins) / n if n else 0.0

        pnls       = [t.pnl for t in trades]
        exp_usd    = statistics.mean(pnls) if pnls else 0.0
        avg_conf   = statistics.mean(t.confidence for t in trades) if trades else 0.0

        # Expectancy as % of entry price
        entry_notionals = [
            abs(t.exit_price - t.entry_price) / t.entry_price
            for t in trades
            if t.entry_price != 0.0
        ]
        exp_pct = statistics.mean(entry_notionals) if entry_notionals else 0.0
        # Sign: positive if winning expectancy, negative otherwise
        if exp_usd < 0:
            exp_pct = -exp_pct

        # Regime breakdown
        regime_breakdown: Dict[str, RegimePerf] = {}
        by_regime = _group_by(trades, lambda t: t.regime_label)
        for regime, rt in by_regime.items():
            rn      = len(rt)
            rwr     = sum(1 for t in rt if t.win) / rn
            rexp    = statistics.mean(t.pnl for t in rt)
            regime_breakdown[regime] = RegimePerf(
                trades     = rn,
                win_rate   = round(rwr, 4),
                expectancy = round(rexp, 4),
            )

        # Symbol breakdown
        symbol_breakdown: Dict[str, RegimePerf] = {}
        by_symbol = _group_by(trades, lambda t: t.symbol)
        for sym, st in by_symbol.items():
            sn   = len(st)
            swr  = sum(1 for t in st if t.win) / sn
            sexp = statistics.mean(t.pnl for t in st)
            symbol_breakdown[sym] = RegimePerf(
                trades     = sn,
                win_rate   = round(swr, 4),
                expectancy = round(sexp, 4),
            )

        decay_detected, decay_severity = self._detect_decay_internal(trades)
        overfitting_score = self._detect_overfitting_internal(trades)
        calibration       = self._confidence_calibration_internal(trades)
        vol_adj_exp       = self._vol_adjusted_expectancy_internal(trades)

        return StrategyMetrics(
            strategy                    = strategy,
            total_trades                = n,
            win_rate                    = round(win_rate, 4),
            expectancy_usd              = round(exp_usd, 4),
            expectancy_pct              = round(exp_pct, 6),
            avg_confidence              = round(avg_conf, 4),
            confidence_calibration_score = calibration,
            regime_breakdown            = regime_breakdown,
            symbol_breakdown            = symbol_breakdown,
            vol_adjusted_expectancy     = vol_adj_exp,
            decay_detected              = decay_detected,
            decay_severity              = decay_severity,
            overfitting_score           = overfitting_score,
        )

    # Internal versions that accept an already-filtered trade list
    # (avoid re-acquiring the lock when called from _compute_strategy_metrics)

    def _detect_decay_internal(
        self, trades: List[_TradeRecord], window: int = 20
    ) -> Tuple[bool, float]:
        half = window // 2
        if len(trades) < window:
            return False, 0.0
        recent = trades[-half:]
        prior  = trades[-(window):-half]
        if not prior or not recent:
            return False, 0.0
        recent_wr = sum(1 for t in recent if t.win) / len(recent)
        prior_wr  = sum(1 for t in prior  if t.win) / len(prior)
        drop      = prior_wr - recent_wr
        decaying  = drop > _DECAY_THRESHOLD
        severity  = min(1.0, max(0.0, drop / 0.5)) if decaying else 0.0
        return decaying, round(severity, 4)

    def _detect_overfitting_internal(
        self, trades: List[_TradeRecord]
    ) -> float:
        if not trades:
            return 0.0
        n   = len(trades)
        wr  = sum(1 for t in trades if t.win) / n
        s1  = 0.0
        s2  = 0.0
        if wr > _OVERFIT_HIGH_WR and n < _OVERFIT_MIN_TRADES:
            wr_excess    = (wr - _OVERFIT_HIGH_WR) / (1.0 - _OVERFIT_HIGH_WR)
            sample_ratio = 1.0 - (n / _OVERFIT_MIN_TRADES)
            s1 = min(1.0, wr_excess * sample_ratio * 2.0)
        regime_wrs = self._regime_win_rates(trades)
        if len(regime_wrs) >= 2:
            stddev = statistics.stdev(regime_wrs.values())
            if stddev > _OVERFIT_REGIME_STDDEV:
                s2 = min(1.0, stddev / 0.5)
        return round(max(s1, s2), 4)

    def _confidence_calibration_internal(
        self, trades: List[_TradeRecord]
    ) -> float:
        if len(trades) < 10:
            return 0.0
        n_buckets = 10
        buckets: Dict[int, List[_TradeRecord]] = {i: [] for i in range(n_buckets)}
        for t in trades:
            idx = min(int(t.confidence * n_buckets), n_buckets - 1)
            buckets[idx].append(t)
        xs: List[float] = []
        ys: List[float] = []
        for idx in range(n_buckets):
            bt = buckets[idx]
            if not bt:
                continue
            xs.append((idx + 0.5) / n_buckets)
            ys.append(sum(1 for t in bt if t.win) / len(bt))
        if len(xs) < 2:
            return 0.0
        return round(max(0.0, _pearson_r(xs, ys)), 4)

    def _vol_adjusted_expectancy_internal(
        self, trades: List[_TradeRecord]
    ) -> float:
        if len(trades) < 2:
            return 0.0
        pnls   = [t.pnl for t in trades]
        mean   = statistics.mean(pnls)
        stddev = statistics.stdev(pnls)
        if stddev == 0.0:
            return 0.0
        return round(mean / stddev, 4)

    @staticmethod
    def _regime_win_rates(
        trades: List[_TradeRecord],
    ) -> Dict[str, float]:
        by_regime = _group_by(trades, lambda t: t.regime_label)
        return {
            regime: sum(1 for t in rt if t.win) / len(rt)
            for regime, rt in by_regime.items()
            if rt
        }


# ── Pure utility functions ────────────────────────────────────────────────────

def _group_by(items, key_fn):
    """Group *items* into a dict of lists using *key_fn*."""
    result: Dict[Any, List] = {}
    for item in items:
        k = key_fn(item)
        result.setdefault(k, []).append(item)
    return result


def _pearson_r(xs: List[float], ys: List[float]) -> float:
    """Compute Pearson correlation coefficient between xs and ys.

    Returns 0.0 if either series has zero variance.
    """
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    cov  = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)

    denom = math.sqrt(var_x * var_y)
    if denom == 0.0:
        return 0.0

    return cov / denom
