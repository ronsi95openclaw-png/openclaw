# Exchange Drift Detection — Phase 3
**File:** `runtime/drift_detector.py`
**Date:** 2026-05-23

## Problem
Stale prices, frozen exchange data, websocket disconnects, and delayed responses could cause the bot to trade on outdated market data without any detection mechanism.

## What Was Built

### `DriftDetector`
9 drift types, 3 severity levels, continuous detection with auto-resolution.

**Drift types:**
`STALE_PRICE`, `STALE_BALANCE`, `STALE_ORDER_STATE`, `FROZEN_EXCHANGE_DATA`, `WEBSOCKET_DESYNC`, `DUPLICATE_FILL`, `OUT_OF_ORDER_EVENT`, `MISSING_EXECUTION_EVENT`, `POSITION_DESYNC`

**Thresholds (configurable):**
| Check | WARNING | CRITICAL |
|-------|---------|----------|
| Price staleness | >60s | >120s |
| Balance staleness | >120s | >300s |
| Frozen data (unchanged) | 5 consecutive identical | — |
| WebSocket desync | >30s since last event | >90s |

**Core methods:**
- `update_price(symbol, price, ts_ms)` — records latest price + timestamp
- `update_balance(balance, ts_ms)` — records latest balance timestamp
- `detect_all(local_positions, current_prices)` → `List[DriftEvent]`
- `should_halt_entries() → bool` — True if any CRITICAL event is unresolved
- `resolve_event(event_type, symbol)` — marks resolved
- `get_drift_summary()` — `{total, critical, warning, info, oldest_event_age_s}`

**File rotation:** `data/drift_events.jsonl` rotates at 1000 lines → keeps most recent 500.

### Integration in `CryptoComBot`
```python
# _open_position() — gate 1 (before reconciliation gate):
if self._drift_detector and self._drift_detector.should_halt_entries():
    logger.warning("DriftDetector HALT: critical exchange drift")
    return

# _scan() per-symbol loop:
self._drift_detector.update_price(symbol, price, int(time.time() * 1000))
```

## Safety Contract
CRITICAL drift → new entries blocked (same pattern as reconciliation halt). Does NOT stop existing position management (check_positions still runs).

## Remaining Gaps
- WebSocket desync check requires `notify_ws_event()` to be called from the WebSocket handler (not yet wired into the MCP bridge or exchange connector)
- Duplicate fill detection requires order fill event stream (not available in demo mode)
