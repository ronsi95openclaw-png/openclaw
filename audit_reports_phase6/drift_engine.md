# Audit Report — Statistical Drift Engine (Phase 6)
**Date:** 2026-05-23
**File:** `research/statistics/drift_engine.py`
**Status:** IMPLEMENTED · TESTED · 8/8 PASSING

## Summary
8-metric statistical drift detection engine monitoring for distributional
divergence between live outcomes and backtest baselines. All outputs advisory
only — never gates trades, modifies weights, or overrides governance.

## 8 Drift Metrics

| Metric | Method | What It Detects |
|--------|--------|----------------|
| LIVE_VS_BACKTEST_DIVERGENCE | z-score on mean PnL | Live outcomes drifting from backtest expectations |
| CONFIDENCE_DRIFT | EWMA half-comparison | Strategy confidence calibration degrading |
| EXPECTANCY_COLLAPSE | Thirds comparison | Terminal decline in per-trade expectancy |
| VOLATILITY_REGIME_DRIFT | Regime transition rate | Market regime instability vs historical baseline |
| STRATEGY_INSTABILITY | CV of per-strategy WR | Win rate divergence across strategies |
| ALPHA_DECAY_PERSISTENCE | AlphaValidationEngine hook | Fraction of strategies with sustained decay |
| OVERFITTING_RECURRENCE | In/out-of-sample PnL ratio | Out-of-sample underperformance vs in-sample |
| EXECUTION_DEGRADATION_CORRELATION | Pearson(fill_rate, pnl) | Execution quality eroding alpha |

## Severity Computation
- z-score thresholds: |z| < 1.0 → NONE; < 1.5 → MINOR; < 2.5 → MODERATE;
  < 3.5 → SEVERE; ≥ 3.5 → CRITICAL
- persistence_score: fraction of rolling 20-record windows in drifted state
- overall_severity: maximum across all findings

## DriftReport.severity_score
NONE=0, MINOR=20, MODERATE=40, SEVERE=70, CRITICAL=100 (max across findings)

## recommended_governance_action
- severity_score ≥ 70 → "ESCALATE"
- severity_score ≥ 40 → "INVESTIGATE"
- else → "MONITOR"

## Incremental Behavior
- `load_backtest_outcomes()` graceful on missing file — returns 0
- Missing backtest triggers CRITICAL LIVE_VS_BACKTEST finding (by design — no
  baseline = maximum uncertainty, operator should investigate)

## Pearson Correlation (inline, no scipy)
```python
def _pearson(xs, ys):
    # standard formula with zero-variance guard (returns 0.0)
```

## Test Results (8/8)
| Test | Result |
|------|--------|
| Empty outcomes no crash | PASSED |
| Expectancy collapse MODERATE+ (30W+30L) | PASSED |
| Live vs backtest divergence MODERATE+ | PASSED |
| Stable strategy: collapse/confidence/overfitting NONE/MINOR | PASSED |
| severity_score in [0, 100] | PASSED |
| Extreme alternating → ESCALATE governance action | PASSED |
| Pearson edge cases (zero variance = 0, perfect = 1.0) | PASSED |
| persist_report() creates valid JSONL | PASSED |

## Advisory Contract
This module is strictly advisory. It does NOT: gate trade entry, write to
strategy_weights.json, call CapitalPreservationEngine, or emit any EventStore
events that influence bot behavior.
