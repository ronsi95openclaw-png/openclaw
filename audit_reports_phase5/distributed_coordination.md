# Audit Report — Distributed Coordination (Phase 5)
**Date:** 2026-05-23  
**Files:** `runtime/distributed_lock.py`, `runtime/leader_election.py`  
**Status:** IMPLEMENTED · TESTED · PASSING

## Summary
File-based distributed lock and leader election subsystem providing replay-safe coordination with split-brain prevention. Designed for single-host multi-process scenarios (e.g., bot + dashboard process + canary deployer) with graceful single-node fallback.

---

## DistributedLock (`distributed_lock.py`)

### Lock Record (JSON on disk)
- `lock_id` — UUID4 per acquisition (distinguishes re-acquire from stale hold)
- `holder` — node identifier string
- `acquired_at` — monotonic timestamp
- `expires_at` — monotonic timestamp (`acquired_at + ttl_seconds`)
- `hostname` — machine name for diagnostics

### Atomic Acquire Protocol
1. Create temp file in lock_dir with `fcntl.LOCK_EX`
2. Write LockRecord JSON to temp file
3. `os.replace(tmp, lock_file)` — atomic on POSIX
4. Wait 50 ms (split-brain window)
5. Re-read lock_file and verify `lock_id` matches — if mismatch, another process won, return False
6. Release `fcntl` lock on temp file

### Split-Brain Prevention
- 50 ms settle-and-verify after atomic rename
- `lock_id` UUID ensures two simultaneous acquirers can't both believe they won
- `force_expire()` only permitted when `time.monotonic() > expires_at` (cannot forcibly expire a live lock)

### TTL Enforcement
- Expired locks (`time.monotonic() > expires_at`) are re-acquirable by any node
- `renew(holder)` extends TTL — rejects if lock is expired or held by different node
- Context manager: `with lock.acquire_context("node")` auto-releases on exit

### Fail-Closed
- Any exception during acquire → return False
- Any exception during release → return False (does not re-raise)
- `is_held_by()` returns False on read error (conservative)

---

## LeaderElection (`leader_election.py`)

### Election States
- `FOLLOWER` — not leader, watching for election opportunity
- `CANDIDATE` — attempting to acquire leader lock
- `LEADER` — holds leader lock
- `UNKNOWN` — initial state, or unresolvable error

### Election Daemon Thread
- Polls every `election_interval_s` seconds (default 5 s)
- FOLLOWER → CANDIDATE: attempt `DistributedLock.acquire(node_id)`
- CANDIDATE → LEADER: on acquire success, fire `on_become_leader` callback
- LEADER: attempt `lock.renew(node_id)` each cycle; on failure → FOLLOWER + fire `on_lose_leadership`

### Callbacks
- `on_become_leader` and `on_lose_leadership` fired in separate non-blocking daemon threads
- Wrapped in `try/except` — callback crash does NOT affect election state

### Single-Node Fallback
- If `DistributedLock` init fails (e.g., lock_dir not creatable): `_single_node_mode = True`
- In single-node mode: `is_leader()` returns True always, state = LEADER
- Logged as WARNING — operator can detect single-node fallback in logs

### Singleton
- `get_election(node_id)` — auto-generates `node_id` from hostname if not provided
- One global election instance per process

---

## Test Coverage (test_phase5_soak.py)
| Test | Result |
|------|--------|
| `test_distributed_lock_basic` | PASSED |
| `test_leader_election_single_node` | PASSED |

## Safety Properties
- File-based coordination — no network dependency, works in single-host deployment
- Lock expiry prevents permanent deadlock if process crashes holding lock
- Split-brain detection via `lock_id` re-read provides strong acquire guarantee
- All lock files stored in configurable `lock_dir` (default `data/locks/`)
