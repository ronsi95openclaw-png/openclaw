# Audit Report — Alpha Durability Validation (Phase 6)
**Date:** 2026-05-23
**File:** `research/statistics/live_alpha_lab.py`
**Status:** IMPLEMENTED · TESTED · 8/8 PASSING

## Summary
Execution-aware alpha durability lab computing alpha half-life, decay acceleration,
execution/latency/spread-adjusted expectancy, volatility-segmented alpha, and
Monte Carlo degradation scenarios per strategy. Strictly advisory — never alters
execution, positions, or governance.

## AlphaDurabilityClassification

| Class | Criteria |
|-------|----------|
| ROBUST | robustness_score ≥ 70 AND decay_acceleration ≥ −0.05 |
| FRAGILE | robustness_score ≥ 40 OR (half_life > 50 AND decay_acceleration > −0.15) |
| COLLAPSING | decay_acceleration < −0.15 OR robustness_score < 40 |
| INVALIDATED | execution_adjusted_expectancy < −5.0 OR half_life < 10 |

## StrategyDurabilityMetrics (per strategy)

- **alpha_half_life**: trades until win rate falls to 50% of its peak in the record set; bounded at 1000
- **decay_acceleration**: (wr_seg3 − wr_seg2) − (wr_seg2 − wr_seg1); negative = accelerating decay
- **execution_adjusted_expectancy**: mean(pnl) − mean(confidence × 2.0) as slippage proxy
- **latency_adjusted_expectancy**: execution_adjusted − 0.1 per trade latency estimate
- **spread_adjusted_expectancy**: latency_adjusted − 0.1 per spread cost estimate
- **volatility_segmented_alpha**: `{"TRENDING": float, "RANGING": float, "UNKNOWN": float}`
- **confidence_calibration_persistence**: Pearson(confidence, win_outcome) over window

## robustness_score Composite (0–100)
```
half_life component:      min(100, half_life) / 100 × 30
expectancy component:     min(100, max(0, exec_expectancy + 50)) / 100 × 30
calibration component:    confidence_calibration_persistence × 20
decay_stability:          max(0, 1 + decay_acceleration) × 20
```

## Monte Carlo Degradation
- `random.Random(self.seed)` for reproducibility
- Bootstrap resampling (with replacement) of strategy PnL list
- Per scenario: expectancy + max_drawdown_pct from cumulative PnL curve
- Reports run 10 scenarios (not full monte_carlo_n) for speed

## Portfolio-Level Aggregation
- `portfolio_classification`: worst (most degraded) across strategies
- `portfolio_robustness_score`: minimum robustness_score
- `alpha_half_life_portfolio`: harmonic mean of strategy half-lives

## Test Results (8/8)
| Test | Result |
|------|--------|
| Empty lab no crash, trades_analyzed==0 | PASSED |
| 60 consistent wins → ROBUST or FRAGILE | PASSED |
| 30W+30L → COLLAPSING/FRAGILE/INVALIDATED | PASSED |
| alpha_half_life bounded at ≤1000 | PASSED |
| Monte Carlo returns exactly N scenarios | PASSED |
| robustness_score in [0, 100] | PASSED |
| Mixed regimes → segmented alpha dict with regime keys | PASSED |
| ROBUST + COLLAPSING portfolio → not ROBUST classification | PASSED |

## Advisory Contract
"Advisory only. Must never alter execution, positions, or bypass governance."
Outputs feed into Claude Analyst nightly review cycle only.
