# Continuous Reconciliation — Phase 3 Findings
**File:** `runtime/reconciliation.py` (extended)
**Date:** 2026-05-23

## Problem Fixed
Phase 2 reconciliation ran only at startup. A bot running for hours could accumulate exchange drift from: manual exchange-side position changes, network reconnects, delayed ACKs, or exchange maintenance.

## What Was Built

### `ContinuousReconciliationScheduler`
Daemon thread running reconciliation every 300s (5 minutes). Key properties:

**Cooldown:** Never runs within 60s of the last run — prevents hammering exchange during repeated failures.

**Exchange-unreachable escalation:**
- 3 consecutive failures → WARNING log
- 5 consecutive failures → `should_halt_entries() = True`, Telegram alert, CRITICAL log
- Recovery: first successful contact resets failure counter and clears halt

**CRITICAL mismatch → halt:**
- `report.halt_required = True` → immediately sets `_halt_entries = True`
- Logs CRITICAL + fires Telegram alert
- Clears only when a subsequent reconciliation fully passes (`report.passed = True`)

**State provider:** Lambda registered by `CryptoComBot` on init, returns `(positions, balance)` snapshot without holding the bot lock (copy-on-read pattern).

**Thread safety:** Single `threading.Lock()` guards all shared state (`_halt_entries`, `_last_run_ts`, `_consecutive_failures`, etc.).

**Prometheus:** Emits `reconciliation_mismatches_gauge` after every cycle.

**Telegram:** `send_alert()` called on halt trigger and on consecutive failure threshold.

### Integration in `CryptoComBot`
```python
# __init__
self._recon_scheduler = self._init_recon_scheduler()  # starts on bot.start()

# start() → self._recon_scheduler.start()
# stop()  → self._recon_scheduler.stop()

# _open_position() — first gate:
if self._recon_scheduler and self._recon_scheduler.should_halt_entries():
    logger.warning("Reconciliation HALT: blocking new position...")
    return
```

### Behavior matrix
| Condition | Action |
|-----------|--------|
| Exchange reachable, no mismatches | Clear halt, log INFO |
| Exchange reachable, warnings | Log WARNING, continue |
| Exchange reachable, CRITICAL mismatch | Set halt, CRITICAL log, Telegram |
| Exchange unreachable (< 5 consecutive) | Warning log, no halt |
| Exchange unreachable (≥ 5 consecutive) | Set halt, CRITICAL log, Telegram |
| Halt active, next reconciliation passes | Clear halt, log INFO |

## Remaining Gaps
- Reconciliation interval is not configurable at runtime (requires restart)
- No self-healing: orphan positions on exchange must be manually resolved
- Exchange API key errors (not network errors) increment failure counter (low-priority fix)
