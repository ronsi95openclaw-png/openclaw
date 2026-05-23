# Audit Report — Canary Shadow Phase Execution (Phase 8)
**Date:** 2026-05-23
**File:** `scripts/run_canary_shadow.py`
**Status:** IMPLEMENTED · TESTED · 8/8 PASSING

## Summary
`run_canary_shadow.py` advances `DeploymentOrchestrator` through Canary Phases 1→2→3
in paper-shadow mode (DEMO_MODE=true enforced). Phase 4 is deliberately excluded —
it requires a human Ed25519 cryptographic approval. Health gates are checked before
each advancement; `--force-paper` allows override in paper mode with explicit warning.

## Phase Execution Flow
```
PENDING
  → [health check ≥ 60.0] → CANARY_PHASE_1
  → [health check ≥ 70.0] → CANARY_PHASE_2
  → [health check ≥ 80.0] → CANARY_PHASE_3
  ↓
  STOP — Phase 4 requires human cryptographic approval (Ed25519 + quorum)
```

## Health Gate Thresholds
| Phase | Composite Score Required |
|-------|------------------------|
| 1 | ≥ 60.0 |
| 2 | ≥ 70.0 |
| 3 | ≥ 80.0 |
| 4 | NOT executed by this script |

## DEMO_MODE Enforcement
- Script reads `os.getenv("DEMO_MODE", "true")`
- If `DEMO_MODE=false`: prints error and `sys.exit(1)`
- `run_shadow_phases(demo_mode=False)` raises `RuntimeError` immediately
- No override possible in live mode — fail-closed

## --force-paper Override
```bash
python3 scripts/run_canary_shadow.py --force-paper
```
When health is below threshold AND `--force-paper` AND DEMO_MODE=true:
- Script prints explicit WARNING showing actual vs threshold score
- Proceeds anyway (paper shadow tolerance)
- Override is logged; never silent

Only valid in DEMO_MODE. Has no effect in live mode (script refuses to run).

## Phase 4 Guard
```python
if current_record.state == DeploymentState.CANARY_PHASE_4:
    _print("Phase 4 requires human approval. Stopping.")
    break
```
`advance_phase()` is never called with Phase 4 as source. The script stops at
Phase 3 and prints a reminder about the Ed25519 approval requirement.

## Usage
```bash
# New deployment (auto-creates):
python3 scripts/run_canary_shadow.py --force-paper

# Existing deployment:
python3 scripts/run_canary_shadow.py --deployment-id abc-123 --operator-id "ops@openclaw"

# Health check only (no advancement):
python3 scripts/run_canary_shadow.py --check-only

# JSON output:
python3 scripts/run_canary_shadow.py --output-json
```

## Expected Output (healthy system)
```
[CANARY SHADOW] Starting paper-shadow canary run
  deployment_id: abc-123
  operator_id: paper-shadow-operator
  demo_mode: true
[PHASE 1] Health check: composite=65.2 (threshold=60.0) ✓
           survivability=45.0  integrity_ok=True  ws_health=0.50
[PHASE 1] Advancing...
[PHASE 1] → CANARY_PHASE_1 ✓
[PHASE 2] ...
[PHASE 3] → CANARY_PHASE_3 ✓
[CANARY SHADOW] Phases 1–3 complete.
  Final state: CANARY_PHASE_3
  Phase 4 requires a human operator with Ed25519 cryptographic approval.
```

## Programmatic API (for testing)
```python
from run_canary_shadow import run_shadow_phases
result = run_shadow_phases(
    orchestrator=orch,
    deployment_id="dep-id",
    operator_id="ops",
    force_paper=True,
    demo_mode=True,
)
# result: {"deployment_id", "phases", "final_state"}
```

## Test Results (8/8)
| Test | Result |
|------|--------|
| demo_mode_false_raises RuntimeError | PASSED |
| phases_1_2_3_advance_with_force_paper | PASSED |
| blocked_when_below_threshold_no_force | PASSED |
| phase_4_never_advanced | PASSED |
| failed_state_stops_run | PASSED |
| output_json_format keys present | PASSED |
| health_snapshot_captured per phase | PASSED |
| operator_id_passed_through to every call | PASSED |
