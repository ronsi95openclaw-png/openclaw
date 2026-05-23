# Audit Report — Grafana Phase 7 Dashboard Panels (Phase 7)
**Date:** 2026-05-23
**File:** `deployment/grafana/openclaw_dashboard.json` (extended Phase 6 → Phase 7)
**Status:** IMPLEMENTED · JSON VALID · 22 TOTAL PANELS

## Summary
Grafana dashboard extended with a "Phase 7 — Hardening Metrics" row containing 8 new
panels (panel IDs 100–107) covering balance divergence monitoring, replay equivalence
tracking, deployment approval status, rollback ladder visualization, and integrity
drift trend analysis.

## New Row: "Phase 7 — Hardening Metrics" (id=99)

## 8 New Panels

### Panel 100 — Balance Divergence (gauge)
- Metric: `openclaw_balance_divergence_pct`
- Thresholds: 0–2% green, 2–5% yellow, 5–10% red
- Purpose: Tracks exchange vs capital engine equity divergence in real-time

### Panel 101 — Balance EWMA Drift (timeseries)
- Metric: `openclaw_balance_ewma_divergence`
- Purpose: Slow-drift detection via EWMA; rising trend = systematic divergence

### Panel 102 — Replay Divergence Counter (stat)
- Metric: `openclaw_replay_divergence_total`
- Purpose: Count of replay verifier divergences since process start
- Alert: > 0 = investigation required

### Panel 103 — Deployment Approval Status (text)
- Metric: `openclaw_canary_phase`
- Purpose: Shows current orchestrator phase and pending approval requirements

### Panel 104 — Replay Equivalence Score (gauge)
- Metric: `openclaw_replay_equivalence_score`
- Thresholds: 1.0 green (fully equivalent), < 1.0 yellow/red
- Purpose: Three-path replay agreement [0, 1]

### Panel 105 — Rollback Trigger Ladder (table)
- Metrics: `openclaw_rollback_trigger_total` (by type label)
- Types: SURVIVABILITY, LATENCY, DRIFT, RECONCILIATION
- Purpose: Shows which triggers are firing and how frequently

### Panel 106 — Live Survivability Trend (timeseries)
- Metric: `openclaw_survivability_score`
- Thresholds: ≥85 green, 40–85 yellow, <40 red (rollback zone)
- Purpose: Continuous survivability monitoring

### Panel 107 — Integrity Drift Trend (timeseries)
- Metric: `openclaw_integrity_drift_score`
- Purpose: DriftEngine composite drift score over time; rising = strategy degradation

## Panel Count
- Phase 6: 13 panels
- Phase 7 added: 8 panels (+ 1 row separator)
- Total: 22 panels

## JSON Validity
Grafana JSON validated. All panel IDs unique. Row collapse supported.
