# Audit Report — Remaining Risks (Phase 5)
**Date:** 2026-05-23  
**Status:** ADVISORY — for operator review

## Risk Classification
| ID | Risk | Severity | Likelihood | Mitigation |
|----|------|----------|------------|------------|
| R-01 | Snapshot dir full — daemon silently degrades | HIGH | LOW | Add disk-space pre-check in SnapshotDaemon before each write |
| R-02 | Telegram alerting unavailable — critical escalations silent | HIGH | MEDIUM | Add secondary alert channel (email/webhook); current fallback is file-only |
| R-03 | DistributedLock lock_dir on same mount as event store — disk failure takes both | MEDIUM | LOW | Move lock_dir to separate mount in production |
| R-04 | LeaderElection single-node fallback masks multi-process conflicts | MEDIUM | LOW | Deploy single bot process per host; document constraint |
| R-05 | ExecutionOptimizer analytics file unbounded growth | LOW | MEDIUM | Add rotation: keep last 10 000 records |
| R-06 | AlphaValidationEngine reads full outcomes file per call | LOW | MEDIUM | Add seek-from-tail optimization for large files |
| R-07 | RollbackManager post-write verification is synchronous — adds latency to emergency path | LOW | LOW | Acceptable for rollback use case; not on hot path |
| R-08 | IntegrityMonitor replay_determinism check uses small subset — may miss intermittent divergence | MEDIUM | LOW | Increase subset size; add random sampling |
| R-09 | CanaryDeployer phase 4 promotion requires manual operator_id but no MFA/2FA | HIGH | LOW | Enforce approval via secure channel (e.g., signed message) before live |
| R-10 | SurvivabilityEngine score degrades gracefully but bot does not auto-halt on UNSAFE | MEDIUM | MEDIUM | Wire `deployment_ready=False` → halt marker in bot startup check |

## Items Outside Phase 5 Scope (Backlog)
- Real balance feed from Crypto.com into CapitalPreservationEngine (backlog item 6)
- Telegram bot integration end-to-end test (alerts currently untested in CI)
- Prometheus metrics endpoint authentication (currently open)
- Multi-host distributed deployment (DistributedLock is single-host optimized)
- Auto-apply Claude Opus weight adjustments at midnight (backlog item 2)

## Items Not Addressed in Phase 5
- TREND_FOLLOW block in UNKNOWN regime (backlog item 1) — risk remains, documented
- Dynamic scan interval (backlog item 3)
- Auto-disable strategies weight < 0.3 (backlog item 5)

## Security Observations
- `data/permissions.key` (Fernet key) chmod 600 — verified in Phase 4
- No new secrets introduced in Phase 5
- Audit JSONL files are append-only by design; no truncation or overwrite paths exist
- All new modules follow fail-closed pattern for safety gates
