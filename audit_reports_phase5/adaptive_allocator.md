# Audit Report — AdaptiveAllocator (Phase 5)
**Date:** 2026-05-23  
**Subsystem:** `research/portfolio/adaptive_allocator.py`  
**Status:** IMPLEMENTED · TESTED · PASSING

## Summary
Bounded portfolio allocation advisor that translates alpha signals and capital engine state into allocation bound recommendations. All recommendations require explicit human operator approval before application. Never bypasses CapitalPreservationEngine.

## Implementation Details

### AllocationBounds (dataclass)
- `max_exposure_pct` — maximum total portfolio exposure [0–100]
- `max_directional_pct` — max exposure in any single direction [0–100]
- `max_single_symbol_pct` — max allocation per symbol [0–100]
- `min_cash_reserve_pct` — minimum cash reserve [0–100]
- `version` — monotonic integer incremented on each application

### AllocationRecommendation (dataclass)
- `proposed_bounds` — new AllocationBounds
- `rationale` — list of human-readable explanation strings
- `confidence` — [0.0, 1.0]
- `requires_approval` — always True for this implementation

### 5 Adaptation Rules (all bounded)
1. **Alpha signal degradation** — reduces max_exposure_pct (floor: 50% of default)
2. **Directional concentration risk** — reduces max_directional_pct (floor: 30%)
3. **Capital engine state** — DEFENSIVE/CRITICAL/HALT → forces conservative bounds
4. **Cash reserve floor** — never reduces min_cash_reserve_pct below 5%
5. **Symbol concentration** — reduces max_single_symbol_pct if any symbol dominates

### Approval Gate
- `apply_recommendation(rec, approver_id)` returns `False` immediately if `approver_id` is empty string or None
- Validation: all bound values in [0.0, 100.0], exposure ≥ directional
- On success: atomic JSON write to `bounds_path` + fcntl-locked JSONL audit append
- On any failure: returns False, nothing written

### Lazy Import of AlphaSignal
- `from research.statistics.alpha_validation import AlphaSignal` imported inside `compute_recommendation()` to prevent circular imports between research submodules

## Test Coverage (test_phase5_soak.py)
| Test | Result |
|------|--------|
| `test_adaptive_allocator_bounds` | PASSED |

## Governance Contract
- Requires explicit `approver_id` — no anonymous or automated applications
- Does NOT call `CapitalPreservationEngine.update()` — bounds are advisory targets only
- CapitalPreservationEngine remains the authoritative capital gate
- All applications audited in `data/allocator_audit.jsonl`
