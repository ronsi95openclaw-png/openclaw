# Strategy Performance Attribution — Phase 3
**File:** `research/analytics/strategy_attribution.py`
**Date:** 2026-05-23

## Problem
No mechanism to detect strategy decay, overfitting, regime blindness, or confidence miscalibration. A strategy could be losing consistently in specific regimes while its aggregate stats still looked acceptable.

## What Was Built

### `StrategyAttributionEngine`

**Inputs:** `data/logs/trade_outcomes.jsonl` (one JSON object per line)

**`StrategyMetrics` dataclass (per-strategy):**
- `win_rate`, `expectancy_usd`, `expectancy_pct`
- `avg_confidence`, `confidence_calibration_score` (Pearson r, 0–1)
- `regime_breakdown` — per-regime: `{trades, win_rate, expectancy}`
- `symbol_breakdown` — per-symbol breakdown
- `vol_adjusted_expectancy` — expectancy / stddev(pnl), 0 if no variance
- `decay_detected` (bool), `decay_severity` (0–1)
- `overfitting_score` (0–1)

**`AttributionReport`:**
- `best_regime_fit` / `worst_regime_fit` per strategy
- `regime_blind_strategies` — strategies with WR <30% in ≥5 trades in any regime
- `degraded_strategies` — strategies where decay detected
- `overfitting_warnings` — strategies with overfitting_score > 0.6
- `overall_portfolio_expectancy`

### Detection algorithms

**Decay detection (`detect_decay`):**
Split recent `window` trades into two halves. If `last_half_wr < first_half_wr - 0.15`, decay detected. Severity = `(wr_drop - 0.15) / 0.35` clamped to [0, 1].

**Overfitting detection (`detect_overfitting`):**
Score = weighted combination of:
- High WR (>70%) with small sample (<30 trades) → suspicious
- High stddev across regime win rates (>0.30) → regime-inconsistent

**Regime blindness (`detect_regime_blindness`):**
Returns list of regime labels where WR < 30% with ≥5 trades. Per Phase 3 directive: TREND_FOLLOW historically 0% WR in UNKNOWN → should be blocked.

**Confidence calibration (`confidence_calibration`):**
Bucket trades by confidence decile. Compute Pearson r between decile confidence midpoints and decile win rates. Well-calibrated strategy: r close to 1.0 (higher confidence = higher WR).

**Vol-adjusted expectancy:**
`expectancy / stddev(pnl)` — reward-to-risk ratio. Strategies with high vol-adjusted expectancy are more consistent.

## Recommended Actions (from analysis)
Based on system design and the CLAUDE.md backlog:
1. Block `TREND_FOLLOW` in `UNKNOWN` regime (backlog #1) — use `detect_regime_blindness()` output to validate
2. Auto-disable strategies with `decay_detected=True` and `decay_severity > 0.7`
3. Alert when `confidence_calibration_score < 0.3` (confidence inflation detected)

## Usage
```python
from research.analytics.strategy_attribution import StrategyAttributionEngine
engine = StrategyAttributionEngine()
engine.load_outcomes("data/logs/trade_outcomes.jsonl")
report = engine.generate_report()
for s, m in report.strategies.items():
    if m.decay_detected:
        print(f"{s}: DECAY severity={m.decay_severity:.2f}")
```
