# Performance Audit

> Last updated: 2026-05-25

## System Health Snapshot

| Metric | Value | Status |
|--------|-------|--------|
| Capability matrix | 31/31 OK | ✅ |
| Survivability (local) | ~90/100 | ✅ STABLE |
| Survivability (Railway cloud) | ~66/100 | ⚠️ DEGRADED (expected) |
| Active daemons | 13 background threads | ✅ |
| Python files | 200+ | ✅ |
| Event types tracked | 25 | ✅ |
| Audit phases completed | 9 | ✅ |
| Obsidian vault writes | Was 0% success | Fixed 2026-05-25 |

---

## Survivability Scoring (runtime/survivability.py)

8 subsystems scored, weighted sum to 100:

| Subsystem | Weight | Local | Railway |
|-----------|--------|-------|---------|
| reconciliation | 20% | ✅ 20/20 | ✅ 20/20 |
| ws_health | 15% | ✅ 15/15 | ⚠️ ~8/15 (no real WS) |
| drift | 15% | ✅ 15/15 | ✅ 15/15 |
| execution_stability | 15% | ✅ 15/15 | ✅ 15/15 (fake data) |
| memory_stability | 10% | ✅ 10/10 | ✅ 10/10 |
| thread_stability | 5% | ✅ 5/5 | ✅ 5/5 |
| snapshot_integrity | 10% | ✅ 10/10 | ✅ 10/10 |
| exchange_connectivity | 10% | ✅ 10/10 | ❌ 3/10 (blocked) |

Railway total: **~88/100 under ideal conditions, ~66/100 typical**

---

## Background Daemon Overhead

13 daemons run on startup. Each consumes a Python thread (~8MB stack):

| Daemon | Thread | CPU impact | Memory |
|--------|--------|-----------|--------|
| uvicorn API server | 1 thread + workers | Low | ~30MB |
| CryptoComBot scan loop | 1 thread | Low (30s sleep) | ~5MB |
| BalanceFeedDaemon | 1 thread (60s poll) | Negligible | ~2MB |
| WeightApplicationDaemon | 1 thread (30s sleep) | Negligible | ~2MB |
| TelegramCommandBot | 1 thread (25s poll) | Negligible | ~2MB |
| SnapshotDaemon | 1 thread (time-based) | Low | ~3MB |
| IntegrityMonitor | 1 thread (5min poll) | Low | ~3MB |
| ReconciliationScheduler | 1 thread | Negligible | ~2MB |
| DriftDetector | 1 thread | Negligible | ~2MB |
| WsGuardian | 1 thread | Negligible | ~2MB |
| ExecutionAnalytics | 1 thread | Negligible | ~2MB |
| ScanIntervalEngine | 1 thread | Negligible | ~2MB |
| **Total** | **13 threads** | **~Low** | **~57MB** |

Total process RSS in Railway: ~120–200MB (acceptable for free tier).

---

## Known Bottlenecks

### 1. Obsidian vault writes (FIXED)
**Was**: 100% silent failure — every write caught by bare except.
**Now**: Working. All 4 writers produce proper vault notes.

### 2. Ruflo HNSW memory (cloud-unavailable)
**Impact**: Pre-trade advisory missing in Railway. Confidence adjustments skipped.
**Severity**: Low — Ruflo is ADVISORY ONLY. IntentPipeline still gates all trades.
**Fix**: Add Ruflo HTTP mode or skip gracefully (already done with `available=False`).

### 3. No Claude Sonnet tier
**Impact**: System jumps from cheap Haiku to expensive Opus for mid-complexity tasks.
**Cost impact**: Some tasks that could use Sonnet ($0.012/1k) use Opus ($0.045/1k).
**Fix**: Add `ask_sonnet()` to `core/brain.py`, route mid-complexity tasks.

### 4. QUIN batch inefficiency
**Impact**: Up to 18 QUIN calls per tick (6 strategies × 3 symbols).
**In practice**: Most ticks have 0–2 signals, so calls are 0–2.
**Fix**: `quin.decide_batch()` for ticks with multiple simultaneous signals.

### 5. Opus prompt not using vault context
**Impact**: Opus restarts fresh each night — no chaining with previous analysis.
**Fix**: Inject last 3 analysis summaries from `07_Optimization/` into Opus prompt.

