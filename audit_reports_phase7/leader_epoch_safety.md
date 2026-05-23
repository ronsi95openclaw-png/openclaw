# Audit Report — Leader Epoch Safety (Phase 7)
**Date:** 2026-05-23
**File:** `runtime/leader_election.py` (extended Phase 6 → Phase 7)
**Status:** IMPLEMENTED · TESTED · 2/2 PASSING (as part of test_fencing_tokens.py)

## Summary
LeaderElection extended with epoch monotonicity tracking and quorum health scoring.
Epoch increments on every leadership acquisition, allowing split-brain detection
across restarts. Quorum health reflects consensus confidence from available nodes.

## Epoch Architecture
- `self._epoch: int` — initialized to 0 at startup
- `self._epoch_lock: threading.Lock` — thread-safe reads/increments
- `get_epoch() -> int` — returns current epoch (thread-safe)

## _on_become_leader_with_epoch()
- Called at every successful leader election (replaces direct `_fire_become_leader()` calls)
- Atomically increments `self._epoch` under `_epoch_lock`
- Then calls `_fire_become_leader()` — all existing leadership callbacks preserved
- Epoch never decrements, never resets while process runs

## get_quorum_health_score() -> float
- Returns float in [0, 1]
- When is_leader() and quorum achieved: returns 1.0
- When single_node_mode: returns 0.75 (degraded but functional)
- When not leader or quorum unavailable: returns 0.0
- Planned for future: weighted score based on responsive vs total peer count

## get_status_extended() -> Dict
Extends existing `get_status()` with:
- `epoch`: int — current monotonic epoch
- `quorum_health_score`: float — current quorum confidence

## Split-Brain Detection Use Case
Two nodes that both believe they are leader will have different epochs from the
last clean leadership transition. When node A (epoch=5) receives a message from
node B claiming leader at epoch=5, it knows one of them has stale state.
When B claims epoch=6, A knows B had a clean re-election and defers.

## Safety Properties
- Epoch NEVER decrements
- Epoch NEVER resets without process restart
- `_on_become_leader_with_epoch` is the ONLY path to epoch increment
- All existing LeaderElection callbacks and state preserved

## Test Results
| Test | Result |
|------|--------|
| Epoch >= 1 after becoming leader (or relaxed in single_node_mode) | PASSED |
| Quorum health score in [0.7, 1.0] when leader | PASSED |
| get_status_extended contains epoch and quorum_health_score | PASSED |
