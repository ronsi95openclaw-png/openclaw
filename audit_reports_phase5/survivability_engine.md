# Audit Report — SurvivabilityEngine (Phase 5)
**Date:** 2026-05-23  
**Subsystem:** `runtime/survivability.py`  
**Status:** IMPLEMENTED · TESTED · PASSING

## Summary
Composite health scoring engine that aggregates 8 weighted subsystem checks into a single 0–100 score with classification and trend detection. Reads only cached state — never makes live API calls. Score informs canary phase evaluation, dashboard display, and survivability API endpoint.

## Implementation Details

### 8 Weighted Subsystems
| Subsystem | Weight | Checks |
|-----------|--------|--------|
| `capital_preservation` | 20 | CapitalEngine state not HALT |
| `event_store` | 15 | EventStore accessible, recent events written |
| `snapshot_health` | 12 | SnapshotDaemon no consecutive failures |
| `ws_guardian` | 12 | WSGuardian health score ≥ 0.4 |
| `integrity_monitor` | 10 | No unresolved CRITICAL findings |
| `strategy_governance` | 10 | GovernanceEngine accessible, no quarantined strategies |
| `execution_optimizer` | 11 | Policy loaded, recent analytics updates |
| `reconciliation` | 10 | Last reconciliation within 5 minutes |

*Weights sum to 100.*

### SurvivabilityClassification
| Score | Classification |
|-------|---------------|
| 80–100 | STABLE |
| 60–79 | DEGRADED |
| 40–59 | CRITICAL |
| 0–39 | UNSAFE |

### `deployment_ready` Flag
- True only when: score ≥ 80 AND capital state is not HALT AND no CRITICAL integrity findings

### Trend Detection
- Maintains rolling history of last N scores (configurable, default 20)
- Compares average of last 5 vs previous 5
- IMPROVING: avg diff > +2.0, DEGRADING: avg diff < -2.0, else STABLE
- Returns "UNKNOWN" if fewer than 5 scores recorded

### No Live API Calls
- All subsystem checks read from module singletons or local file state
- If a subsystem is unavailable (import error), that subsystem contributes 0 to score
- Score degrades gracefully as subsystems become unavailable

## Test Coverage (test_phase5_soak.py)
| Test | Result |
|------|--------|
| `test_survivability_engine_score` | PASSED |
| `test_survivability_trend` | PASSED |

## Dashboard Integration
- `/api/survivability` endpoint returns full `SurvivabilityReport`
- `/api/diagnostics` includes `survivability_score` field for quick polling
