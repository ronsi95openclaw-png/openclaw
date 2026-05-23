# Grafana Dashboard & Alert Rules — Phase 4
**Files:** `deployment/grafana/openclaw_dashboard.json`, `deployment/alerts/openclaw_alerts.yml`
**Date:** 2026-05-23

## Grafana Dashboard

### Panel Layout (13 panels)

**Row 1 — Health Summary (stat panels):**
| Panel | Metric | Thresholds |
|-------|--------|-----------|
| Capital State | `openclaw_capital_state` | 0=SAFE/green, 1=DEFENSIVE/yellow, 2=CRITICAL/orange, 3=HALT/red |
| Open Positions | `openclaw_open_positions_total` | — |
| Total PnL (USD) | `openclaw_total_pnl_usd` | <0=red, ≥0=yellow, ≥10=green |
| Reconciliation Status | `openclaw_reconciliation_passed` | 0=FAILED/red, 1=PASSED/green |
| Active Drift Events | `openclaw_drift_events_active` | 0=green, ≥1=yellow, ≥3=red |
| WS Health Score | `openclaw_ws_health_score` | <0.4=red, <0.7=yellow, ≥0.7=green |

**Row 2 — Time Series:**
| Panel | Metrics |
|-------|---------|
| PnL Over Time | `openclaw_total_pnl_usd` (line) |
| Scan Latency (ms) | P95+P50 of `openclaw_scan_duration_seconds` histogram |

**Row 3 — Operational:**
| Panel | Metrics |
|-------|---------|
| Reconciliation Results | `openclaw_reconciliation_runs_total`, `openclaw_reconciliation_failures_total` |
| EventStore Throughput | `rate(openclaw_event_store_appends_total[1m])` |

**Row 4 — Process Health:**
| Panel | Metrics |
|-------|---------|
| Memory Usage (MB) | `process_resident_memory_bytes / 1024 / 1024` |
| Active Threads | `openclaw_thread_count` |
| Critical Incidents | `openclaw_critical_incidents_total` |

**Dashboard settings:** 30s auto-refresh, UTC timezone, 6h default time range.

## Alert Rules

### CRITICAL (immediate action required)
| Alert | Condition | For |
|-------|-----------|-----|
| EmergencyHaltActive | `openclaw_capital_state == 3` | 0m (instant) |
| ReconciliationFailed | `openclaw_reconciliation_passed == 0` | 5m |
| CriticalDriftDetected | `openclaw_drift_events_active >= 1` | 2m |
| WebSocketHealthCritical | `openclaw_ws_health_score < 0.4` | 1m |

### HIGH (urgent but not immediate halt)
| Alert | Condition | For |
|-------|-----------|-----|
| CapitalStateDefensive | `openclaw_capital_state >= 1` | 10m |
| DrawdownHigh | `openclaw_drawdown_pct > 15` | 5m |
| ScanLatencyHigh | `P95(scan_duration) > 25s` | 5m |
| ReconciliationConsecutiveFails | `consecutive_fails >= 3` | 0m |

### MEDIUM (monitor and investigate)
| Alert | Condition | For |
|-------|-----------|-----|
| MemoryGrowthHigh | RSS > 500MB | 15m |
| EventStoreNotGrowing | no seq change in 30m | 30m |
| NoTradesInPeriod | no trades in 6h | 6h |

## Deployment Notes
To provision in Docker:
```yaml
# docker-compose.yml excerpt
grafana:
  volumes:
    - ./deployment/grafana:/etc/grafana/provisioning/dashboards
    - ./deployment/alerts/openclaw_alerts.yml:/etc/prometheus/rules/openclaw.yml
```

The dashboard JSON includes `__inputs` for a Prometheus datasource named `DS_PROMETHEUS`. Update the datasource connection string to match your Prometheus endpoint.
