# Audit Report — Replay Consistency Verifier (Phase 7)
**Date:** 2026-05-23
**File:** `runtime/replay_verifier.py`
**Status:** IMPLEMENTED · TESTED · 8/8 PASSING

## Summary
ReplayVerifier performs three-path replay consistency verification comparing raw events,
snapshot+tail reconstruction, and live bot state. Detects divergence between paths,
emits EventStore events on divergence, and appends to JSONL audit log.
Strictly advisory — never halts bot or modifies EventStore.

## ReplayPath Enum
- `RAW_EVENTS` — direct sum from EventStore
- `SNAPSHOT_PLUS_TAIL` — snapshot equity + events since snapshot
- `LIVE_STATE` — CapitalPreservationEngine.get_equity() live reading

## ReplayCheckField Enum
- `EQUITY`, `OPEN_POSITION_COUNT`, `CAPITAL_STATE`, `STRATEGY_WEIGHTS_HASH`, `GOVERNANCE_CONFIG_HASH`

## ReplayDivergence (dataclass)
- `field`: ReplayCheckField
- `path_a`, `path_b`: ReplayPath
- `value_a`, `value_b`: Any
- `pct_diff`: Optional[float]

## ReplayEquivalenceReport (dataclass)
- `report_id`: UUID4
- `generated_at`: ISO8601
- `is_equivalent`: bool (True only when zero divergences)
- `divergences`: List[ReplayDivergence]
- `checksum_tree`: Dict — per-path checksum of all numeric fields
- `paths_available`: List[ReplayPath]
- `duration_ms`: float
- `audit_written`: bool

## Comparison Logic
- Numeric fields: abs(a - b) / max(abs(a), 1.0) > tolerance_pct → divergence
- String fields (capital_state): exact equality
- File hash fields (strategy_weights, governance_config): SHA-256 hex digest comparison
- tolerance_pct default: 0.01 (1%)

## Fail-Closed Behavior
- Path unavailable (ImportError, AttributeError): skipped gracefully; not added to paths_available
- Path raises exception: treated as unavailable; divergence NOT fabricated
- On divergence: emits RECONCILIATION_COMPLETED event to EventStore (lazy import)
- Prometheus counter incremented on divergence (lazy import, no-op if unavailable)
- Optional rollback escalation if RollbackManager available

## Atomic Audit Persistence
- `data/replay_verifier_audit.jsonl` — fcntl.LOCK_EX append
- Written for every run (including no-divergence runs)

## Singleton
`get_verifier() -> ReplayVerifier` — double-checked locking

## Test Results (8/8)
| Test | Result |
|------|--------|
| run_verification no crash on empty | PASSED |
| Equivalent on empty store | PASSED |
| Divergence detected via subclass override | PASSED |
| checksum_tree present in report | PASSED |
| report_id unique across runs | PASSED |
| Audit file created | PASSED |
| Duration recorded (>= 0) | PASSED |
| _emit_divergence_event safety | PASSED |
