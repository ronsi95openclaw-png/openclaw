# Structured Event Sourcing — Phase 3
**File:** `runtime/event_store.py`
**Date:** 2026-05-23

## Problem
The existing `ReplayJournal` was append-only JSONL with no sequence numbers, no checksums, and no formal event schema. State reconstruction from the journal was ad-hoc. This made deterministic replay impossible to guarantee.

## What Was Built

### `EventStore` — Authoritative Immutable Event Store

**Design principles:**
- Does NOT replace `ReplayJournal` — additive layer
- Immutable: no existing line is ever mutated
- Monotonically increasing sequence numbers (survives restarts via last-line O(1) seek)
- Per-event SHA-256 checksum: `sha256(f"{seq}:{event_type}:{trace_id}:{json.dumps(payload, sort_keys=True)}")`
- File writes: `fcntl.LOCK_EX` + `fsync` before unlock
- Concurrent appends: threading.Lock for sequence counter; fcntl for file

**12 event types:** `SIGNAL_GENERATED`, `INTENT_CREATED`, `INTENT_REJECTED`, `POSITION_OPENED`, `POSITION_CLOSED`, `CAPITAL_STATE_CHANGED`, `EMERGENCY_HALT`, `RECONCILIATION_INCIDENT`, `EXECUTION_FAILURE`, `STRATEGY_WEIGHT_CHANGED`, `DRIFT_DETECTED`, `HALT_RELEASED`

**Key methods:**
- `append(event_type, trace_id, payload, symbol, strategy) → StoredEvent`
- `read_from(seq, limit) → List[StoredEvent]` — streaming, no full-file load
- `read_by_trace(trace_id) → List[StoredEvent]`
- `verify_integrity(start_seq) → (bool, List[str])` — full checksum + monotonic seq validation
- `snapshot(capital_state, open_position_count, strategy_weights) → EventStoreSnapshot`
- `reconstruct_state_from_events(events) → dict` — deterministic state replay

**State reconstruction tracks:**
- `capital_state` (from `CAPITAL_STATE_CHANGED`)
- `open_positions` set of trace_ids (from `POSITION_OPENED` / `POSITION_CLOSED`)
- `total_trades` counter
- `halt_reason` string (from `EMERGENCY_HALT`, cleared by `HALT_RELEASED`)

### Integration in `RuntimeOrchestrator`
```python
self._event_store = self._init_event_store()

# On every intent verdict:
ev_type = "INTENT_CREATED" if verdict.approved else "INTENT_REJECTED"
self._emit_event(ev_type, tid, {...})

# On capital state transition:
self._emit_event("CAPITAL_STATE_CHANGED", str(id(self)), {
    "old_state": old_state, "new_state": new_state, "equity": equity,
})
```

### Soak Test Results
- 1000 sequential appends: all checksums valid, seqs monotonically increasing ✅
- 20 threads × 50 concurrent appends = 1000 total: all seqs unique, integrity passes ✅

## Remaining Gaps
- `POSITION_OPENED` / `POSITION_CLOSED` events not yet emitted from `CryptoComBot._open_position()` / `_close_position()`
- No snapshotting integration (snapshot() must be called explicitly, not yet automated)
- Read path not yet exposed via `/api/diagnostics`
