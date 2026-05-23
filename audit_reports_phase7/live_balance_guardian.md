# Audit Report — Live Balance Guardian (Phase 7)
**Date:** 2026-05-23
**File:** `runtime/live_balance_guardian.py`
**Status:** IMPLEMENTED · TESTED · 10/10 PASSING
**Risk Resolved:** R-01 (Real balance feed not wired into CapitalPreservationEngine)

## Summary
BalanceGuardian cross-validates exchange-reported balance against CapitalPreservationEngine
equity and EventReplay-reconstructed equity. Detects divergence, stale feeds, negative
collateral, and long-run drift via EWMA. All severity escalation is fail-closed.
DEMO_MODE=true: HALT severity is computed but downgraded to CRITICAL (advisory only).

## BalanceSeverity Ladder

| Severity | Trigger |
|----------|---------|
| INFO | All checks pass, divergence < warning_threshold |
| WARNING | Divergence ≥ warning_threshold (default 2%) OR stale feed first detection |
| CRITICAL | Divergence ≥ critical_threshold (default 5%) OR negative collateral |
| HALT | Divergence ≥ halt_threshold (default 10%) OR consecutive halts ≥ 3 |

## BalanceCheckResult (15 fields)
- `severity`, `exchange_balance`, `capital_engine_equity`, `replay_equity`
- `divergence_pct` (exchange vs capital), `replay_mismatch_pct`
- `ewma_divergence` (alpha=0.1), `is_stale`, `stale_seconds`
- `negative_collateral`, `demo_mode`, `consecutive_halts`
- `check_ts`, `audit_written`, `telegram_sent`

## Cross-Validation Logic
1. Read capital engine equity (lazy import, try/except)
2. Read replay equity from EventReplayEngine (lazy import, try/except)
3. Compute divergence_pct = abs(exchange - capital) / max(abs(capital), 1.0) × 100
4. Detect stale: last_known_good older than stale_threshold_s (default 300s)
5. Update EWMA divergence (α=0.1)
6. Check negative collateral (exchange_balance < 0 OR capital_engine_equity < 0)
7. Detect replay mismatch (replay_mismatch_pct ≥ 5.0%)
8. Apply severity ladder (fail-closed: tie → higher severity)
9. Track consecutive HALTs (3+ triggers Telegram advisory even in DEMO_MODE)
10. Atomic JSONL audit write (fcntl.LOCK_EX + tempfile.mkstemp + os.replace)
11. Cache last-known-good on INFO or WARNING

## DEMO_MODE Safety Contract
- Severity computation runs fully (no bypass)
- HALT computed → downgraded to CRITICAL for enforcement
- Telegram advisory fires for CRITICAL+ even in DEMO_MODE
- No halt marker written to disk in DEMO_MODE
- All audit JSONL writes execute normally

## Safety Properties
- NEVER fail-open (unknown exchange balance → INFO with stale detection)
- NEVER modify EventStore
- NEVER bypass CapitalPreservationEngine governance
- All file I/O: atomic writes, fcntl locking
- Singleton via double-checked locking: `get_guardian()`

## Test Results (10/10)
| Test | Result |
|------|--------|
| No data returns INFO | PASSED |
| Healthy balance INFO | PASSED |
| Small divergence WARNING | PASSED |
| Large divergence CRITICAL | PASSED |
| DEMO_MODE never HALTs | PASSED |
| Negative collateral CRITICAL | PASSED |
| Stale detection | PASSED |
| EWMA updates monotonically | PASSED |
| Audit file created | PASSED |
| Last-known-good cached | PASSED |
