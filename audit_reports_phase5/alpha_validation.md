# Audit Report — AlphaValidationEngine (Phase 5)
**Date:** 2026-05-23  
**Subsystem:** `research/statistics/alpha_validation.py`  
**Status:** IMPLEMENTED · TESTED · PASSING

## Summary
Statistical validation engine for strategy alpha signals. Reads closed trade outcomes, computes rolling Sharpe proxy, EWMA-smoothed win rate, and decay rate per strategy. Outputs structured advisory signals — never gates trades or modifies weights directly.

## Implementation Details

### AlphaSignal Enum
| Signal | Meaning |
|--------|---------|
| `STRONG_ALPHA` | Clear positive expectancy, stable win rate |
| `MARGINAL_ALPHA` | Positive expectancy but statistically weak |
| `INSUFFICIENT_DATA` | Fewer than minimum trades for significance |
| `NO_ALPHA` | Near-zero or negative expectancy |
| `ALPHA_COLLAPSED` | Win rate decaying, expectancy deteriorating |

### StrategyAlphaMetrics (per strategy)
- `sample_size` — number of trades in window
- `win_rate` — raw fraction of winning trades
- `avg_pnl` — mean PnL per trade
- `sharpe_proxy` — `avg_pnl / std_pnl * sqrt(n)` (unbounded, advisory only)
- `sharpe_ewma` — EWMA-smoothed Sharpe, alpha=0.1 (stable estimate)
- `win_rate_decay_rate` — thirds comparison: (last third WR) − (first third WR)
- `calibration_drift` — rolling confidence calibration delta
- `significance` — bounded `min(1.0, sqrt(n)*|mean|/std/3.0)`
- `signal` — derived AlphaSignal

### EWMA Smoothing
Computed iteratively: `ewma = alpha * value + (1 - alpha) * prev_ewma`  
Seeded from first trade value. Alpha=0.1 gives ~10-trade half-life.

### Win Rate Decay Detection
- Splits window into thirds
- `win_rate_decay_rate` = WR(last third) − WR(first third)
- Negative value → strategy win rate declining over window
- `ALPHA_COLLAPSED` signal triggered when decay < −0.15 AND avg_pnl ≤ 0

### Portfolio-Level Aggregation
- `portfolio_alpha_signal` = most conservative (worst) signal across all strategies
- `overall_portfolio_expectancy` = weighted average `avg_pnl` by sample size
- `total_strategies_analyzed` = count of strategies with sufficient data

### File I/O
- Reads `trade_outcomes.jsonl` with `fcntl.LOCK_SH` (shared read lock)
- Only reads `window` most recent records per strategy
- Missing or empty file returns empty outcomes (no error)

## Test Coverage (test_phase5_soak.py)
| Test | Result |
|------|--------|
| `test_alpha_validation_empty` | PASSED |
| `test_alpha_validation_synthetic_outcomes` | PASSED |

## Advisory Contract
AlphaValidationEngine is advisory only. It:
- Does NOT gate trade entry
- Does NOT modify `strategy_weights.json`
- Does NOT communicate with the trading bot directly

Recommended usage: Claude Analyst reads `generate_report()` output during nightly cycle and may recommend weight adjustments via `AdaptiveAllocator`.
