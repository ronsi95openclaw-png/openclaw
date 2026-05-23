# Operational Diagnostics — Phase 3
**Files:** `runtime/diagnostics.py`, `dashboard/api/server.py` (GET /api/diagnostics)
**Date:** 2026-05-23

## Problem
No unified health view. Operators had to manually inspect logs, reconciliation.jsonl, and capital_state.json to understand system status. No single API endpoint for subsystem health.

## What Was Built

### `DiagnosticsEngine`
8 independent subsystem checks, each isolated in try/except. Any exception → `SubsystemStatus.UNREACHABLE`.

**Subsystems checked:**
| Subsystem | Check Method | What it probes |
|-----------|-------------|----------------|
| Exchange | `check_exchange_connectivity()` | `fetch_ticker("BTC_USDT")`, measures latency |
| Capital Engine | `check_capital_engine()` | Import + `get_state()`, validates non-None |
| Reconciliation | `check_reconciliation()` | Last line of `data/reconciliation.jsonl`, age check |
| Replay Journal | `check_replay_journal()` | File exists, line count, last event age |
| Event Store | `check_event_store()` | `get_latest_seq()`, file exists |
| Execution Analytics | `check_execution_analytics()` | Instantiate engine, check analytics file |
| Drift Detector | `check_drift_detector()` | Last entry age in `data/drift_events.jsonl` |
| Prometheus | `check_prometheus()` | TCP probe to localhost:9090, 2s timeout |

**System metrics (`_get_system_metrics`):**
- `memory_mb` — via `psutil.Process().memory_info()` or `/proc/{pid}/status` fallback
- `thread_count` — `threading.active_count()`
- `open_fds` — via `psutil` or `/proc/{pid}/fd` directory count

**Critical incident collection:** Merges last 5 CRITICAL entries from `data/reconciliation.jsonl` and `governance/logs/emergency.jsonl`.

**`overall_status`:** Worst severity across all 8 subsystems.

### `GET /api/diagnostics` endpoint
```
GET /api/diagnostics
Headers: X-Dashboard-Token: <token>

Response:
{
  "generated_at": "2026-05-23T...",
  "overall_status": "HEALTHY",
  "capital_state": "SAFE",
  "open_positions": 0,
  "reconciliation_status": "PASSED",
  "last_reconciliation_ts": "...",
  "drift_events_active": 0,
  "websocket_connections": 2,
  "replay_journal_events": 1247,
  "event_store_last_seq": 523,
  "memory_mb": 148.3,
  "thread_count": 12,
  "open_fds": 34,
  "uptime_seconds": 3601.2,
  "recent_critical_incidents": [],
  "subsystems": {
    "exchange": {"status": "HEALTHY", "latency_ms": 124, ...},
    ...
  }
}
```

Falls back to a minimal response if the diagnostics module is unavailable (ImportError).

### Process singleton
`get_diagnostics_engine()` returns a single `DiagnosticsEngine` instance per process via double-checked locking. Avoids repeated connection probes from concurrent requests.

## Remaining Gaps
- `check_exchange_connectivity()` makes a live API call — should be rate-limited to avoid hammering exchange during health check storms
- Reconciliation scheduler status not yet included in `DiagnosticsReport`
- DriftDetector active events not counted from the engine directly (reads file)
