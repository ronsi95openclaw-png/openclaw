# Replay Consistency Report
**File:** `runtime/replay_validator.py`
**Date:** 2026-05-22

## Summary
The replay journal (`data/replay_journal.jsonl`) was append-only but never validated. A truncation, clock skew, missed event, or concurrent write could produce an inconsistent audit trail without any detection.

## What Was Built

### `ReplayValidator`
Deterministic validation engine — no OpenClaw imports, pure data analysis.

**8 checks implemented:**

| Check | Severity | Description |
|-------|----------|-------------|
| JSON parse | ERROR | Any unparseable line flagged immediately |
| Timestamp ordering | WARNING | Events detected out of time-order |
| Signal/intent pairing | WARNING | `signal_generated` with no `intent_approved/rejected` within 5s |
| Capital state machine | ERROR | Illegal or discontinuous transitions |
| Duplicate detection | WARNING | Same `event_type + trace_id` within 1 second |
| Open intents | INFO | `intent_approved` with no subsequent position open/close |
| Unknown states | ERROR | Capital state events with unrecognised state names |
| Checksum | INFO | SHA-256 of all events sorted by `ts` |

### Legal capital transitions
```
SAFE           → SAFE, DEFENSIVE, CRITICAL, EMERGENCY_HALT
DEFENSIVE      → DEFENSIVE, SAFE, CRITICAL, EMERGENCY_HALT
CRITICAL       → CRITICAL, EMERGENCY_HALT
EMERGENCY_HALT → EMERGENCY_HALT
```
`EMERGENCY_HALT → SAFE` is **illegal** and triggers ERROR.

### API
```python
ReplayValidator.validate_file(path) -> ReplayValidationReport
# or instance method:
ReplayValidator().validate(journal_path) -> ReplayValidationReport
```

### `ReplayValidationReport` fields
`passed`, `total_events`, `issues`, `open_intents_count`, `capital_transitions`, `duplicate_count`, `checksum`, `validated_at`

`passed = True` only when zero ERROR-severity issues.

## Chaos Test Coverage
- Empty journal → passes
- Missing file → fails gracefully with informative issue
- Corrupt JSON line → ERROR flagged
- Time-backwards events → WARNING flagged
- `EMERGENCY_HALT → SAFE` → ERROR flagged

## Usage
```python
from runtime.replay_validator import ReplayValidator
report = ReplayValidator.validate_file("data/replay_journal.jsonl")
if not report.passed:
    for issue in report.issues:
        print(issue.severity, issue.description)
```

## Integration Recommendation
Run `ReplayValidator.validate_file()` as part of:
1. Startup health check (alongside reconciliation)
2. Nightly Claude Opus analysis pipeline
3. CI/CD gate before deployment
