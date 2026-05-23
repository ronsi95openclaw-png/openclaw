# Audit Report — Distributed Lock Fencing Tokens (Phase 7)
**Date:** 2026-05-23
**File:** `runtime/distributed_lock.py` (extended Phase 6 → Phase 7)
**Status:** IMPLEMENTED · TESTED · 8/8 PASSING

## Summary
DistributedLock extended with monotonically increasing fencing tokens, stale writer
rejection, and split-brain audit persistence. Eliminates the split-brain write window
by requiring writers to check token freshness before mutating shared state.

## Fencing Token Architecture
- Module-level `_FENCING_TOKENS: Dict[str, int]` — per-resource monotonic counters
- `_FENCING_LOCK: threading.Lock` — thread-safe increment
- `_get_next_fencing_token(resource_name) -> int` — always returns > previous value
- `_persist_fencing_tokens()` — atomic write to `data/fencing_tokens.json` (fcntl.LOCK_EX)
- `_load_fencing_tokens()` — restores counters on process restart; called at module load

## LockRecord Enhancement
`fencing_token: int = 0` field added to existing LockRecord dataclass.
Written to lock file on every acquire. Readers can compare tokens.

## New DistributedLock Methods

### acquire_with_fencing(holder_id) -> Tuple[bool, int]
- Calls `acquire(holder_id)` — all existing acquire logic preserved
- On success: increments module fencing token, stores in `_held_fencing_token`
- Returns `(True, token)` on success, `(False, 0)` on failure
- Persists token to `data/fencing_tokens.json` after increment

### get_fencing_token() -> Optional[int]
- Returns `_held_fencing_token` if lock currently held by this instance
- Returns `None` if not held
- Thread-safe read

### is_write_safe(token) -> bool
- Returns True if `token == _held_fencing_token` AND lock is currently held
- Returns False for any stale (old epoch) token
- Fail-closed: exception → False

### _append_split_brain_audit(holder, own_lock_id, found_lock_id)
- Called automatically by `acquire()` on split-brain detection
- Writes JSONL entry to `data/split_brain_audit.jsonl`
- Fields: ts, resource, attempted_holder, own_lock_id, found_lock_id
- fcntl.LOCK_EX; exception → silently suppressed (never raises)

## Monotonicity Guarantee
Fencing tokens are:
1. Strictly increasing per resource (module-level counter, never reset while process runs)
2. Persisted to disk — survive process restart (load_fencing_tokens at module import)
3. Thread-safe (protected by `_FENCING_LOCK`)

## Safety Properties
- NEVER issues same token twice for a resource
- NEVER allows is_write_safe on a token from a previous lock epoch
- NEVER modifies EventStore
- release() clears _held_fencing_token atomically

## Test Results (8/8)
| Test | Result |
|------|--------|
| acquire_with_fencing returns (True, token > 0) | PASSED |
| Tokens strictly monotonically increasing | PASSED |
| is_write_safe returns True with correct token | PASSED |
| is_write_safe returns False for stale token | PASSED |
| get_fencing_token returns None when not held | PASSED |
| Split-brain audit written on detection | PASSED |
| Epoch increments on leadership | PASSED |
| Quorum health score in [0.7, 1.0] when leader | PASSED |
