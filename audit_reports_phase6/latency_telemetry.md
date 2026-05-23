# Audit Report — Latency Telemetry (Phase 6)
**Date:** 2026-05-23
**Files:** `runtime/latency_profiler.py`, `runtime/execution_telemetry.py`
**Status:** IMPLEMENTED · TESTED · 12/12 PASSING

## Summary
Two-layer execution telemetry stack: `LatencyProfiler` captures per-operation
p50/p95/p99 with EWMA and anomaly detection; `ExecutionTelemetryCollector`
aggregates all signals into a unified snapshot driving rollback triggers.

---

## LatencyProfiler

### 8 OperationCategories with Baselines (p50 ms)
| Category | Baseline p50 |
|----------|-------------|
| WEBSOCKET | 5.0 |
| REST_API | 50.0 |
| ORDER_ACKNOWLEDGEMENT | 100.0 |
| FILL_CONFIRMATION | 200.0 |
| RECONCILIATION | 500.0 |
| SNAPSHOT | 200.0 |
| EVENT_PERSISTENCE | 20.0 |
| LOCK_ACQUISITION | 5.0 |

### Metrics
- **p50/p95/p99**: linear interpolation on sorted sample buffer
- **EWMA** (alpha=0.1): running estimate, seeded from first sample
- **anomaly_detected**: p99 > 3× p50 (latency bimodality indicator)
- **exchange_degradation_score**: `min(1.0, p99 / (baseline × 5))` — 0=healthy
- **timing_drift**: current EWMA window vs historical EWMA window ratio
- **Prometheus export**: text format, `openclaw_latency_ms{category,operation,quantile}`

### Context Manager
```python
with profiler.measure(OperationCategory.REST_API, "fetch_ticker"):
    result = exchange.fetch_ticker(symbol)
# → automatically records elapsed_ms
```

---

## ExecutionTelemetryCollector

### 8 record_*() methods
Delegates to LatencyProfiler for each operation category. Also maintains
rolling buffer of 1000 fill samples for slippage and fill-rate aggregation.

### ExecutionTelemetry snapshot
- `ws_latency_p99_ms`, `rest_latency_p99_ms`, `order_ack_latency_p99_ms`, `fill_latency_p99_ms`
- `avg_slippage_bps`, `avg_fill_rate`
- `exchange_degradation_score` (from LatencyProfiler)
- `execution_timing_drift`
- `survivability_score` (lazy SurvivabilityEngine, default 50.0)
- `telemetry_health`: HEALTHY / DEGRADED / CRITICAL

### Rollback Trigger Conditions
| Trigger | Condition |
|---------|-----------|
| LATENCY_EXPLOSION | ws_latency_p99 > 1000 ms |
| EXCHANGE_DEGRADED | exchange_degradation_score > 0.8 |
| FILL_RATE_COLLAPSED | avg_fill_rate < 0.5 |
| SURVIVABILITY_CRITICAL | survivability_score < 40 |

## Test Results (12/12)
| Test | Result |
|------|--------|
| Record 100 samples → p50 < p99 | PASSED |
| EWMA converges to fed value | PASSED |
| Anomaly detection (5ms + 100ms mix) | PASSED |
| Context manager records elapsed | PASSED |
| Low-latency samples → HEALTHY | PASSED |
| 2000ms p99 → LATENCY_EXPLOSION trigger | PASSED |
| [+ 6 rollback automation tests] | PASSED |

## Operational Risk Eliminated
No latency visibility existed before Phase 6. Exchange degradation was
detectable only through failed orders, not through pre-failure latency drift.
Now: p99 latency trending toward threshold triggers automated rollback before
orders start failing.
