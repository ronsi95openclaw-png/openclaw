# Extended Soak Test Results — Phase 4
**Date:** 2026-05-23  **Run:** `pytest tests/soak/test_extended_soak.py -v`

## Result: 10/10 PASSED

---

## Tests

| # | Test | Result | Wall Time | What It Validates |
|---|------|--------|-----------|-------------------|
| 1 | `test_100k_event_replay` | ✅ PASS | ~129s | 100k mixed events; full portfolio reconstruction; all checksums valid |
| 2 | `test_event_emission_storm_concurrent` | ✅ PASS | ~8s | 50×200=10k concurrent appends; all seqs unique; integrity passes |
| 3 | `test_snapshot_create_and_recover` | ✅ PASS | <1s | Corrupt gzip file detected; warning accumulated; fallback to valid |
| 4 | `test_exchange_metadata_precision` | ✅ PASS | <1s | BTC/ETH/SOL truncation; dual ticker/canonical form; validate_order |
| 5 | `test_ws_guardian_health_degradation` | ✅ PASS | <1s | HEALTHY→STALE→DEAD→HEALTHY transitions; should_halt_entries() |
| 6 | `test_governance_dry_run` | ✅ PASS | <1s | REDUCE_WEIGHT decision generated; weights file unmodified |
| 7 | `test_reconnect_storm_bounded` | ✅ PASS | <1s | 15 failures; backoff capped at 300s; reset clears count |
| 8 | `test_event_snapshot_rotation` | ✅ PASS | <1s | 8 snapshots → keep_n=5 → 5 most recent survive |
| 9 | `test_governance_quarantine_bounded` | ✅ PASS | <1s | new_weight >= 0.10; reversible=True |
| 10 | `test_position_lifecycle_replay` | ✅ PASS | <1s | Full lifecycle; open_positions={}, realized_pnl=-50.0, total_trades=1 |

---

## Key Findings

### EventReplayEngine bug was critical
Before fix: ALL events were silently skipped with `NameError: name 'capital_state_ref' is not defined`. The engine appeared to run without errors but produced empty state. Fix: pre-declare ref list variables before calling `_apply_event()`.

### EventSnapshotEngine.verify_snapshot must read from disk
Before fix: `verify_snapshot` only computed checksum from in-memory metadata — always returned True regardless of file corruption. Fix: re-read and decompress `.snap.gz` from disk, fail on any exception.

### 100k event replay is I/O-bound, not CPU-bound
Write: ~104s (100k fsyncs with fcntl). Replay: ~35s (100k sequential JSON reads). Total: ~139s.
The fcntl+fsync write pattern ensures durability but limits throughput to ~1k events/second.
This is acceptable: 1k events/second means 86M events/day — orders of magnitude above expected production volume (~1k events/day for a single-bot deployment).

### WSGuardian health transitions proven under simulated time
Test directly sets `_last_heartbeat_ts` to compress time rather than sleeping. Transitions are deterministic at 30s (STALE) and 60s (DEAD). Recovery to HEALTHY is immediate after `record_heartbeat()`.

---

## Combined Test Suite (Phase 2 + Phase 3 + Phase 4)
```
chaos/test_exchange_failures.py  — 21/21 PASS
chaos/test_capital_chaos.py      — 18/18 PASS
soak/test_runtime_soak.py        — 10/10 PASS
soak/test_extended_soak.py       — 10/10 PASS
TOTAL: 59/59 PASS
```
