# Audit Report — IntegrityMonitor (Phase 5)
**Date:** 2026-05-23  
**Subsystem:** `runtime/integrity_monitor.py`  
**Status:** IMPLEMENTED · TESTED · PASSING

## Summary
Scheduled background monitor running 7 isolated integrity checks against all Phase 4/5 subsystems. Each check is independently bounded, fail-closed, and produces structured findings. CRITICAL findings halt entry gate (via halt marker) when configured.

## Implementation Details

### 7 Isolated Checks
| Check | Subsystem | Scan Window |
|-------|-----------|-------------|
| `event_store_integrity` | EventStore SHA-256 checksums | Last N events |
| `snapshot_integrity` | EventSnapshotEngine on-disk files | Latest K snapshots |
| `seq_monotonicity` | EventStore sequence continuity | Last N events |
| `replay_determinism` | EventReplayEngine double-run comparison | Subset of events |
| `reconciliation_consistency` | ReconciliationEngine position delta | Current positions |
| `governance_persistence` | StrategyGovernanceEngine persistence | Config file |
| `event_store_growth` | EventStore growth rate anomaly | Rolling window |

### Incremental Scanning
- `last_scanned_seq` tracks progress between scans — avoids rescanning old events
- `event_scan_window` parameter caps how many events are checked per scan cycle (default 1 000)

### CRITICAL Handling (fail-closed)
1. Single Telegram alert per scan (deduplicated — one alert regardless of how many CRITICAL findings)
2. Prometheus counter increment: `openclaw_integrity_critical_total`
3. Best-effort EventStore append of `INTEGRITY_ALERT` event
4. If `halt_on_critical=True`: write `data/INTEGRITY_HALT` marker (CryptoComBot checks this at gate entry)
5. Persist to `data/integrity_incidents.jsonl` with full structured record

### Severity Levels
- `INFO` — informational, no action
- `WARNING` — degraded condition, logged
- `CRITICAL` — halt-eligible, triggers full escalation pipeline

### Singleton
- `get_monitor()` module-level singleton
- Bot wires monitor in `_init_integrity_monitor()` at startup

## Test Coverage (test_phase5_soak.py)
| Test | Result |
|------|--------|
| `test_integrity_monitor_scan` | PASSED |
| `test_integrity_monitor_lifecycle` | PASSED |

## Safety Properties
- Each check runs inside its own `try/except` — one failing check cannot block others
- Fail-closed: exception in a check produces a WARNING finding rather than silently passing
- Never mutates EventStore contents (only appends metadata events)
- halt_on_critical=False by default in test mode; True recommended in production
