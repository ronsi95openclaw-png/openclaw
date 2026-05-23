# Audit Report — Multi-Leg Execution Simulation (Phase 7)
**Date:** 2026-05-23
**File:** `runtime/microstructure_simulator.py` (extended Phase 6 → Phase 7)
**Status:** IMPLEMENTED · TESTED · 8/8 PASSING
**Risk Resolved:** R-03 (MicrostructureSimulator only modeled single-leg orders)

## Summary
MicrostructureSimulator extended with full multi-leg execution simulation covering
paired SL+TP fills, partial TP laddering, trailing stop probability, cascade chain
modeling, correlated multi-symbol stress testing, and realism scoring.

## New Classes Added

### MultiLegFillResult (dataclass)
- `entry`: FillResult — entry leg simulation
- `sl_fill`: FillResult — stop-loss leg (worst-case spread amplification: 2×)
- `tp_fill`: Optional[FillResult] — take-profit leg (None if not triggered)
- `trailing_stop_triggered`: bool
- `partial_tp_fills`: List[FillResult] — for TP ladder levels
- `cascade_chain_length`: int (0 = no cascade; 1–5 range in PANIC mode)
- `net_slippage_bps`: float — weighted average across all legs
- `total_latency_ms`: float — entry + worst-case exit latency
- `maker_taker_entry`: str — "maker" or "taker"
- `maker_taker_exit`: str
- `realism_score`: float [0, 1]

### MultiLegSimulationConfig (dataclass)
- `sl_distance_pct`, `tp_distance_pct` — order placement distances
- `tp_ladder_levels` (default 3), `tp_ladder_fractions` — partial TP allocation
- `use_trailing_stop`: bool
- `exit_latency_multiplier`: float — asymmetry between entry and exit
- `correlated_symbols`: bool — shared RNG for multi-symbol correlation

## simulate_multi_leg() — 13 Steps
1. Simulate entry leg via existing `simulate_fill()`
2. Compute SL price (worst-case spread: 2× normal spread amplification)
3. Simulate SL fill with amplified spread
4. Compute TP price from config
5. Simulate TP fill (may be None if price not reached)
6. Apply exit latency multiplier (PANIC mode raises by factor)
7. Compute trailing stop probability (30% in VOLATILE+, configurable)
8. Simulate partial TP ladder if tp_ladder_levels > 0
9. Compute cascade chain length (1–5 in PANIC mode, 0 otherwise)
10. Compute weighted net slippage across all fills
11. Sum total latency: entry + worst-case exit
12. Assign maker_taker for entry and exit
13. Compute realism score from fill components

## run_correlated_stress()
- Shared `random.Random(seed)` state across all symbols
- Runs `simulate_multi_leg()` per symbol in sequence
- Skips symbols with no price set (graceful degradation)
- Returns `Dict[str, MultiLegFillResult]`

## get_multi_leg_execution_realism_score()
- 100-run average of realism_score from simulate_multi_leg()
- Returns float [0, 1]

## Test Results (8/8)
| Test | Result |
|------|--------|
| Multi-leg normal mode returns fills | PASSED |
| SL price correctly computed | PASSED |
| PANIC mode has higher total latency | PASSED |
| Partial TP ladder returns fills | PASSED |
| Deterministic with same seed | PASSED |
| Net slippage bounded | PASSED |
| Cascade chain propagates | PASSED |
| Correlated stress returns per-symbol result | PASSED |
