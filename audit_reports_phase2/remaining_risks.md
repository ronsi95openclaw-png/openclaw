# Remaining Risks — After Phase 2
**Date:** 2026-05-22

## Critical (block live deployment)

| # | Risk | File | Mitigation Path |
|---|------|------|-----------------|
| R-1 | CORS hardcoded to localhost — remote dashboard blocked | `server.py:48` | Add `CORS_ORIGINS` env var, read on startup |
| R-2 | Manual EMERGENCY_HALT recovery requires code-level reset | `emergency_controls.py` | Build governance UI endpoint: `POST /admin/halt/release` |
| R-3 | Reconciliation runs at startup only — live drift undetected | `reconciliation.py` | Schedule periodic reconciliation in scan loop (every 10 minutes) |

## High (should fix before extended live)

| # | Risk | File | Mitigation Path |
|---|------|------|-----------------|
| R-4 | Exchange quantity precision not instrument-specific | `exchange.py` | Add `_QTY_PRECISION` dict, round to correct decimals per instrument |
| R-5 | Position schema validation too lenient (no range checks) | `cryptocom_bot.py:95` | Add Pydantic model for position validation on load |
| R-6 | `governance._is_active()` no lock (CC-11) | `operator_controls.py` | Wrap in `threading.Lock()` |
| R-7 | ShadowOptimizationEngine not wired into `_auto_apply_opus_weights()` | `cryptocom_bot.py` | Route Opus weight_adjustments through `apply_candidate()` before live write |
| R-8 | No periodic replay journal validation | `replay_validator.py` | Run `ReplayValidator.validate_file()` nightly in Claude Analyst pipeline |

## Medium (improve before full production)

| # | Risk | File | Mitigation Path |
|---|------|------|-----------------|
| R-9 | Prometheus metrics not wired to Google Sheets reporter | `metrics.py`, `reporting/` | Add GSheets row for daily metric summary |
| R-10 | No Grafana dashboard config for OpenClaw metrics | `infra/grafana/` | Add provisioning JSON for 5 core panels |
| R-11 | `qwen_compressor.py` empty lesson not flagged | `qwen_compressor.py:65` | Add `if not lesson.strip(): logger.warning(...)` |
| R-12 | Google Sheets queue overflow no metric | `google_sheets.py:294` | Increment `metrics.record_exchange_error("sheets_overflow")` |
| R-13 | `review_queue` TOCTOU in `promote()` | `review_queue.py:128` | Add file lock around read+write |
| R-14 | Replay journal rotation not atomic | `replay_journal.py:177` | Rename via `os.replace()` (atomic) |
| R-15 | `permissions.py` base64 "encryption" | `permissions.py:126` | Replace with Fernet (same pattern as `secrets.py`) |

## Low / Deferred

| # | Risk | Notes |
|---|------|-------|
| R-16 | `qwen_compressor.py` empty lesson | LOW impact — just a missing log |
| R-17 | `claude_analyst.py` hardcoded model ID | Easy fix, no runtime impact |
| R-18 | CORS hardcoded (paper-only risk) | CRITICAL for live remote access |
| R-19 | No backtested strategy validation vs live conditions | Architecture work |
| R-20 | `review_queue` mutable dict return | Easy defensive copy |

## Readiness Assessment

| Gate | Before Phase 2 | After Phase 2 |
|------|---------------|--------------|
| Extended Paper Trading | ✅ PASS | ✅ PASS |
| Limited Live (small size) | ⚠️ CLOSE | ⚠️ CLOSE — R-1 (CORS) and R-2 (halt recovery) still block |
| Full Production | ❌ Far | ❌ Closer — needs R-1, R-2, R-4, R-7, Docker CI push |
