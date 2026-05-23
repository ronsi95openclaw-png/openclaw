# EventStore Snapshot System — Phase 4
**File:** `runtime/event_snapshot.py`
**Date:** 2026-05-23

## Problem
EventStore had no checkpoint system. On bot restart with millions of events, full replay from seq=0 would be required, making restart time grow unboundedly. No way to validate on-disk snapshot integrity or recover from partial/corrupt snapshots.

## What Was Built

### `SnapshotMetadata` Dataclass
Key fields:
- `snapshot_id`: UUID
- `seq_at_snapshot`: int — EventStore seq at time of snapshot
- `capital_state`, `open_positions`, `realized_pnl`, `active_halt` — portfolio state
- `event_count_at_snap`: int
- `checksum`: SHA-256 of all fields excluding `checksum` itself

### `EventSnapshotEngine`
**Trigger conditions (`maybe_snapshot()`):**
- Current seq − last snapshot seq ≥ `snapshot_interval_events` (default: 10,000)
- OR time since last snapshot ≥ `snapshot_interval_hours` (default: 24h)

**Write path (`_write_snapshot()`):**
1. Serialize metadata to JSON
2. gzip compress
3. Write to temp file
4. `os.replace()` — atomic rename
5. fcntl-locked append to `index.jsonl`
6. Never propagates exceptions (always safe to call)

**`verify_snapshot(meta)` — re-reads from disk:**
```python
# Reads .snap.gz file, decompresses, recomputes checksum from disk contents
# Returns False on ANY exception (CRC error, missing file, checksum mismatch)
```

**`recover_from_latest_snapshot()`:**
- Walks ALL index entries newest-first (does not skip corrupt — includes them in warning list)
- Returns `(first_valid, warnings)` where warnings accumulates info about each corrupt/missing snapshot
- Returns `(None, ["All snapshots corrupt or missing"])` if nothing recoverable

**`delete_old_snapshots(keep_n=5)`:**
- Deletes `.snap.gz` files for old snapshots
- Atomically rewrites `index.jsonl` to keep only surviving entries

**Startup recovery:** On `__init__`, reads existing `index.jsonl` to sync `_last_snap_seq` and `_last_snap_ts` — so trigger thresholds survive process restarts.

**Thread safety:** Single `threading.Lock()` on all operations.

### Key Fix: verify_snapshot reads from disk
Original implementation only checked in-memory metadata checksum (always True). Fixed to re-read and decompress the actual `.snap.gz` file, triggering on any CRC corruption or data modification.

## Soak Test Verification
- `test_snapshot_create_and_recover`:
  - Create valid snapshot (seq=500) — older
  - Create second snapshot (seq=510), corrupt its gzip bytes — newer
  - `verify_snapshot(corrupt_meta)` returns False ✅
  - `verify_snapshot(valid_meta)` returns True ✅
  - `recover_from_latest_snapshot()` returns valid_meta (seq=500) ✅
  - Warnings contain "corrupt" or "checksum" or "mismatch" ✅
- `test_event_snapshot_rotation`:
  - Create 8 snapshots → `delete_old_snapshots(keep_n=5)` → 5 most recent survive ✅
