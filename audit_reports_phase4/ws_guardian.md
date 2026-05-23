# WebSocket Guardian — Phase 4
**File:** `runtime/ws_guardian.py`
**Date:** 2026-05-23

## Problem
WebSocket connectivity had no health monitoring layer. The DriftDetector could detect price staleness but had no mechanism to detect WebSocket reconnections, sequence gaps, or degraded heartbeat health. No gate existed to block new position entries during WS outages.

## What Was Built

### `WSGuardian` Class

**Health scoring (0.0–1.0):**
- Base: 1.0
- Heartbeat age > 30s: −0.3; > 60s (DEAD): −0.5
- Each sequence gap: −0.1 (capped at −0.3 total)
- Each consecutive failure: −0.05 (capped at −0.2 total)
- Score clamped to [0.0, 1.0]

**`HeartbeatStatus` enum:** HEALTHY / STALE / DEAD

**`WSHealthScore` dataclass:**
```
score: float                      # 0.0–1.0
heartbeat_status: HeartbeatStatus
sequence_gaps_detected: int
last_heartbeat_age_s: float
reconnect_count: int
consecutive_failures: int
last_updated: float               # monotonic timestamp
is_halting: bool                  # True when score < 0.4 or DEAD
```

**Key methods:**
- `record_heartbeat()` — resets heartbeat timer, clears stale/dead state
- `record_message(seq)` — detects sequence gaps (seq jump > 1 = gap)
- `record_reconnect(success)` — tracks reconnects; success resets backoff; failure increments failures
- `get_health_score()` — computes and returns current `WSHealthScore`
- `should_halt_entries()` — True if score < 0.4 OR status == DEAD
- `get_backoff_delay()` — `min(base^reconnect_count, 300.0)` seconds

**DEAD transition alerting:** Fires Telegram alert exactly once per DEAD transition (not on every call). Reset by `record_heartbeat()`.

**Thread safety:** `threading.Lock()` on all state mutations and reads.

### Integration into CryptoComBot

```python
# __init__:
self._ws_guardian = self._init_ws_guardian()  # after drift_detector

# _open_position() Gate 1 (before drift, before reconciliation):
if self._ws_guardian and self._ws_guardian.should_halt_entries():
    logger.warning("WSGuardian HALT: blocking new position — WebSocket health degraded")
    return
```

### Module Singleton
```python
guardian = get_guardian()  # double-checked locking
```

## Soak Test Verification
- `test_ws_guardian_health_degradation`: Direct `_last_heartbeat_ts` manipulation to compress time. HEALTHY → STALE (>30s) → DEAD (>60s) → HEALTHY (after record_heartbeat). `should_halt_entries()` = True when DEAD. ✅
- `test_reconnect_storm_bounded`: 15 failed reconnects; `get_backoff_delay()` capped at 300.0s; `reset()` clears count. ✅
