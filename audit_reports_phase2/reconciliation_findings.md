# Reconciliation Engine — Findings Report
**File:** `runtime/reconciliation.py`
**Date:** 2026-05-22

## Summary
Exchange reconciliation was the single largest blocker for limited-live deployment. A crash or restart could leave bot state diverged from the exchange: ghost positions tracked locally but closed on exchange, orphan positions opened on exchange but not tracked, or wrong sizes. This file implements the full reconciliation engine.

## What Was Built

### `ReconciliationEngine`
- **9-step live reconciliation** on every startup:
  1. Fetch positions, open orders, and equity from exchange
  2. Index local positions by `(instrument, side)`
  3. Detect ghost positions (local-only) → auto-remove
  4. Detect orphan positions (exchange-only) → HALT required
  5. Detect size mismatches (>1% tolerance) → WARNING
  6. Detect side mismatches → CRITICAL
  7. Detect missing SL orders → HALT required
  8. Detect missing TP orders → WARNING
  9. Detect balance mismatch (>5% tolerance) → WARNING

- **Demo mode** does local-only integrity check: validates all required fields, removes corrupt positions, no exchange calls.

- **Exchange unreachable** (timeout, 5xx): marks `exchange_reachable=False`, logs CRITICAL. Does NOT auto-HALT to avoid false alarms from transient connectivity.

### `ReconciliationReport` dataclass
Fields: `ts`, `demo_mode`, `passed`, `halt_required`, `exchange_reachable`, `local_positions`, `exchange_positions`, `mismatches`, `resolved_count`, `critical_count`, `warning_count`, `duration_ms`, `notes`. Includes `summary()` method for log/alert use.

### `reconcile_on_startup(local_positions, local_balance, demo_mode)` convenience function
Called from `CryptoComBot.__init__` after `_load_state()`.

### Severity Classification
| Mismatch | Severity | Action |
|----------|----------|--------|
| Ghost position | WARNING | Auto-remove |
| Orphan position | CRITICAL | HALT required |
| Size mismatch >1% | WARNING | Log |
| Side mismatch | CRITICAL | HALT required |
| Missing SL order | CRITICAL | HALT required |
| Missing TP order | WARNING | Log |
| Balance mismatch >5% | WARNING | Log |
| Exchange timeout | CRITICAL | Mark unreachable |
| Corrupt local state | WARNING | Auto-remove |

## Integration
`CryptoComBot.__init__` → `_run_startup_reconciliation()` → `reconcile_on_startup()`

If `halt_required=True`, `status_msg` is set to `"HALTED — reconciliation failure"` and the scan loop refuses to open new positions.

## Chaos Test Coverage
- Exchange timeout → `exchange_reachable=False`
- Demo mode never calls exchange
- Corrupt positions flagged in demo mode
- Valid demo positions pass cleanly

## Remaining Limitations
- Reconciliation runs at startup only (not continuously in loop)
- Exchange `get_positions()` / `get_open_orders()` require valid API keys
- In demo mode, orphan/ghost checks are skipped (no exchange to compare against)
