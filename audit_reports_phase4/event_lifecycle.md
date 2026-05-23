# Event Lifecycle Sourcing — Phase 4
**Files:** `runtime/event_store.py`
**Date:** 2026-05-23

## Problem
EventStore had 12 event types covering high-level capital and strategy decisions, but lacked granular order-level and WebSocket lifecycle events. `EventReplayEngine._apply_event()` had a critical NameError bug (pre-declared ref lists missing), silently skipping every event during replay.

## What Was Built

### Extended EventType Enum (24 total, +12 from Phase 3)

**New event types:**
| Category | Events |
|----------|--------|
| Order lifecycle | ORDER_SUBMITTED, ORDER_ACKNOWLEDGED, ORDER_REJECTED, ORDER_CANCELLED |
| Fill events | POSITION_PARTIALLY_FILLED, SL_TRIGGERED, TP_TRIGGERED |
| Reconciliation | RECONCILIATION_STARTED, RECONCILIATION_COMPLETED |
| WebSocket | WEBSOCKET_RECONNECTED, WEBSOCKET_DESYNC, EXECUTION_TIMEOUT |

### EventReplayEngine (fixed + verified)

**Bug fixed:** In `reconstruct_portfolio_state()`, the mutable "ref list" pattern (single-element lists for scalar mutation) was using inline list literals as keyword arguments, making the variable names inaccessible after the call. Fixed by pre-declaring all ref list variables before calling `_apply_event()`.

```python
# Before (broken):
self._apply_event(ev, capital_state_ref=[capital_state], ...)
capital_state = capital_state_ref[0]  # NameError: capital_state_ref not defined

# After (correct):
capital_state_ref = [capital_state]
self._apply_event(ev, capital_state_ref=capital_state_ref, ...)
capital_state = capital_state_ref[0]  # Works correctly
```

**`reconstruct_portfolio_state()` → 12-key dict:**
- `capital_state`: str (UNKNOWN/SAFE/DEFENSIVE/CRITICAL/EMERGENCY_HALT)
- `open_positions`: dict {trace_id: {symbol, side, entry_price, size, strategy}}
- `realized_pnl`: float (accumulated from POSITION_CLOSED events)
- `active_halt`: bool
- `halt_reason`: str
- `total_trades`: int
- `exposure`: float (sum of position notionals)
- `execution_failures`: int
- `strategy_trade_counts`: dict {strategy: int}
- `last_capital_transition`: str (e.g., "SAFE→DEFENSIVE")
- `reconstructed_at`: ISO timestamp
- `events_processed`: int

**`verify_reconstruction()` — invariant checks:**
- NaN check on exposure and realized_pnl
- Negative exposure guard
- HALT consistency: if active_halt, capital_state must be EMERGENCY_HALT

**`get_event_throughput(window_seconds=60)` — last-N events within window:**
- Reads last 1000 events, counts those within time window

### POSITION_OPENED/CLOSED emission in CryptoComBot
```python
# _open_position() — after Telegram alert:
EventStore().append(EventType.POSITION_OPENED, trace_id=trade_id, payload={
    symbol, strategy, side, entry_price, size, sl_price, tp_price, regime, demo
})

# _close_position() — before _save_state():
EventStore().append(EventType.POSITION_CLOSED, trace_id=pos["id"], payload={
    symbol, strategy, side, entry_price, exit_price, pnl, outcome, demo
})
```

## Soak Test Verification
- `test_position_lifecycle_replay`: SIGNAL_GENERATED → ORDER_SUBMITTED → ORDER_ACKNOWLEDGED → POSITION_OPENED → 3× SIGNAL_GENERATED → SL_TRIGGERED → POSITION_CLOSED → `realized_pnl == -50.0`, `open_positions == {}`, `total_trades == 1` ✅
- `test_100k_event_replay`: 100k mixed events, all checksums valid, deterministic replay ✅
