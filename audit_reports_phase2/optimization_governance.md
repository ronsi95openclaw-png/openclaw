# Adaptive Governance Hardening — Shadow Optimization
**File:** `runtime/shadow_optimization.py`
**Date:** 2026-05-22

## Problem
Claude Opus weight recommendations were applied directly to `strategy_weights.json` with no validation gate. A bad recommendation (low sample size, low confidence, or large weight jump) could immediately shift strategy behaviour, including disabling a working strategy or over-weighting a strategy with a short lucky streak.

## What Was Built

### `ShadowOptimizationEngine`
Four-gate promotion system that separates recommendation intake from live application.

**4 validation gates (in order):**
| Gate | Threshold | Rejection reason |
|------|-----------|-----------------|
| Minimum trades | ≥ 10 | "Insufficient sample: N trades < 10 required" |
| Confidence | ≥ 0.65 | "Confidence X below minimum 0.650" |
| Step-size cap | Δweight ≤ 0.30 | "Weight change X exceeds max step 0.30" |
| Boundary guard | No jump > 0.20 from 0.0 or 1.0 | "Weight at boundary; max jump 0.20" |

**EWMA bias:** Recent outcomes weighted at 1.5× vs older outcomes. Prevents a lucky recent streak from masking poor historical performance.

**Candidate lifecycle:** `PENDING → APPROVED | REJECTED | ROLLED_BACK`

**Key methods:**
- `apply_candidate(strategy, new_weight, source, actual_trades, raw_outcomes)` — registers pending change
- `promote(strategy)` → `(bool, reason)` — applies only if all gates pass
- `rollback(strategy)` — reverts to snapshot weight
- `promote_all_eligible()` — batch promotion
- `get_pending()` / `get_status()` — inspection
- Atomic write via `os.replace()` to `data/shadow_weights.json`

## Integration
The existing `_auto_apply_opus_weights()` in `CryptoComBot` applies Claude Opus recommendations directly. **Recommended next step:** Route those recommendations through `ShadowOptimizationEngine.apply_candidate()` first, then call `promote_all_eligible()` after 10 confirmed trades.

This was deliberately left as a future integration to avoid breaking the existing nightly apply path during Phase 2.

## Chaos Test Coverage
- Weight jump > 0.30 from forced snapshot → rejected
- Low trades (< 10) → rejected
- Rollback preserves snapshot weight
- 5 concurrent `apply_candidate()` calls → no crash or corruption

## Risk Reduction
Prevents a single bad Claude Opus analysis from wiping out a well-calibrated strategy weight. The 10-trade minimum ensures any adjustment is based on observable post-adjustment performance, not just the pre-change signal history.
