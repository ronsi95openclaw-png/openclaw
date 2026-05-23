# Audit Report — Remaining Risks (Phase 9)
**Date:** 2026-05-23
**Status:** ADVISORY — for operator review

## Risk Classification (Post Phase 9)

| ID | Risk | Severity | Likelihood | Status |
|----|------|----------|------------|--------|
| R-02 | Telegram CI staging test requires real TELEGRAM_BOT_TOKEN | LOW | MEDIUM | Validator ready; operator adds secret |
| R-03 | Multi-leg SL+TP simulation uses conservative spread amplification | LOW | LOW | Configurable via MultiLegSimulationConfig |
| R-05 | Orchestrator health defaults conservative when subsystems unavailable | MEDIUM | MEDIUM | Dashboard shows health breakdown in deployment panel |
| R-06 | LatencyProfiler log rotation not implemented | LOW | MEDIUM | Add max_lines rotation before long-duration live run |
| R-07 | ChaosRuntime RECONCILIATION_STORM silently skips when ReconciliationEngine unavailable | LOW | LOW | Acceptable graceful degradation |
| R-09 | WS fault injector REORDERING buffer not configurable | LOW | LOW | Sufficient for single-stream architecture |
| R-10 | Terraform provider is DigitalOcean-specific | LOW | LOW | Document migration before AWS/GCP |
| R-11 | ReplayVerifier confidence lower with 1–2 paths available | LOW | LOW | Two-path still detects gross divergence |
| R-12 | LeaderElection quorum_health_score is 0.75 in single_node_mode | LOW | LOW | By design; document non-HA single-node |
| R-13 | Fencing token persistence uses JSON without WAL | LOW | LOW | Counter monotonic within session |
| R-14 | Backtest baseline is synthetic | MEDIUM | CERTAIN | Labeled "synthetic": true; replace with real data before live |
| R-15 | BalanceFeedDaemon interval default 30s may not match rate limits | LOW | LOW | Configurable |
| R-16 | Dashboard audit JSONL has no size rotation | LOW | LOW | Document max growth rate; add rotation before 30-day live run |
| R-17 | /ws/v2 typed WebSocket hub not implemented | LOW | LOW | Existing /ws serves all current needs; deferred |

## Items Resolved in Phase 9

- ~~Operational visibility gap~~ → RESOLVED: 9-section dashboard with 23 API endpoints
- ~~No privileged action audit trail~~ → RESOLVED: DashboardAuditEvent JSONL for all POST actions
- ~~Advance-phase not protected against Phase4→STABLE~~ → RESOLVED: Hard 403 guard in router
- ~~Chaos inject not protected in live mode~~ → RESOLVED: DEMO_MODE gate for destructive events
- ~~No real-time telemetry push~~ → RESOLVED: run_telemetry_loop publishes 5 channels via EventBus

## Remaining Backlog (not blocking supervised live deployment)
1. Replace synthetic backtest baseline with real historical data
2. Add Telegram bot token to CI staging secrets for live E2E validation
3. LatencyProfiler log rotation (max_lines enforcement)
4. Dashboard audit JSONL rotation (30-day retention)
5. /ws/v2 typed multi-channel WebSocket hub (TelemetryHub class)
6. LeaderElection quorum health from real peer count (multi-node)
7. Fencing token WAL for crash-safe persistence
8. Dynamic scan interval (slow in ranging, fast in trending)
9. Auto-apply Claude Opus weight adjustments at midnight

## Supervised Live Deployment Status
**READY TO PROCEED** — all technical prerequisites met across Phases 1–9.

Operator must still:
1. Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env
2. Run `python3 scripts/run_canary_shadow.py --force-paper` for Phases 1–3
3. Monitor `data/balance_audit.jsonl` for divergence events
4. Monitor `data/dashboard_audit.jsonl` for unauthorized advance-phase attempts
5. Keep DEMO_MODE=true until Phase 4 Ed25519 approval obtained
