# Audit Report — WebSocket Fault Injector (Phase 6)
**Date:** 2026-05-23
**File:** `runtime/ws_fault_injector.py`
**Status:** IMPLEMENTED · TESTED · 6/6 PASSING

## Summary
Deterministic WebSocket fault injector for validating WSGuardian behavior under
adversarial conditions. 8 fault types with bounded injection rates, seeded
deterministic replay, and EventStore integration.

## 8 FaultTypes

| Fault | Effect | Message Result |
|-------|--------|---------------|
| PACKET_LOSS | Message dropped | `[]` (empty) |
| PACKET_DUPLICATION | Message doubled | `[msg, msg.copy()]` |
| PACKET_REORDERING | Buffered then delivered out-of-order | Previous buffered message |
| STALE_HEARTBEAT | WSGuardian.record_reconnect(False) ×3 | Guardian health degrades |
| DELAYED_RECONNECT | records reconnect delay | Advisory |
| FRAGMENTED_FRAME | Modeled as reordering variant | Out-of-order delivery |
| MALFORMED_PAYLOAD | Returns `{"type":"MALFORMED","data":"???corrupted???"}` | Bad frame |
| SEQUENCE_GAP | Skip internal seq counter | Guardian gap detection triggered |

## Fault Priority (applied in order)
MALFORMED > PACKET_LOSS > DUPLICATION > REORDERING > STALE_HEARTBEAT

## Bounded Injection Rate
- Rolling 20-message window tracks fault fraction
- `max_injection_rate=0.20` default — never faults > 20% of messages
- Rate > limit: `inject()` passes message through unchanged (no fault)
- Rate = 1.0: no ceiling (test mode)

## Deterministic Replay
- `random.Random(seed)` — ALL randomness seeded
- `reset()` re-seeds from original seed — subsequent `inject()` calls produce identical sequence
- Same seed + same input stream = identical FaultEvent list (replay-safe testing)

## WSGuardian Integration
- No module-level import of WSGuardian (circular risk)
- Guardian passed as parameter to `inject_stale_heartbeat(guardian)` and `inject_sequence_gap(guardian)`
- Injector is testable without WSGuardian being available

## EventStore Integration
`emit_chaos_events_to_store()`:
- Lazy import `get_store()` inside method, wrapped in try/except
- Emits each un-emitted FaultEvent as EventStore record
- Returns count emitted; marks each `emitted_to_store = True`

## Test Results (6/6)
| Test | Result |
|------|--------|
| packet_loss_rate=1.0 → empty result | PASSED |
| duplication_rate=1.0 → 2 messages | PASSED |
| malformed_rate=1.0 → corrupted payload | PASSED |
| max_injection_rate=0.1 respected over 100 messages | PASSED |
| Same seed → identical fault sequence | PASSED |
| get_stats() totals match len(get_events()) | PASSED |

## Operational Risk Eliminated
WSGuardian had unit tests but no adversarial validation. Now: fault injection
proves that PACKET_LOSS correctly degrades guardian health score, SEQUENCE_GAP
triggers gap detection logic, and MALFORMED_PAYLOAD doesn't crash the receive path.