### 6. Response cache key collision risk
**Impact**: MD5 hash collision (extremely unlikely) could return wrong cached response.
**Severity**: Negligible — 1 in 2^128 probability.
**Note**: Not a real bottleneck, just a design note.

### 7. Scan interval fixed at 60s in Railway
**Impact**: Railway bot can't access real market data, so scan interval optimization is moot.
**In local mode**: `ScanIntervalEngine` can dynamically adjust 15–120s based on volatility.

---

## Silent Failure Points

These were (or are) failing without any visible error:

| Component | Was failing | Fixed? | How detected |
|-----------|-------------|--------|--------------|
| Obsidian trade_journal_writer | ✅ Yes (silently) | ✅ Fixed | Code audit |
| Obsidian vault_manager | ✅ Yes (silently) | ✅ Fixed | Code audit |
| Obsidian optimization_writer | ✅ Yes (silently) | ✅ Fixed | Code audit |
| Obsidian strategy_writer | ✅ Yes (silently) | ✅ Fixed | Code audit |
| Ruflo MCP bridge (Railway) | ✅ Yes (expected) | — handled | available=False |
| Telegram polling (Railway) | ✅ Yes (expected) | — handled | network blocked |
| Crypto.com API (Railway) | ✅ Yes (expected) | — handled | _fake_candles() |
| /pause /halt Telegram cmds | ✅ Yes ("Unknown command") | ✅ Fixed | User testing |

---

## IntegrityMonitor Checks (runtime/integrity_monitor.py)

7 checks run every 5 minutes:

| Check | Pass condition | On fail |
|-------|---------------|---------|
| EventStore checksum | All events have valid SHA-256 | CRITICAL + halt marker |
| Snapshot verification | Last 5 snapshots readable | WARNING → CRITICAL |
| Sequence monotonicity | seq_n > seq_{n-1} for all events | CRITICAL |
| Replay determinism | Two reconstructions match | CRITICAL |
| Reconciliation staleness | Last recon < 10min during trading | WARNING |
| Governance persistence | Last entry readable JSON | WARNING |
| EventStore growth | seq advancing during trading | WARNING (>30min stalled) |

**3+ consecutive integrity failures** → Telegram CRITICAL alert.
**5+ consecutive failures** → Telegram CRITICAL + incident log + optional halt.

---

## Capital Preservation State Machine

```
SAFE (risk_scalar=1.0)
  → DEFENSIVE: daily drawdown ≥5% OR ≥4 consecutive losses
      → CRITICAL: weekly drawdown ≥10% OR ≥7 consecutive losses
          → EMERGENCY_HALT: monthly drawdown ≥20%
              ↑ Release: POST /admin/halt/release (with 4 pre-condition guards)

Auto-recovery:
  DEFENSIVE → SAFE: equity recovers to within 5% of all-time peak
```

Current state: **SAFE** (alltime_peak: $295.30, current: ~$295.30)

---

## Audit Report History

| Phase | Key additions |
|-------|--------------|
| Phase 1 | Capital risk, concurrency, security audit |
| Phase 2 | Chaos tests, portfolio risk, reconciliation, replay |
| Phase 3 | Drift detection, execution analytics, event sourcing |
| Phase 4 | WS guardian, event snapshots, exchange metadata, governance |
| Phase 5 | Adaptive allocator, alpha validation, canary deploy, rollback |
| Phase 6 | Chaos runtime, distributed failures, latency telemetry |
| Phase 7 | Balance guardian, replay verifier, multileg simulator |
| Phase 8 | Balance feed, canary shadow, Telegram validator, backtest baseline |
| Phase 9 | Audit rotation, telemetry, dashboard soak, command tests |

Production readiness score at Phase 9: **91/100**

---

## Production Hardening Gaps

| Gap | Priority | Effort |
|-----|----------|--------|
| Obsidian retrieval (read back) | High | 2 days |
| Claude Sonnet routing tier | Medium | 4 hours |
| QUIN batch decisions | Low | 2 hours |
| Opus context chaining | Medium | 4 hours |
| Ruflo HTTP transport (cloud) | Low | 1 day |
| Dashboard Next.js on Railway | Low | 1 day |
| Weekly consolidation job | Medium | 1 day |
| Vector search (if JSONL not enough) | Low | 3 days |
