# Strategy Governance Integration — Phase 4
**File:** `runtime/strategy_governance.py`
**Date:** 2026-05-23

## Problem
`StrategyAttributionEngine` (Phase 3) detected decay, overfitting, and regime blindness, but its output was never acted on. Strategies could decay indefinitely with no automated response. No link existed between attribution analysis and the ShadowOptimizationEngine weight system.

## What Was Built

### `GovernanceAction` Enum
```
REDUCE_WEIGHT       — decay detected: reduce weight by 20%, floor at 0.20
DISABLE_IN_REGIME   — regime-blind: advisory warning (compatibility matrix not modified)
CLAMP_CONFIDENCE    — confidence inflation: advisory warning
FREEZE_OPTIMIZATION — overfitting: mark shadow candidate as REJECTED (blocks auto-promote)
QUARANTINE          — severe decay + negative expectancy + ≥20 trades: weight = 0.10
NO_ACTION           — no issues detected
```

### `GovernanceDecision` Dataclass
```python
strategy: str
action: GovernanceAction
reason: str
trace_id: str           # UUID per decision
new_weight: float       # None for advisory-only actions
applied: bool           # False in dry_run or if action advisory-only
reversible: bool        # QUARANTINE reversible=True (can be released manually)
regime_mask: List[str]  # Affected regimes (for DISABLE_IN_REGIME)
confidence_clamp: float # Max confidence (for CLAMP_CONFIDENCE)
```

### Rule Priority (worst wins)
1. **QUARANTINE** — expectancy < −5 USD AND trades ≥ 20 → weight clamped to 0.10
2. **REDUCE_WEIGHT** — decay_severity > 0.7 → weight × 0.80, floor 0.20
3. **FREEZE_OPTIMIZATION** — overfitting_score > 0.6 → shadow candidate REJECTED
4. **DISABLE_IN_REGIME** — regime WR < 30% in ≥5 trades → advisory log warning
5. **CLAMP_CONFIDENCE** — confidence_calibration_score < 0.3 → advisory log warning
6. **NO_ACTION** — no issues

### ShadowOptimizationEngine Integration
All REDUCE_WEIGHT and QUARANTINE decisions route through `ShadowOptimizationEngine.apply_candidate()`:
- Never writes weights directly
- ShadowOptimizationEngine gates apply (min trades, confidence ≥ 0.65, Δweight ≤ 0.30, boundary guard)

### dry_run Mode
When `dry_run=True` (always enabled in DEMO_MODE):
- Generates decisions and persists to `data/governance_decisions.jsonl`
- Never calls ShadowOptimizationEngine
- Never modifies strategy weights

### Persistence
```jsonl
{"ts": "...", "strategy": "EMA_CROSS", "action": "REDUCE_WEIGHT", "trace_id": "...", "applied": true, "new_weight": 0.64, ...}
```
Uses `fcntl.LOCK_EX` + `threading.Lock` for concurrent safety.

### Nightly Integration in CryptoComBot
```python
# In end-of-day cycle (when date changes):
self._run_strategy_governance()
# → get_governance_engine(dry_run=self.state.demo_mode)
# → gov.run_governance_cycle()
# → logs all applied decisions
```

## Soak Test Verification
- `test_governance_dry_run`:
  - Mock attribution → EMA_CROSS: decay_severity=0.8 → expect REDUCE_WEIGHT
  - `dry_run=True`: decision generated, `applied=False`
  - Weights file unmodified after run ✅
- `test_governance_quarantine_bounded`:
  - expectancy=-10 USD, trades=25 → QUARANTINE
  - `new_weight >= 0.10` (floor enforced) ✅
  - `reversible=True` ✅
