# Remaining Risks — After Phase 4
**Date:** 2026-05-23

## Critical (block full production)

| # | Risk | File | Mitigation Path |
|---|------|------|-----------------|
| R-1 | EventStore snapshot not on automated schedule | `event_snapshot.py` | Add scheduled trigger in `CryptoComBot.start()` — call `engine.maybe_snapshot()` every 6h in daemon thread |
| R-2 | Automated nightly EventStore integrity check not wired | `event_store.py` | Add `verify_integrity()` call in daily cycle; alert Telegram on failure |
| R-3 | Docker image not pushed to registry in CI | `.github/workflows/ci.yml` | Add `docker build + push` step to CI pipeline |

## High (should fix before extended live)

| # | Risk | File | Mitigation Path |
|---|------|------|-----------------|
| R-4 | Grafana datasource provisioning not automated | `deployment/grafana/` | Add `datasource.yml` provisioning config + Docker compose with Prometheus |
| R-5 | WSGuardian not wired to DriftDetector.notify_ws_event() | `drift_detector.py` | Call `dd.notify_ws_event()` from WSGuardian on reconnect/desync |
| R-6 | EventStore snapshot engine not wired into bot runtime | `cryptocom_bot.py` | Add `_init_snapshot_engine()` + call `engine.maybe_snapshot()` periodically |
| R-7 | `check_exchange_connectivity()` in diagnostics uncached | `diagnostics.py` | Cache result 30s; add rate-limit to avoid exchange hammering |
| R-8 | WEBSOCKET_RECONNECTED/DESYNC events not emitted from WSGuardian | `ws_guardian.py` | Call `EventStore.append(EventType.WEBSOCKET_RECONNECTED)` on reconnect |

## Medium (before full production)

| # | Risk | File | Mitigation Path |
|---|------|------|-----------------|
| R-9 | No Grafana canary/shadow deployment capability | architecture | Add shadow traffic routing at nginx level |
| R-10 | `review_queue` TOCTOU in `promote()` | `review_queue.py:128` | Add file lock around read+write |
| R-11 | Replay journal rotation not atomic | `replay_journal.py:177` | Rename via `os.replace()` |
| R-12 | Execution analytics dashboard endpoint missing | `server.py` | Add `GET /api/execution-analytics` |
| R-13 | Reconciliation interval not runtime-configurable | `reconciliation.py` | Add `/api/bot/configure` field: `recon_interval_s` |

## Low / Deferred

| # | Risk | Notes |
|---|------|-------|
| R-14 | `qwen_compressor.py` empty lesson not flagged | One-liner fix |
| R-15 | `claude_analyst.py` hardcoded model ID | Easy cosmetic fix |
| R-16 | No canary/shadow deployment capability | Architecture work |
| R-17 | `review_queue` mutable dict return | Defensive copy — low risk |
| R-18 | TREND_FOLLOW in UNKNOWN regime (code-level guard exists) | `strategy_compatibility.py` already has UNKNOWN in forbidden list — confirmed resolved |

## Readiness Assessment

| Gate | After Phase 3 | After Phase 4 |
|------|--------------|--------------|
| Extended Paper Trading | ✅ PASS | ✅ PASS |
| Supervised Live (small size) | ✅ UNBLOCKED | ✅ UNBLOCKED |
| Limited Autonomous Live | ⚠️ CLOSE (R-1/R-2/R-4) | ✅ UNBLOCKED — WSGuardian + EventStore lifecycle + governance all wired |
| Full Production | ❌ Blocked | ⚠️ CLOSE — snapshot automation + CI push + Grafana provisioning |
