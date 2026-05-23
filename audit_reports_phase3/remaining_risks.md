# Remaining Risks — After Phase 3
**Date:** 2026-05-23

## Critical (block live deployment)

| # | Risk | File | Mitigation Path |
|---|------|------|-----------------|
| R-1 | WebSocket notify not wired into DriftDetector | `drift_detector.py` | Call `dd.notify_ws_event()` from MCP bridge / exchange WS handler |
| R-2 | EventStore POSITION_OPENED/CLOSED not emitted | `cryptocom_bot.py` | Add `_emit_event()` calls in `_open_position()` / `_close_position()` |
| R-3 | Halt release endpoint has no rate limiting | `server.py` | Add per-IP rate limit (max 5 attempts per minute) |
| R-4 | ShadowOptimizationEngine not wired into `_auto_apply_opus_weights()` | `cryptocom_bot.py` | Route Opus recommendations through shadow gate before live write |

## High (should fix before extended live)

| # | Risk | File | Mitigation Path |
|---|------|------|-----------------|
| R-5 | Exchange quantity precision not instrument-specific | `exchange.py` | Add `_QTY_PRECISION` dict per instrument |
| R-6 | Position schema no Pydantic validation on load | `cryptocom_bot.py:117` | Add Pydantic model for position validation |
| R-7 | `governance._is_active()` without lock (CC-11) | `operator_controls.py` | Add `threading.Lock()` |
| R-8 | `permissions.py` base64 "encryption" | `permissions.py:126` | Replace with Fernet (same as secrets.py) |
| R-9 | `check_exchange_connectivity()` in diagnostics makes live API call | `diagnostics.py` | Cache result for 30s, rate-limit to avoid load |
| R-10 | Continuous reconciliation interval not runtime-configurable | `reconciliation.py` | Add `/api/bot/configure` field: `recon_interval_s` |

## Medium (before full production)

| # | Risk | File | Mitigation Path |
|---|------|------|-----------------|
| R-11 | No EventStore snapshot automation | `event_store.py` | Schedule daily snapshot + verify_integrity() check |
| R-12 | Strategy attribution not wired to auto-disable | `cryptocom_bot.py` | Call `detect_regime_blindness()` at midnight, update weights |
| R-13 | TREND_FOLLOW not blocked in UNKNOWN regime | `strategies.py` / `cryptocom_bot.py` | Backlog #1 — add regime guard in strategy compatibility |
| R-14 | Execution analytics dashboard endpoint missing | `server.py` | Add `GET /api/execution-analytics` |
| R-15 | `review_queue` TOCTOU in `promote()` | `review_queue.py:128` | Add file lock around read+write |
| R-16 | Replay journal rotation not atomic | `replay_journal.py:177` | Rename via `os.replace()` |

## Low / Deferred

| # | Risk | Notes |
|---|------|-------|
| R-17 | No Grafana dashboard provisioning | Low urgency in paper mode |
| R-18 | `qwen_compressor.py` empty lesson not flagged | One-liner fix |
| R-19 | `claude_analyst.py` hardcoded model ID | Easy cosmetic fix |
| R-20 | No canary/shadow deployment capability | Architecture work |
| R-21 | `review_queue` mutable dict return | Defensive copy — low risk |

## Readiness Assessment

| Gate | Before Phase 3 | After Phase 3 |
|------|---------------|--------------|
| Extended Paper Trading | ✅ PASS | ✅ PASS |
| Supervised Live (small size) | ⚠️ CLOSE | ✅ UNBLOCKED — drift detection + continuous recon + halt release all complete |
| Limited Autonomous Live | ❌ Blocked | ⚠️ CLOSE — needs R-1 (WS drift), R-2 (event emission), R-4 (shadow wire) |
| Full Production | ❌ Far | ❌ Blocked — R-5, R-7, R-11, Grafana, CI push, canary |
